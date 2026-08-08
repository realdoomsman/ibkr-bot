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

# Exact form matches (prefix-matching wrongly caught 8-K12B, S-11, etc.).
# EFFECT is disseminated in an overnight batch, not intraday - it stays a
# best-effort catch here and is really covered at ENTRY by edgar.dilution_flags.
FLATTEN_FORMS = {"424B1", "424B4", "424B5", "EFFECT"}
ALERT_FORMS = {"424B3", "S-1", "S-3", "S-1/A", "S-3/A"}
EIGHTK_FORMS = {"8-K", "8-K/A"}
FLATTEN_8K_ITEMS = ("3.02", "5.03")

_cache: dict = {"at": 0.0, "entries": [], "pages": 0}


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


def _fetch_entries(pages: int = 1) -> list[dict]:
    """Newest filings market-wide, cached 45s. `pages` x 100 entries via the
    &start= param - the 100-entry window covers only ~7-20 min at RTH rates,
    so the 30-min watchdog sweep must page back further or it misses filings.
    [] on any failure (fail-open)."""
    now = time.time()
    if now - _cache["at"] < 45 and _cache["pages"] >= pages:
        return _cache["entries"]
    _cache["at"] = now  # stamp first: an outage costs one timeout per window
    try:
        entries = []
        for pg in range(pages):
            url = FEED + f"&start={pg * 100}"
            req = urllib.request.Request(url, headers=UA)
            root = ET.fromstring(urllib.request.urlopen(req, timeout=10).read())
            page_entries = root.findall("a:entry", ATOM)
            for e in page_entries:
                title = e.findtext("a:title", "", ATOM)
                entries.append({
                    "id": e.findtext("a:id", "", ATOM),
                    "title": title,
                    # "424B5 - COMPANY INC (0001234567) (Filer)"
                    "form": title.split(" - ")[0].strip().upper(),
                    "cik": (re.search(r"\((\d{10})\)", title) or [None, ""])[1],
                    "summary": e.findtext("a:summary", "", ATOM) or "",
                })
            if len(page_entries) < 100:
                break  # last page
        _cache["entries"], _cache["pages"] = entries, pages
    except Exception:
        log.debug("tripwire feed unavailable", exc_info=True)
    return _cache["entries"]


def classify(form: str, summary: str) -> tuple[str, str]:
    """(action, label) for a form. action in {'flatten','alert',''}."""
    form = form.upper()
    if form in FLATTEN_FORMS:
        return "flatten", form
    if form in EIGHTK_FORMS:
        items = re.findall(r"Item\s+(\d+\.\d+)", summary or "")
        if any(i in FLATTEN_8K_ITEMS for i in items):
            return "flatten", f"8-K item {','.join(i for i in items if i in FLATTEN_8K_ITEMS)}"
        return "", ""  # ordinary 8-K (PR/earnings) - not a dilution signal
    if form in ALERT_FORMS:
        return "alert", form
    return "", ""


def check(cik_to_symbol: dict[str, str],
          seen_name: str = "tripwire_seen.json",
          pages: int = 1) -> list[dict]:
    """New filings matching our CIKs since last check.

    Returns [{symbol, form, action, detail, id}]. IMPORTANT: 'alert' hits are
    marked seen here (losing one is harmless); 'flatten' hits are NOT marked
    seen - the caller must ack() them only after the exit is CONFIRMED, so a
    failed flatten re-fires instead of riding the rug (the exact tail this
    module exists to cut). Each consumer passes its own seen_name.
    """
    if not cik_to_symbol:
        return []
    hits: list[dict] = []
    seen_path = Path(seen_name)
    seen = _load_seen(seen_path)
    known = set(seen["ids"])
    changed = False
    for e in _fetch_entries(pages):
        if not e["cik"] or e["cik"] not in cik_to_symbol or e["id"] in known:
            continue
        action, label = classify(e["form"], e["summary"])
        if not action:
            continue
        sym = cik_to_symbol[e["cik"]]
        hit = {"symbol": sym, "form": label, "action": action,
               "detail": e["title"][:120], "id": e["id"]}
        hits.append(hit)
        log.warning("TRIPWIRE %s: %s filed %s -> %s", sym, action.upper(),
                    label, e["title"][:100])
        if action == "alert":  # flatten ids are ack'd only after a confirmed exit
            seen["ids"].append(e["id"])
            changed = True
    if changed:
        _save_seen(seen_path, seen)
    return hits


def ack(filing_id: str, seen_name: str) -> None:
    """Mark a flatten-class filing handled - call ONLY after the exit is
    confirmed flat, so an unconfirmed exit re-fires on the next check()."""
    if not filing_id:
        return
    seen_path = Path(seen_name)
    seen = _load_seen(seen_path)
    if filing_id not in seen["ids"]:
        seen["ids"].append(filing_id)
        _save_seen(seen_path, seen)


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
