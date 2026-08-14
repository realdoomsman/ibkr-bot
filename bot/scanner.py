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

import pandas as pd

log = logging.getLogger("bot.scanner")

# Cumulative fraction of a normal day's volume completed by each half-hour
# since the open (approximation of the standard U-shape; the GENERIC
# fallback - tracked symbols get their own measured curve, below).
_CURVE = [0.13, 0.205, 0.265, 0.315, 0.36, 0.40, 0.44, 0.485, 0.535,
          0.595, 0.67, 0.78, 1.00]

ENRICH_CACHE = Path("data_cache/enrich")
PROFILE_CACHE = Path("data_cache/rvol_profiles")


def expected_volume_fraction(now_et: dt.datetime,
                             curve: list[float] | None = None) -> float:
    """Fraction of an average day's volume normally done by `now_et`.
    `curve` = a per-symbol measured curve; default = the generic U-shape."""
    c = curve if curve and len(curve) == len(_CURVE) else _CURVE
    mins = (now_et.hour - 9) * 60 + now_et.minute - 30
    if mins <= 0:
        return 0.02
    slot = min(int(mins // 30), len(c) - 1)
    prev = c[slot - 1] if slot > 0 else 0.0
    frac_in = min(1.0, (mins - slot * 30) / 30)
    return min(1.0, prev + (c[slot] - prev) * frac_in)


def rvol_estimate(cum_volume_today: float, avg_daily_volume: float,
                  now_et: dt.datetime,
                  curve: list[float] | None = None) -> float | None:
    """RVOL = today's volume so far vs normal-by-now. >3 = genuinely in play.

    Best-effort: both inputs come from sources with unit quirks (IBKR bars
    report lots for some feeds), so this is a LOGGED feature, not a gate,
    until the lab calibrates it against our own archive."""
    if not avg_daily_volume or avg_daily_volume <= 0:
        return None
    expected = avg_daily_volume * expected_volume_fraction(now_et, curve)
    if expected <= 0:
        return None
    return round(cum_volume_today / expected, 2)


def curve_from_bars(bars: pd.DataFrame, min_days: int = 10) -> list[float] | None:
    """A symbol's own cumulative intraday volume curve (13 half-hour slots)
    from 5-min RTH bars spanning multiple sessions. Median across days (a
    single halt/news day must not warp the profile); normalized to end at
    1.0; None when fewer than `min_days` usable sessions."""
    try:
        rth = bars[[dt.time(9, 30) <= t.time() < dt.time(16, 0)
                    for t in bars["date"]]]
        if not len(rth):
            return None
        day_curves = []
        for _, day in rth.groupby([t.date() for t in rth["date"]]):
            total = float(day["volume"].sum())
            if total <= 0 or len(day) < 30:  # skip halt-shortened/thin days
                continue
            cum, slots = 0.0, []
            for s in range(13):
                end = dt.time(9 + (30 + (s + 1) * 30) // 60,
                              (30 + (s + 1) * 30) % 60)
                in_slot = day[[t.time() < end for t in day["date"]]]
                slots.append(float(in_slot["volume"].sum()) / total)
            day_curves.append(slots)
        if len(day_curves) < min_days:
            return None
        arr = pd.DataFrame(day_curves)
        curve = [round(float(arr[s].median()), 4) for s in range(13)]
        # monotone + terminal 1.0 by construction of cumulative sums; guard:
        curve[-1] = 1.0
        return curve
    except Exception:
        return None


def build_symbol_curve(symbol: str, sessions: int = 20) -> list[float] | None:
    """Fetch + cache (7 days) a symbol's measured curve. NETWORK: call this
    from TASK contexts only (radar capture) - the engine reads cache via
    cached_symbol_curve() and never pays a network round-trip."""
    try:
        from bot import polygon

        if not polygon.available():
            return None
        cached = cached_symbol_curve(symbol, max_age_days=7)
        if cached is not None:
            return cached
        PROFILE_CACHE.mkdir(parents=True, exist_ok=True)
        end = dt.date.today()
        start = end - dt.timedelta(days=int(sessions * 1.6) + 3)
        bars = polygon.minute_bars(symbol, end.isoformat(), minutes=5,
                                   start=start.isoformat())
        curve = curve_from_bars(bars) if bars is not None else None
        if curve:
            (PROFILE_CACHE / f"{symbol.lower()}.json").write_text(
                json.dumps({"date": dt.date.today().isoformat(),
                            "curve": curve}), encoding="utf-8")
        return curve
    except Exception:
        log.debug("curve build failed for %s", symbol, exc_info=True)
        return None


def cached_enrich(symbol: str) -> dict:
    """Cache-only read of today's enrichment - no network, engine-safe."""
    try:
        c = json.loads((ENRICH_CACHE / f"{symbol.lower()}.json")
                       .read_text(encoding="utf-8"))
        return c if c.get("date") == dt.date.today().isoformat() else {}
    except Exception:
        return {}


def cached_symbol_curve(symbol: str,
                        max_age_days: int = 10) -> list[float] | None:
    """Cache-only read - safe in the trading loop (no network, ever)."""
    try:
        c = json.loads((PROFILE_CACHE / f"{symbol.lower()}.json")
                       .read_text(encoding="utf-8"))
        age = (dt.date.today() - dt.date.fromisoformat(c["date"])).days
        return c["curve"] if age <= max_age_days else None
    except Exception:
        return None


def edge_spread_pct(bars) -> float | None:
    """Effective spread %% from OHLC candles alone (Ardia-Guidotti-Kroencke
    EDGE estimator, J. Financial Economics 2024; `bidask` package, verified
    in-venv: recovers a synthetic 1%% spread as 1.06%%±0.02%%).

    This is the system's largest previously-unmeasured cost: a 1.5%% spread
    consumes a fifth of the ladder's +8%% first rung. ADVISORY tiers derived
    from the ladder's expectancy math: <=0.75%% full clip, <=1.5%% half,
    >1.5%% reject. Journaled per entry; gating decided at a gate.

    Precision note: the estimator's error scales ~1/sqrt(n). The verified
    envelope was 390 bars; live callers pass ~90 (2-3 sessions), so treat the
    tier boundaries as soft and let the gate weigh the noise. Returns the bar
    count alongside the estimate so the journal records the sample size."""
    try:
        from bidask import edge

        if bars is None or len(bars) < 60:
            return None
        s = edge(bars["open"], bars["high"], bars["low"], bars["close"], sign=False)
        if s != s:  # NaN
            return None
        return round(100 * float(s), 3)
    except Exception:
        log.debug("edge spread failed", exc_info=True)
        return None


def spread_tier(spread_pct: float | None) -> str:
    if spread_pct is None:
        return "unknown"
    if spread_pct <= 0.75:
        return "full"
    if spread_pct <= 1.5:
        return "half"
    return "reject"


def split_mismatch(yahoo_close: float, raw_prior_close: float,
                   tol: float = 0.05) -> bool:
    """True when adjusted (Yahoo) and raw (IBKR) prior closes disagree badly -
    the corporate-action fingerprint. A forensic teardown showed this exact
    adjusted-vs-raw join minting 63%% of a backtest's profit; in penny-land
    (constant reverse splits) it corrupts RVOL by the split factor. On True:
    quarantine derived features for the symbol, never trust the join."""
    if not yahoo_close or not raw_prior_close or yahoo_close <= 0:
        return False
    return abs(yahoo_close / raw_prior_close - 1) > tol


def premarket_high(ib, symbol: str):
    """(premarket high, time it was set) for today, from IBKR extended-hours
    5-min bars (04:00-09:30 ET). Returns (None, None) if unavailable
    (fail-open - missing PM data must never block trading).

    Two evidence-based filters ride on this (1,506 real gapper replays):
      1. BREAK filter: movers that break the PM high are ~breakeven/83%-win;
         those below it are -11.6%/49%-win. Only buy the break.
      2. EARLY-HIGH filter: within the break cohort, a PM high set BEFORE
         08:00 nets +2.50%/90%-win (n=326) vs -1.35%/79% for late PM highs -
         an early high later broken = fresh RTH demand over a mature base;
         a just-set high broken immediately = chasing an extended spike."""
    try:
        import datetime as _dt
        from zoneinfo import ZoneInfo

        from ib_async import Stock, util

        contract = Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)
        bars = ib.reqHistoricalData(
            contract, endDateTime="", durationStr="1 D",
            barSizeSetting="5 mins", whatToShow="TRADES",
            useRTH=False, formatDate=1,   # <-- extended hours INCLUDED
        )
        if not bars:
            return None, None
        df = util.df(bars)
        df["date"] = pd.to_datetime(df["date"])
        today = _dt.datetime.now(ZoneInfo("America/New_York")).date()
        pm = df[[t.date() == today and t.time() < _dt.time(9, 30) for t in df["date"]]]
        if not len(pm):
            return None, None
        idx = pm["high"].idxmax()
        return (round(float(pm.loc[idx, "high"]), 4),
                pm.loc[idx, "date"].time())
    except Exception:
        log.debug("premarket_high failed for %s", symbol, exc_info=True)
        return None, None


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
    out = {"date": today, "symbol": symbol, "float_m": None, "ipo_months": None,
           "mcap_m": None}
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
        # Polygon reference data fills whatever Yahoo dropped (Yahoo keeps
        # first claim on float_m - floatShares is TRUE float; Polygon's
        # shares-outstanding is the same proxy Yahoo's own fallback used).
        # Always fetched when available: sec_type is the warrant/unit
        # backstop the review demanded (5-letter W/U/R names pass every
        # symbol-shape heuristic).
        from bot import polygon

        if polygon.available():
            ov = polygon.ticker_overview(symbol)
            if ov.get("type"):
                out["sec_type"] = ov["type"]
            if out["float_m"] is None and ov.get("shares_out_m"):
                out["float_m"] = ov["shares_out_m"]
            if out["mcap_m"] is None and ov.get("mcap_m"):
                out["mcap_m"] = ov["mcap_m"]
            if out["ipo_months"] is None and ov.get("list_date"):
                d = dt.date.fromisoformat(ov["list_date"])
                out["ipo_months"] = round((dt.date.today() - d).days / 30.44, 1)
            # TTM revenue growth (user's growth spec, 2026-08-14): growthlab
            # showed growth+volume lifts every cell it touches at multi-day
            # horizons; journaled on intraday entries so the gate can test
            # whether it separates winners intraday too. Task-context call.
            g = polygon.ttm_revenue_growth(symbol)
            if g is not None:
                out["rev_growth"] = g
    except Exception:
        log.debug("polygon enrich failed for %s", symbol, exc_info=True)
    # Cache only real data: an all-None result from a transient hiccup
    # must not poison the whole day (review finding) - retry next call.
    if out["float_m"] is not None or out["ipo_months"] is not None \
            or out["mcap_m"] is not None:
        try:
            p.write_text(json.dumps(out), encoding="utf-8")
        except Exception:
            pass
    return out
