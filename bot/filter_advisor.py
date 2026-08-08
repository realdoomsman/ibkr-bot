"""Advisory-only interface to the trained meta-labeling trade filter.

At every live entry the bots ask this module for a second opinion; the answer
is JOURNALED and nothing else. It cannot veto, resize, or delay an order.
The nightly review accumulates the scoreboard (P&L of high-p vs low-p
entries); only sustained live evidence at an evaluation gate can promote the
filter from commentator to gatekeeper.

Current honest status (see models/trade_filter_meta.json): on real
out-of-sample data the filter has NOT yet demonstrated edge at p>0.5; its
rare high-conviction picks (p>0.6) looked strong but on far too few samples.
It is deployed to gather live evidence, not because it has proven itself.

Design rules:
  - Never raises: any failure returns None and trading proceeds untouched.
  - Uses cached Yahoo daily bars (no extra IBKR requests, no pacing risk).
  - Model loads once per process and hot-reloads if retrained on disk.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("bot.filter")

MODEL_PATH = Path("models/trade_filter.ubj")
META_PATH = Path("models/trade_filter_meta.json")

_cache: dict = {"model": None, "features": None, "mtime": None,
                "take": 0.5, "skip": 0.35}


def is_available() -> bool:
    return MODEL_PATH.exists() and META_PATH.exists()


def _load():
    mtime = MODEL_PATH.stat().st_mtime
    if _cache["model"] is None or _cache["mtime"] != mtime:
        from xgboost import XGBClassifier

        model = XGBClassifier()
        model.load_model(MODEL_PATH)
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        # Verdict thresholds come from the trainer's OOS profit analysis
        # (derive_thresholds in run_train_filter) - defaults if absent.
        _cache.update(model=model, features=meta["features"], mtime=mtime,
                      take=float(meta.get("take_threshold", 0.5)),
                      skip=float(meta.get("skip_threshold", 0.35)))
        log.info("Trade filter loaded (trained %s, %d samples, TAKE>=%.2f)",
                 meta.get("trained", "?"), meta.get("samples", 0), _cache["take"])
    return _cache["model"], _cache["features"]


def _cache_is_fresh(symbol: str, years: int = 2) -> bool:
    import pandas as pd

    p = Path("data_cache") / f"{symbol.lower().replace('.', '_')}_{years}y.csv"
    return p.exists() and (
        pd.Timestamp.fromtimestamp(p.stat().st_mtime).normalize()
        == pd.Timestamp.today().normalize()
    )


def prewarm(symbols: list[str]) -> None:
    """Load the model and warm each symbol's daily cache OUTSIDE the order
    path, so in-scan advise() calls are ~milliseconds and network-free."""
    if not is_available():
        return
    try:
        _load()
        # Pay the heavy imports HERE, not inside the first in-scan advise().
        from run_ml_filter import engineer_features  # noqa: F401
    except Exception:
        log.debug("prewarm: model load failed", exc_info=True)
        return
    from bot.data import fetch_yahoo_daily

    for sym in list(symbols) + ["SPY"]:  # SPY feeds the market-context features
        try:
            fetch_yahoo_daily(sym, years=2)
        except Exception:
            log.debug("prewarm: cache fetch failed for %s", sym, exc_info=True)


def advise(symbol: str, entry_next_open: bool = False, no_network: bool = False) -> dict | None:
    """P(win) for entering `symbol`, or None if unavailable. Advisory only.

    entry_next_open - score the row for TOMORROW's open (the daily bot's
        after-close flow): a placeholder bar is appended so the just-closed
        session's signal is visible to the model. Safe by construction: every
        feature is shift(1)-lagged, so the placeholder's values are never read.
    no_network - only use today's already-warmed cache (the intraday scan
        loop passes True so a cold cache degrades to 'no advice', never to a
        blocking fetch inside the order path).
    """
    if not is_available():
        return None
    try:
        if no_network and not _cache_is_fresh(symbol):
            return None

        import pandas as pd

        from bot.data import fetch_yahoo_daily
        from run_ml_filter import engineer_features

        model, features = _load()
        bars = fetch_yahoo_daily(symbol, years=2)
        # Market context for the mkt_* features; degrade to None (not a
        # crash) if SPY is unavailable - the NaN check below then simply
        # withholds advice when the deployed model actually needs them.
        market = None
        try:
            if not no_network or _cache_is_fresh("SPY"):
                market = fetch_yahoo_daily("SPY", years=2)
        except Exception:
            market = None
        if entry_next_open:
            bars = bars.copy()
            bars.loc[bars.index[-1] + pd.Timedelta(days=1)] = bars.iloc[-1].values
        else:
            # During the session, a midday-refreshed cache contains today's
            # IN-PROGRESS bar; scoring it would make p depend on what time
            # the cache was warmed. Score the last COMPLETED session always.
            from zoneinfo import ZoneInfo

            import datetime as _dt

            now_et = _dt.datetime.now(ZoneInfo("America/New_York"))
            if (_dt.time(9, 30) <= now_et.time() < _dt.time(16, 10)
                    and bars.index[-1].date() == now_et.date()):
                bars = bars.iloc[:-1]
                if market is not None and len(market) \
                        and market.index[-1].date() == now_et.date():
                    market = market.iloc[:-1]
        row = engineer_features(bars, market=market).iloc[[-1]]
        if list(row.columns) != features:
            row = row.reindex(columns=features)
        if row.isna().any().any():
            return None
        p = float(model.predict_proba(row)[0, 1])
        take, skip = _cache["take"], _cache["skip"]
        verdict = "TAKE" if p >= take else ("NEUTRAL" if p >= skip else "SKIP")
        return {"p_win": p, "verdict": verdict}
    except Exception:
        log.debug("filter advise failed for %s", symbol, exc_info=True)
        return None
