"""NASDAQ trade-halt feed - free, machine-readable, real-time.

Our +12% sub-$5 movers are prime LULD volatility-halt candidates, and the
ecosystem review found we consumed zero halt data: a halt freezes every exit
and, if it spans the close, silently defeats the EOD flatten. This module
polls nasdaqtrader.com's public RSS (all US listed halts, not just NASDAQ).

Reason codes that matter here:
  LUDP  = volatility pause (the +12% mover's natural habitat)
  T1/T2 = news pending/released - do NOT enter, anything can resume-gap
  H10/T12 etc. = regulatory - radioactive

Never raises; failures return empty (a missing feed must not stop trading,
it just removes a filter). Cached ~90s so per-scan polling is cheap.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
import urllib.request
import xml.etree.ElementTree as ET

log = logging.getLogger("bot.halts")

URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
NS = {"ndaq": "http://www.nasdaqtrader.com/"}
_cache: dict = {"at": 0.0, "halts": {}}


def todays_halts() -> dict[str, dict]:
    """{symbol: {reason, halt_time, resumed}} for TODAY (ET), cached ~90s.

    Adversarial-review hardened: the raw feed carries YEARS of history
    (live check found items back to 2019) and lists newest first, so a
    naive parse blocks recycled tickers forever and lets an older resumed
    entry mask a symbol's CURRENT halt. We therefore (a) scope strictly to
    today's ET date via ndaq:HaltDate, and (b) keep only the LATEST event
    per symbol. The cache timestamp is stamped even on failure so a feed
    outage costs one 10s timeout per 90s window, never one per symbol."""
    now = time.time()
    if now - _cache["at"] < 90:
        return _cache["halts"]
    _cache["at"] = now  # stamp FIRST - failures must not disable the cache
    try:
        from zoneinfo import ZoneInfo

        today_et = dt.datetime.now(ZoneInfo("America/New_York")).strftime("%m/%d/%Y")
        req = urllib.request.Request(URL, headers={"User-Agent": "ibkr-paper-bot"})
        root = ET.fromstring(urllib.request.urlopen(req, timeout=10).read())
        halts: dict[str, dict] = {}
        for item in root.iter("item"):
            sym = item.findtext("ndaq:IssueSymbol", "", NS).strip().upper()
            halt_date = item.findtext("ndaq:HaltDate", "", NS).strip()
            halt_time = item.findtext("ndaq:HaltTime", "", NS).strip()
            if not sym or halt_date != today_et:
                continue  # stale/historical entry - not today's business
            prev = halts.get(sym)
            if prev and prev["halt_time"] >= halt_time:
                continue  # keep the LATEST same-day event per symbol
            halts[sym] = {
                "reason": item.findtext("ndaq:ReasonCode", "", NS).strip(),
                "halt_time": halt_time,
                "resumed": bool(item.findtext("ndaq:ResumptionTradeTime", "", NS).strip()),
            }
        _cache["halts"] = halts
    except Exception:
        log.debug("halt feed unavailable", exc_info=True)
    return _cache["halts"]


def halt_block_reason(symbol: str) -> str:
    """Non-empty string = do not ENTER this symbol right now.

    Blocks anything halted today that has not resumed, and anything with a
    news-pending/regulatory code even after resumption (resume gaps in those
    are violent). A resumed volatility pause (LUDP) is allowed again - that
    is ordinary life in this tier."""
    h = todays_halts().get(symbol.upper())
    if not h:
        return ""
    if not h["resumed"]:
        return f"HALTED now ({h['reason'] or '?'} @ {h['halt_time']})"
    if h["reason"].upper().startswith(("T1", "T2", "H")):
        return f"halted earlier today on news/regulatory ({h['reason']})"
    return ""
