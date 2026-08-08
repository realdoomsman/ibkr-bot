"""Market data: synthetic bars for offline learning, IBKR bars for real use."""

from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]


def synthetic_daily_bars(
    symbol: str = "DEMO",
    days: int = 756,  # ~3 trading years
    start_price: float = 100.0,
    seed: int | None = None,
) -> pd.DataFrame:
    """Generate a plausible-looking daily OHLCV series (geometric random walk).

    This exists so you can run the backtester and learn the mechanics WITHOUT
    an IBKR account or market-data subscription. A random walk has no real
    exploitable pattern - so if your strategy "makes money" on it, that is
    your first lesson in why backtests lie.
    """
    if seed is None:
        # Deterministic per symbol so runs are reproducible. (crc32, not hash():
        # Python salts str hashing per process, which would silently change the
        # data on every run.)
        seed = zlib.crc32(symbol.encode())
    rng = np.random.default_rng(seed)

    drift, vol = 0.0003, 0.015
    rets = rng.normal(drift, vol, days)
    closes = start_price * np.exp(np.cumsum(rets))

    opens = np.empty(days)
    opens[0] = start_price
    opens[1:] = closes[:-1] * (1 + rng.normal(0, 0.002, days - 1))
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.004, days)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.004, days)))
    volume = rng.integers(1_000_000, 5_000_000, days)

    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume},
        index=idx,
    )
    df.index.name = "date"
    df.attrs["symbol"] = symbol
    return df


def fetch_yahoo_daily(
    symbol: str,
    years: int = 10,
    cache_dir: str = "data_cache",
) -> pd.DataFrame:
    """Free real daily bars via Yahoo Finance (yfinance) - no API key needed.

    Great for honest backtesting on real history while you wait for IBKR
    access. Prices are split/dividend adjusted. Data is cached locally and
    refreshed at most once per day.
    """
    from pathlib import Path

    cache = Path(cache_dir) / f"{symbol.lower().replace('.', '_')}_{years}y.csv"
    cache.parent.mkdir(exist_ok=True)

    today = pd.Timestamp.today().normalize()
    # fromtimestamp() converts the mtime to LOCAL time to match Timestamp.today();
    # interpreting it as UTC would serve yesterday's cache as fresh in US timezones.
    if cache.exists() and pd.Timestamp.fromtimestamp(cache.stat().st_mtime).normalize() == today:
        df = pd.read_csv(cache, index_col="date", parse_dates=True)
    else:
        import yfinance as yf

        raw = yf.Ticker(symbol).history(period=f"{years}y", interval="1d", auto_adjust=True)
        if raw.empty:
            raise RuntimeError(
                f"Yahoo Finance returned no data for {symbol!r}. "
                f"Check the ticker, or try again later."
            )
        raw.columns = [str(c).lower() for c in raw.columns]
        df = raw[["open", "high", "low", "close", "volume"]].dropna()
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        df.index.name = "date"
        df.to_csv(cache)

    df.index.name = "date"
    df.attrs["symbol"] = symbol.upper()
    return df


def fetch_intraday_bars(ib, symbol: str, min_bars: int, bar_size: str = "5 mins") -> pd.DataFrame:
    """Recent intraday bars from IBKR (regular trading hours only).

    Sized so the strategy's warmup is covered: ~78 5-minute bars per session.
    NOTE: without a real-time market data subscription IBKR serves this
    DELAYED ~15 minutes - fine for testing mechanics, blinding for real
    intraday signals.
    """
    import math

    from ib_async import Stock, util

    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    days = min(5, max(2, math.ceil(min_bars / 78) + 1))
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=f"{days} D",
        barSizeSetting=bar_size,
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )
    if not bars:
        raise RuntimeError(f"No intraday data returned for {symbol}")
    df = util.df(bars).set_index("date")
    df.index = pd.to_datetime(df.index)
    df = df[["open", "high", "low", "close", "volume"]]
    df.attrs["symbol"] = symbol
    return df


def archive_intraday_bars(symbol: str, bars: pd.DataFrame) -> None:
    """Persist every scanned intraday bar set - waste no information.

    The engines fetch these bars all day and would otherwise discard them.
    Archiving builds a proprietary intraday dataset (one CSV per symbol per
    session, overwritten each scan so the day's last write is the fullest)
    that future intraday models can train on. Delayed data, but consistently
    delayed - fine for research. Never raises: archiving must not trade.
    """
    try:
        from pathlib import Path

        if not len(bars):
            return
        d = Path("data_archive/intraday")
        d.mkdir(parents=True, exist_ok=True)
        # Last session ONLY - the fetch spans 2-3 sessions, and writing them
        # all would duplicate every session across consecutive daily files.
        last_day = bars.index[-1].date()
        day_bars = bars[[ts.date() == last_day for ts in bars.index]]
        day_bars.to_csv(d / f"{symbol}_{last_day.isoformat()}.csv")
    except Exception:
        import logging

        logging.getLogger("bot.data").debug("bar archive failed for %s", symbol, exc_info=True)


def fetch_headlines(symbol: str, limit: int = 10) -> list[str]:
    """Recent news headlines for a symbol via Yahoo Finance. Free; best effort.

    Returns [] on any failure - news is a research input, never a dependency.
    """
    try:
        import yfinance as yf

        items = yf.Ticker(symbol).news or []
    except Exception:
        return []
    titles = []
    for item in items[:limit]:
        title = item.get("title") or (item.get("content") or {}).get("title")
        if title:
            titles.append(str(title).strip())
    return titles


def fetch_daily_bars(ib, symbol: str, days: int = 260) -> pd.DataFrame:
    """Fetch daily bars from IBKR via an already-connected ib_async IB instance.

    Requires TWS or IB Gateway running with API enabled. Uses adjusted daily
    bars; free delayed data is fine for daily-bar strategies.
    """
    import math

    from ib_async import Stock, util

    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    # IBKR rejects day-denominated requests over 365 days ("must be made in
    # years"), so phrase long lookbacks in years (~252 trading days each).
    if days <= 365:
        duration = f"{max(days, 30)} D"
    else:
        duration = f"{math.ceil(days / 252)} Y"
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting="1 day",
        whatToShow="ADJUSTED_LAST",
        useRTH=True,
        formatDate=1,
    )
    if not bars:
        raise RuntimeError(
            f"No historical data returned for {symbol}. Check the symbol and "
            f"your market-data permissions in TWS."
        )
    df = util.df(bars)
    df = df.rename(columns={"date": "date"}).set_index("date")
    df.index = pd.to_datetime(df.index)
    df = df[["open", "high", "low", "close", "volume"]]
    df.attrs["symbol"] = symbol
    return df
