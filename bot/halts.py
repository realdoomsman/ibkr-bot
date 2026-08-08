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
    """{symbol: {reason, halt_time, resumed}} for today, cached ~90s."""
    now = time.time()
    if now - _cache["at"] < 90:
        return _cache["halts"]
    halts: dict[str, dict] = {}
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "ibkr-paper-bot"})
        root = ET.fromstring(urllib.request.urlopen(req, timeout=10).read())
        for item in root.iter("item"):
            sym = item.findtext("ndaq:IssueSymbol", "", NS).strip().upper()
            if not sym:
                continue
            halts[sym] = {
                "reason": item.findtext("ndaq:ReasonCode", "", NS).strip(),
                "halt_time": item.findtext("ndaq:HaltTime", "", NS).strip(),
                "resumed": bool(item.findtext("ndaq:ResumptionTradeTime", "", NS).strip()),
            }
        _cache.update(at=now, halts=halts)
    except Exception:
        log.debug("halt feed unavailable", exc_info=True)
    return halts


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
