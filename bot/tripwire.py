"""EDGAR intraday tripwire: catch the dilution rug WHILE holding, in ~a minute.

The no-stop penny ladder's single worst tail is a mid-session offering: the
company files, insiders sell into the spike, the price collapses, and a
price-based rule would ride it down. This watches the SEC's whole-market
new-filings feed (one unfiltered Atom call covers EVERYTHING filed in the
last minutes; 8-K item numbers arrive inside the feed's summary) and fires
on filings that historically mark the rug being pulled.

Trigger classes for a HELD symbol:
  FLATTEN-NOW: 424B1/424B4/424B5 (priced offerings/takedowns), EFFECT
               (registration effective - restricted shares unlock),
               8-K with Item 3.02 (unregistered sales) or 5.03 (reverse
               split/charter change)
  ALERT-ONLY:  424B3 (ATM drip), fresh S-1/S-3 (machinery being built)

This is an INFORMATION-based exit, not a stop loss - fully compatible with
the user's no-stop directive. Feed-down degrades to nothing (fail-open);
seen-filing dedupe persists in tripwire_seen.json. Poll cost: one request
per ~45s against SEC's 10-req/SECOND cap.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger("bot.tripwire")

FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
        "&type=&company=&dateb=&owner=include&count=100&output=atom")
UA = {"User-Agent": "ibkr-paper-research-bot contact realdoomsalt@gmail.com"}
SEEN_FILE = Path("tripwire_seen.json")
ATOM = {"a": "http://www.w3.org/2005/Atom"}

FLATTEN_FORMS = ("424B1", "424B4", "424B5", "EFFECT")
ALERT_FORMS = ("424B3", "S-1", "S-3")
FLATTEN_8K_ITEMS = ("3.02", "5.03")

_cache: dict = {"at": 0.0, "entries": []}


def _load_seen(path: Path) -> dict:
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
        today = dt.date.today().isoformat()
        return seen if seen.get("date") == today else {"date": today, "ids": []}
    except Exception:
        return {"date": dt.date.today().isoformat(), "ids": []}


def _save_seen(path: Path, seen: dict) -> None:
    try:
        seen["ids"] = seen["ids"][-2000:]
        path.write_text(json.dumps(seen), encoding="utf-8")
    except Exception:
        pass


def _fetch_entries() -> list[dict]:
    """Latest ~100 filings market-wide, cached 45s. [] on any failure."""
    now = time.time()
    if now - _cache["at"] < 45:
        return _cache["entries"]
    _cache["at"] = now  # stamp first: an outage costs one timeout per window
    try:
        req = urllib.request.Request(FEED, headers=UA)
        root = ET.fromstring(urllib.request.urlopen(req, timeout=10).read())
        entries = []
        for e in root.findall("a:entry", ATOM):
            title = e.findtext("a:title", "", ATOM)
            entries.append({
                "id": e.findtext("a:id", "", ATOM),
                "title": title,
                # "424B5 - COMPANY INC (0001234567) (Filer)"
                "form": title.split(" - ")[0].strip().upper(),
                "cik": (re.search(r"\((\d{10})\)", title) or [None, ""])[1],
                "summary": e.findtext("a:summary", "", ATOM) or "",
            })
        _cache["entries"] = entries
    except Exception:
        log.debug("tripwire feed unavailable", exc_info=True)
    return _cache["entries"]


def check(cik_to_symbol: dict[str, str],
          seen_name: str = "tripwire_seen.json") -> list[dict]:
    """New filings matching our CIKs since last check.

    Returns [{symbol, form, action: 'flatten'|'alert', detail}]. Each filing
    fires exactly once PER CONSUMER: every caller must pass its own
    seen_name, or one consumer's dedupe would swallow another's flatten
    signal (the penny engine and the watchdog both watch this feed).
    """
    if not cik_to_symbol:
        return []
    hits: list[dict] = []
    seen_path = Path(seen_name)
    seen = _load_seen(seen_path)
    known = set(seen["ids"])
    for e in _fetch_entries():
        if not e["cik"] or e["cik"] not in cik_to_symbol or e["id"] in known:
            continue
        seen["ids"].append(e["id"])
        sym = cik_to_symbol[e["cik"]]
        form, action = e["form"], ""
        if any(form.startswith(f) for f in FLATTEN_FORMS):
            action = "flatten"
        elif form.startswith("8-K"):
            items = re.findall(r"Item\s+(\d+\.\d+)", e["summary"] or e["title"])
            if any(i in FLATTEN_8K_ITEMS for i in items):
                action, form = "flatten", f"8-K item {','.join(items)}"
            else:
                action = "alert"
        elif any(form.startswith(f) for f in ALERT_FORMS):
            action = "alert"
        if action:
            hits.append({"symbol": sym, "form": form, "action": action,
                         "detail": e["title"][:120]})
            log.warning("TRIPWIRE %s: %s filed %s -> %s",
                        sym, action.upper(), form, e["title"][:100])
    if hits or seen["ids"] != list(known):
        _save_seen(seen_path, seen)
    return hits


def cik_map(symbols: list[str]) -> dict[str, str]:
    """{10-digit CIK: symbol} using bot.edgar's cached lookup. Best effort."""
    out = {}
    try:
        from bot.edgar import cik_for

        for s in symbols:
            c = cik_for(s)
            if c:
                out[c] = s.upper()
    except Exception:
        log.debug("cik_map failed", exc_info=True)
    return out
