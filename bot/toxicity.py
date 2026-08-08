"""Free exchange 'toxicity' feeds: the market's own broken-company registry.

Three keyless sources (all live-verified in the deep-research expedition):

1. nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt - the official symbol
   directory. Field 'Financial Status' marks companies the EXCHANGE certifies
   as broken: D=Deficient, E=Delinquent, Q=Bankrupt, G/H/J/K = combinations.
   A deficient company's only lifeline is issuing shares - it WILL sell into
   any spike. ~330 live names, heavily overlapping our spike pool.
2. Same file + otherlisted.txt: the authoritative ETF Y/N flag - replaces
   name-pattern guessing for the user's no-ETF rule.
3. nasdaqtrader.com RSS category 105 - every reverse-split announcement with
   the ticker in the title (reverse-split recency is the measured ~30%
   precursor of an imminent offering).

Refreshed once per day (run_rotate calls refresh() pre-market); consumers
read toxicity.json. Failures keep yesterday's file - stale beats blind,
and a missing file simply removes the filter (fail-open like the halt feed).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import urllib.request
from pathlib import Path

log = logging.getLogger("bot.toxicity")

TOX_FILE = Path("toxicity.json")
BAD_FSI = set("DEQGHJK")  # exchange-certified financial distress codes
SYMDIR = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHERDIR = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SPLIT_RSS = "https://www.nasdaqtrader.com/rss.aspx?feed=currentheadlines&categorylist=105"


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ibkr-paper-bot"})
    return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")


def refresh() -> dict:
    """Rebuild toxicity.json. Returns the data (or yesterday's on failure)."""
    data = {"date": dt.date.today().isoformat(), "fsi": {}, "etf": [],
            "reverse_split": []}
    try:
        # Nasdaq-listed: Symbol|Security Name|Category|Test|FinancialStatus|Lot|ETF|NextShares
        lines = _fetch(SYMDIR).splitlines()
        hdr = lines[0].split("|")
        i_sym, i_fsi, i_etf = hdr.index("Symbol"), hdr.index("Financial Status"), hdr.index("ETF")
        for ln in lines[1:]:
            f = ln.split("|")
            if len(f) <= max(i_sym, i_fsi, i_etf) or f[i_sym].startswith("File Creation"):
                continue
            sym = f[i_sym].strip().upper()
            if f[i_fsi].strip().upper() in BAD_FSI and f[i_fsi].strip():
                data["fsi"][sym] = f[i_fsi].strip().upper()
            if f[i_etf].strip().upper() == "Y":
                data["etf"].append(sym)
    except Exception:
        log.warning("nasdaqlisted.txt fetch failed", exc_info=True)
    try:
        # Other-listed (NYSE/AMEX): ACT Symbol|Security Name|Exchange|CQS|ETF|...
        lines = _fetch(OTHERDIR).splitlines()
        hdr = lines[0].split("|")
        i_sym, i_etf = hdr.index("ACT Symbol"), hdr.index("ETF")
        for ln in lines[1:]:
            f = ln.split("|")
            if len(f) > max(i_sym, i_etf) and f[i_etf].strip().upper() == "Y":
                data["etf"].append(f[i_sym].strip().upper())
    except Exception:
        log.warning("otherlisted.txt fetch failed", exc_info=True)
    try:
        # Reverse-split announcements (tickers appear in parentheses in titles)
        xml = _fetch(SPLIT_RSS)
        for title in re.findall(r"<title>(.*?)</title>", xml, re.S):
            for tick in re.findall(r"\(([A-Z]{1,5})\)", title):
                data["reverse_split"].append(tick)
    except Exception:
        log.warning("reverse-split RSS fetch failed", exc_info=True)

    if data["fsi"] or data["etf"]:
        data["etf"] = sorted(set(data["etf"]))
        data["reverse_split"] = sorted(set(data["reverse_split"]))
        import os

        tmp = TOX_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, TOX_FILE)
        log.info("toxicity.json refreshed: %d distressed, %d ETFs, %d recent "
                 "reverse splits", len(data["fsi"]), len(data["etf"]),
                 len(data["reverse_split"]))
        return data
    log.warning("toxicity refresh produced nothing - keeping previous file")
    return load()


def load() -> dict:
    try:
        return json.loads(TOX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"fsi": {}, "etf": [], "reverse_split": []}


def block_reason(symbol: str, tox: dict | None = None) -> str:
    """Non-empty = this symbol violates a directive or is exchange-flagged."""
    t = tox if tox is not None else load()
    s = symbol.upper()
    if s in t.get("fsi", {}):
        return f"exchange financial-status {t['fsi'][s]} (distressed - will dilute)"
    if s in set(t.get("etf", [])):
        return "ETF (banned by directive - exact exchange flag)"
    return ""
