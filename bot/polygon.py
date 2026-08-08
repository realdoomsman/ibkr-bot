"""Polygon.io client (Stocks Advanced tier) - the independent second opinion.

Why a SECOND real-time source when IBKR already streams: redundancy. The
documented bot-killer is a primary feed that FREEZES while looking healthy -
a bot once traded a whole afternoon on a price stuck since 11 AM. The
watchdog cross-checks IBKR's tape against Polygon's every run; disagreement
or staleness pages the phone. One feed can lie quietly; two can't.

Also the lab's truth source: aggregates include DELISTED tickers (the
corpses Yahoo-based studies can't see - survivorship bias), and v3 quotes
give real historical NBBO to validate the EDGE spread estimator against.

Key handling: POLYGON_API_KEY environment variable ONLY - never in code,
config, or the repo. Every function fails open (returns None): a Polygon
outage must never block trading, and never pages the phone by itself -
only positive evidence of divergence does.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger("bot.polygon")

BASE = "https://api.polygon.io"
NY = ZoneInfo("America/New_York")


def _key() -> str | None:
    """POLYGON_API_KEY env var, else gitignored polygon_key.txt beside the
    code. The file fallback exists because S4U scheduled tasks (watchdog)
    don't reliably load user-level environment variables."""
    k = (os.environ.get("POLYGON_API_KEY") or "").strip()
    if k:
        return k
    try:
        from pathlib import Path

        k = Path(__file__).resolve().parent.parent.joinpath(
            "polygon_key.txt").read_text(encoding="utf-8-sig").strip()
        return k or None
    except Exception:
        return None


def available() -> bool:
    return _key() is not None


def _get(path: str, params: dict | None = None, timeout: int = 15) -> dict | None:
    """GET a Polygon endpoint (or a full next_url). None on any failure."""
    key = _key()
    if not key:
        return None
    url = path if path.startswith("http") else f"{BASE}{path}"
    q = dict(params or {})
    q["apiKey"] = key
    sep = "&" if "?" in url else "?"
    url = url + sep + urllib.parse.urlencode(q)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ibkr-paper-bot"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        log.debug("polygon GET failed: %s", path, exc_info=True)
        return None


# ---------------------------------------------------------------- parsing
# Pure functions, unit-tested offline; the network wrappers stay thin.

def parse_last_trade(js: dict | None, now_ns: int) -> tuple[float | None, float | None]:
    """(price, age_seconds) from a /v2/last/trade payload. Trade timestamps
    arrive in EPOCH NANOSECONDS ('t' = SIP timestamp)."""
    try:
        res = (js or {}).get("results") or {}
        price = float(res["p"])
        age_s = (now_ns - int(res["t"])) / 1e9
        if price <= 0 or age_s < -60:  # future-stamped = clock nonsense
            return None, None
        return price, max(0.0, age_s)
    except Exception:
        return None, None


def spread_pct_from_quote(q: dict | None) -> float | None:
    """Quoted spread %% of mid from one v3 NBBO record. None for crossed,
    one-sided, or zero quotes (halts, opening rotations)."""
    try:
        bid, ask = float(q["bid_price"]), float(q["ask_price"])
        if bid <= 0 or ask <= bid:
            return None
        return round(100 * (ask - bid) / ((ask + bid) / 2), 3)
    except Exception:
        return None


def df_from_aggs(js: dict | None) -> pd.DataFrame | None:
    """DataFrame(date, open, high, low, close, volume) from an aggregates
    payload; 't' is epoch MILLISECONDS. None if empty/malformed."""
    rows = (js or {}).get("results") or []
    if not rows:
        return None
    try:
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(NY)
        return df.rename(columns={"o": "open", "h": "high", "l": "low",
                                  "c": "close", "v": "volume"})[
            ["date", "open", "high", "low", "close", "volume"]]
    except Exception:
        return None


def parse_last_nbbo(js: dict | None, now_ns: int) -> tuple[float | None, float | None]:
    """(spread %% of mid, age_seconds) from a /v2/last/nbbo payload
    ('p'/'P' = bid/ask prices, 't' = SIP nanoseconds)."""
    try:
        res = (js or {}).get("results") or {}
        s = spread_pct_from_quote({"bid_price": res["p"], "ask_price": res["P"]})
        if s is None:
            return None, None
        age = (now_ns - int(res["t"])) / 1e9
        if age < -60:
            return None, None
        return s, max(0.0, age)
    except Exception:
        return None, None


# ---------------------------------------------------------------- fetchers

def last_nbbo_spread(symbol: str) -> tuple[float | None, float | None]:
    """(current quoted spread %%, quote age s) - real-time NBBO, the Advanced
    tier's headline feature. Journaled at entry beside the EDGE estimate:
    the 2026-08-07 spreadcheck run graded EDGE at only 58%% tier agreement
    on penny names (errors BOTH directions), so the real quote is the number
    the gate should trust."""
    js = _get(f"/v2/last/nbbo/{symbol.upper()}")
    return parse_last_nbbo(js, time.time_ns())


def last_trade(symbol: str) -> tuple[float | None, float | None]:
    """(price, age_seconds) of the most recent SIP trade - the real-time
    cross-check the watchdog uses. (None, None) on any failure."""
    js = _get(f"/v2/last/trade/{symbol.upper()}")
    return parse_last_trade(js, time.time_ns())


def daily_bars(symbol: str, start: str, end: str,
               adjusted: bool = True) -> pd.DataFrame | None:
    """Daily OHLCV between ISO dates - WORKS FOR DELISTED TICKERS, which is
    the whole point: survivorship-free history for the lab."""
    js = _get(f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start}/{end}",
              {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": 50000})
    return df_from_aggs(js)


def minute_bars(symbol: str, day: str, minutes: int = 5,
                adjusted: bool = True, start: str | None = None) -> pd.DataFrame | None:
    """Intraday bars for one session - or a range when `start` is given
    (extended hours included; filter on the 'date' column for RTH).
    Dates are ISO YYYY-MM-DD."""
    js = _get(f"/v2/aggs/ticker/{symbol.upper()}"
              f"/range/{minutes}/minute/{start or day}/{day}",
              {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": 50000})
    return df_from_aggs(js)


def quote_at(symbol: str, when_et: dt.datetime) -> dict | None:
    """First NBBO quote at/after an ET datetime (one small request - sampling
    a handful of marks beats paying for a full day's quote tape)."""
    ns = int(when_et.timestamp() * 1e9)
    js = _get(f"/v3/quotes/{symbol.upper()}",
              {"timestamp.gte": ns, "order": "asc", "sort": "timestamp", "limit": 1})
    res = (js or {}).get("results") or []
    return res[0] if res else None


def quoted_spread_pct(symbol: str, day: str,
                      times: list[dt.time] | None = None) -> tuple[float | None, int]:
    """(median quoted NBBO spread %% of mid, n samples) for one session,
    sampled at half-hour marks 09:35-15:35 ET. The TRUTH the EDGE estimator
    is validated against."""
    if times is None:
        times = [dt.time(9, 35)] + [dt.time(h, m) for h in range(10, 16)
                                    for m in (5, 35)]
    d = dt.date.fromisoformat(day)
    samples = []
    for t in times:
        q = quote_at(symbol, dt.datetime.combine(d, t, tzinfo=NY))
        s = spread_pct_from_quote(q)
        # a quote stamped past the close belongs to another day - discard
        if s is not None and q and int(q.get("sip_timestamp", 0)) <= int(
                dt.datetime.combine(d, dt.time(16, 0), tzinfo=NY).timestamp() * 1e9):
            samples.append(s)
    if not samples:
        return None, 0
    samples.sort()
    mid = len(samples) // 2
    med = samples[mid] if len(samples) % 2 else (samples[mid - 1] + samples[mid]) / 2
    return round(med, 3), len(samples)
