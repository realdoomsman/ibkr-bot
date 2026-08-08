"""Scanner enrichment: the leading signals the ecosystem review said we lack.

Our rotation and radar lead with price change - the LAGGING signal. The
serious small-cap ecosystem (and the one peer-reviewed intraday edge) leads
with RELATIVE VOLUME at time-of-day, then float, IPO age, and catalysts.
This module computes those. MEASUREMENT-FIRST: everything here is logged and
journaled so the evidence decides at a gate which become hard filters;
nothing gates entries yet except what the user has already banned.

RVOL: today's cumulative volume vs what's NORMAL by this time of day.
Until our own 5-minute archive is deep enough to build true per-symbol
profiles, expected volume = 20d average daily volume x the classic U-shaped
intraday cumulative curve (heavy open, quiet lunch, heavy close). The lab
refines this with real archive data as it accumulates.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("bot.scanner")

# Cumulative fraction of a normal day's volume completed by each half-hour
# since the open (approximation of the standard U-shape; refined by the lab
# once data_archive holds enough sessions).
_CURVE = [0.13, 0.205, 0.265, 0.315, 0.36, 0.40, 0.44, 0.485, 0.535,
          0.595, 0.67, 0.78, 1.00]

ENRICH_CACHE = Path("data_cache/enrich")


def expected_volume_fraction(now_et: dt.datetime) -> float:
    """Fraction of an average day's volume normally done by `now_et`."""
    mins = (now_et.hour - 9) * 60 + now_et.minute - 30
    if mins <= 0:
        return 0.02
    slot = min(int(mins // 30), len(_CURVE) - 1)
    prev = _CURVE[slot - 1] if slot > 0 else 0.0
    frac_in = min(1.0, (mins - slot * 30) / 30)
    return min(1.0, prev + (_CURVE[slot] - prev) * frac_in)


def rvol_estimate(cum_volume_today: float, avg_daily_volume: float,
                  now_et: dt.datetime) -> float | None:
    """RVOL = today's volume so far vs normal-by-now. >3 = genuinely in play.

    Best-effort: both inputs come from sources with unit quirks (IBKR bars
    report lots for some feeds), so this is a LOGGED feature, not a gate,
    until the lab calibrates it against our own archive."""
    if not avg_daily_volume or avg_daily_volume <= 0:
        return None
    expected = avg_daily_volume * expected_volume_fraction(now_et)
    if expected <= 0:
        return None
    return round(cum_volume_today / expected, 2)


def enrich_symbol(symbol: str) -> dict:
    """Float (millions of shares) and IPO age (months) via Yahoo, cached per
    day. The pump profile the review flagged: tiny float + young listing.
    Never raises; missing fields come back None."""
    ENRICH_CACHE.mkdir(parents=True, exist_ok=True)
    p = ENRICH_CACHE / f"{symbol.lower()}.json"
    today = dt.date.today().isoformat()
    try:
        cached = json.loads(p.read_text(encoding="utf-8"))
        if cached.get("date") == today:
            return cached
    except Exception:
        pass
    out = {"date": today, "symbol": symbol, "float_m": None, "ipo_months": None}
    try:
        import yfinance as yf

        info = yf.Ticker(symbol).info or {}
        flt = info.get("floatShares") or info.get("sharesOutstanding")
        if flt:
            out["float_m"] = round(float(flt) / 1e6, 1)
        first = info.get("firstTradeDateEpochUtc")
        if first:
            months = (time.time() - float(first) / (1000 if first > 1e12 else 1)) \
                / (30.44 * 86400)
            out["ipo_months"] = round(months, 1)
    except Exception:
        log.debug("enrich failed for %s", symbol, exc_info=True)
    try:
        p.write_text(json.dumps(out), encoding="utf-8")
    except Exception:
        pass
    return out
