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
import urllib.error
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


# Circuit breaker (review finding): a black-holed connection burns its full
# timeout PER CALL, and the radar/lab paths chain dozens of calls - minutes
# of stall from an outage that is supposed to cost nothing. After 3
# consecutive transport failures, calls short-circuit for 120s.
_BREAKER = {"fails": 0, "until": 0.0}


def _get(path: str, params: dict | None = None, timeout: int = 15) -> dict | None:
    """GET a Polygon endpoint (or a full next_url). None on any failure."""
    key = _key()
    if not key:
        return None
    if time.time() < _BREAKER["until"]:
        return None  # breaker open - Polygon proved unreachable moments ago
    url = path if path.startswith("http") else f"{BASE}{path}"
    q = dict(params or {})
    q["apiKey"] = key
    sep = "&" if "?" in url else "?"
    url = url + sep + urllib.parse.urlencode(q)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ibkr-paper-bot"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _BREAKER["fails"] = 0
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 4xx (unknown ticker, bad request) = Polygon is UP; don't trip
        # the breaker for a bad symbol. 5xx counts as an outage signal.
        if e.code >= 500:
            _trip()
        log.debug("polygon GET %s -> HTTP %s", path, e.code)
        return None
    except Exception:
        _trip()
        log.debug("polygon GET failed: %s", path, exc_info=True)
        return None


def _trip() -> None:
    _BREAKER["fails"] += 1
    if _BREAKER["fails"] >= 3:
        _BREAKER["until"] = time.time() + 120
        log.warning("polygon: %d consecutive failures - pausing all calls "
                    "for 120s (fail-fast, trading unaffected)", _BREAKER["fails"])


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


def movers_from_snapshot(js: dict | None, min_pct: float, lo: float, hi: float,
                         min_dvol_m: float) -> list[dict]:
    """Filter a full-market snapshot down to live movers in our band.
    One API call sees EVERY US ticker's day change - the radar's screener
    sees a delayed, pre-filtered slice. Sorted hottest first."""
    out = []
    for t in (js or {}).get("tickers") or []:
        try:
            sym = str(t.get("ticker") or "")
            if not sym.isalpha() or len(sym) > 5:
                continue  # dot-suffix units/warrants, indexes
            if len(sym) == 5 and sym[-1] in "WUR":
                # NASDAQ 5th-letter convention: W=warrant U=unit R=rights -
                # plain-alpha symbols the isalpha() check can't catch (review
                # finding: HYMCW/AACIU-style names reached the radar pool).
                # The radar's per-pick security-type check is the backstop.
                continue
            pct = float(t.get("todaysChangePerc") or 0)
            day = t.get("day") or {}
            price = float((t.get("lastTrade") or {}).get("p") or day.get("c") or 0)
            vw = float(day.get("vw") or 0) or price
            # day.v is zero before the open; min.av (accumulated volume incl
            # premarket) keeps the 8:20 capture from being a silent no-op.
            vol = float(day.get("v") or 0) or float((t.get("min") or {}).get("av") or 0)
            dvol_m = vol * vw / 1e6
            if pct >= min_pct and lo <= price <= hi and dvol_m >= min_dvol_m:
                out.append({"symbol": sym, "last": round(price, 4),
                            "pct": round(pct, 1), "dollar_vol_m": round(dvol_m, 2)})
        except Exception:
            continue
    return sorted(out, key=lambda m: -m["pct"])


def overview_fields(js: dict | None) -> dict:
    """The useful subset of /v3/reference/tickers/{t}: shares out (millions),
    market cap (millions), listing date, security type."""
    res = (js or {}).get("results") or {}
    out: dict = {}
    try:
        sh = res.get("weighted_shares_outstanding") \
            or res.get("share_class_shares_outstanding")
        if sh:
            out["shares_out_m"] = round(float(sh) / 1e6, 1)
        if res.get("market_cap"):
            out["mcap_m"] = round(float(res["market_cap"]) / 1e6, 1)
        if res.get("list_date"):
            out["list_date"] = str(res["list_date"])
        if res.get("type"):
            out["type"] = str(res["type"])
    except Exception:
        pass
    return out


def reverse_splits_from(js: dict | None) -> set[str]:
    """Tickers with a REVERSE split (split_to < split_from) in a
    /v3/reference/splits payload."""
    out = set()
    for r in (js or {}).get("results") or []:
        try:
            if float(r["split_to"]) < float(r["split_from"]):
                out.add(str(r["ticker"]).upper())
        except Exception:
            continue
    return out


def news_items(js: dict | None, symbol: str) -> list[dict]:
    """[{title, published_utc, sentiment}] from a /v2/reference/news payload;
    sentiment is Polygon's per-ticker insight for OUR symbol when present."""
    out = []
    for a in (js or {}).get("results") or []:
        try:
            sent = None
            for ins in a.get("insights") or []:
                if str(ins.get("ticker", "")).upper() == symbol.upper():
                    sent = ins.get("sentiment")
            out.append({"title": str(a.get("title") or "")[:120],
                        "published_utc": str(a.get("published_utc") or ""),
                        "sentiment": sent})
        except Exception:
            continue
    return out


def parse_last_nbbo_sizes(js: dict | None, now_ns: int) \
        -> tuple[float | None, float | None, float | None]:
    """(bid_size, ask_size, age_seconds) from a /v2/last/nbbo payload
    ('s'/'S' = bid/ask sizes). The first-15-minutes demand gate reads this:
    entries in the opening window need bid size >= 1.5x ask size."""
    try:
        res = (js or {}).get("results") or {}
        bs, as_ = float(res["s"]), float(res["S"])
        if bs < 0 or as_ <= 0:
            return None, None, None
        age = (now_ns - int(res["t"])) / 1e9
        if age < -60:
            return None, None, None
        return bs, as_, max(0.0, age)
    except Exception:
        return None, None, None


def last_nbbo_sizes(symbol: str) -> tuple[float | None, float | None, float | None]:
    """(bid_size, ask_size, age_s) - real-time book pressure at the NBBO.
    Entry-path caller: short timeout, fails to (None, None, None)."""
    js = _get(f"/v2/last/nbbo/{symbol.upper()}", timeout=5)
    return parse_last_nbbo_sizes(js, time.time_ns())


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
    the gate should trust. Short timeout: this runs in the ENTRY PATH - a
    Polygon outage must cost seconds, never stall a scan."""
    js = _get(f"/v2/last/nbbo/{symbol.upper()}", timeout=5)
    return parse_last_nbbo(js, time.time_ns())


def last_trade(symbol: str) -> tuple[float | None, float | None]:
    """(price, age_seconds) of the most recent SIP trade - the real-time
    cross-check the watchdog uses. (None, None) on any failure. Short
    timeout: called from live paths, must fail fast."""
    js = _get(f"/v2/last/trade/{symbol.upper()}", timeout=5)
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


def snapshot_movers(min_pct: float = 10.0, lo: float = 0.20, hi: float = 7.0,
                    min_dvol_m: float = 1.0) -> list[dict]:
    """Live movers across the ENTIRE US market in one call (real-time on the
    Advanced tier). Radar merges these ahead of its screener pool - during
    pennymo's mid-session refresh this is how a spike gets seen in seconds
    instead of on the next screener pass. Empty list on failure.
    timeout=10: called from the engine's radar refresh - must fail fast."""
    js = _get("/v2/snapshot/locale/us/markets/stocks/tickers", timeout=10)
    return movers_from_snapshot(js, min_pct, lo, hi, min_dvol_m)


def ticker_overview(symbol: str) -> dict:
    """Reference data: shares out, market cap, list date, type. Steadier than
    Yahoo's .info (which day-caches failures and rate-limits). {} on failure.
    timeout=5: reachable from the engine's radar refresh via enrich_symbol."""
    return overview_fields(_get(f"/v3/reference/tickers/{symbol.upper()}",
                                timeout=5))


def recent_reverse_splits(days_back: int = 365, days_fwd: int = 30) -> set[str] | None:
    """Every ticker with a reverse split executed in the window (default: the
    research-backed 12-month lookback + announced upcoming). The NASDAQ RSS
    only shows the last few weeks of headlines; this is the full registry.

    Returns None on ANY failed page (review finding): a silently partial
    registry is worse than no answer - the caller keeps yesterday's."""
    today = dt.date.today()
    out: set[str] = set()
    path: str | None = "/v3/reference/splits"
    params: dict | None = {
        "execution_date.gte": (today - dt.timedelta(days=days_back)).isoformat(),
        "execution_date.lte": (today + dt.timedelta(days=days_fwd)).isoformat(),
        "limit": 1000}
    for _ in range(5):  # paginate, bounded
        js = _get(path, params)
        if js is None:
            return None  # partial result must not masquerade as complete
        out |= reverse_splits_from(js)
        path, params = js.get("next_url"), None
        if not path:
            break
    return out


def ticker_news(symbol: str, limit: int = 5) -> list[dict]:
    """Recent articles for one ticker with Polygon's sentiment tag - the
    included poor-man's Benzinga. [] on failure. timeout=5: reachable from
    the engine's radar refresh."""
    js = _get("/v2/reference/news",
              {"ticker": symbol.upper(), "limit": limit, "order": "desc",
               "sort": "published_utc"}, timeout=5)
    return news_items(js, symbol)


def revenue_growth_from(js: dict | None) -> float | None:
    """TTM revenue growth from a /vX/reference/financials payload: latest
    four quarters' revenues vs the prior four. None when unknowable."""
    try:
        rows = (js or {}).get("results") or []
        revs = []
        for r in rows:
            v = (((r.get("financials") or {}).get("income_statement") or {})
                 .get("revenues") or {}).get("value")
            if v is not None:
                revs.append(float(v))
        if len(revs) < 8:
            return None
        latest, prior = sum(revs[:4]), sum(revs[4:8])
        if prior <= 0:
            return None
        return round(latest / prior - 1, 4)
    except Exception:
        return None


def ttm_revenue_growth(symbol: str) -> float | None:
    """Year-over-year trailing-twelve-month revenue growth (the 'earnings'
    half of the user's growth spec; revenue beats EPS for coverage and
    manipulation-resistance). None on any failure - fail-open."""
    js = _get("/vX/reference/financials",
              {"ticker": symbol.upper(), "limit": 8, "timeframe": "quarterly",
               "order": "desc", "sort": "period_of_report_date"})
    return revenue_growth_from(js)


def trade_at(symbol: str, when_et: dt.datetime) -> tuple[float | None, float | None]:
    """(price, ts_seconds_offset) of the first trade at/after an ET datetime -
    the markout rig's probe. Offset = how long after the asked time the trade
    actually printed (thin names may not trade for a while)."""
    ns = int(when_et.timestamp() * 1e9)
    js = _get(f"/v3/trades/{symbol.upper()}",
              {"timestamp.gte": ns, "order": "asc", "sort": "timestamp",
               "limit": 1}, timeout=5)
    res = (js or {}).get("results") or []
    if not res:
        return None, None
    try:
        return float(res[0]["price"]), (int(res[0]["sip_timestamp"]) - ns) / 1e9
    except Exception:
        return None, None


def grouped_daily(day: str, adjusted: bool = False) -> pd.DataFrame | None:
    """EVERY US ticker's OHLCV for one session - including tickers that later
    DIED. The survivorship-free universe builder. RAW prices by default
    (adjusted joins are the documented backtest poison)."""
    js = _get(f"/v2/aggs/grouped/locale/us/market/stocks/{day}",
              {"adjusted": str(adjusted).lower()})
    rows = (js or {}).get("results") or []
    if not rows:
        return None
    try:
        df = pd.DataFrame(rows).rename(columns={
            "T": "symbol", "o": "open", "h": "high", "l": "low",
            "c": "close", "v": "volume", "vw": "vwap"})
        if "vwap" not in df.columns:
            df["vwap"] = df["close"]
        # 'vw' is optional PER ROW; NaN would silently fail the dollar-volume
        # test and drop exactly the thin dying names this data exists to keep.
        df["vwap"] = df["vwap"].fillna(df["close"])
        return df[["symbol", "open", "high", "low", "close", "volume", "vwap"]]
    except Exception:
        return None


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
