"""ML Trade Filter: XGBoost learns WHICH signal-days are worth trading.

Pipeline (built on mock data so it runs anywhere; swap in real bars later):

  1. engineer_features()      - the 5 classic signals + volatility/momentum
                                context. Every feature at row t is knowable
                                BEFORE day t's open (see bot/signals.py).
  2. triple_barrier_labels()  - Lopez de Prado's labeling: enter at day t's
                                open; label +1 if the profit target is hit
                                first, -1 if the stop is hit first, 0 if
                                time expires. Labels legitimately use FUTURE
                                prices - they are the answer key, never a
                                feature.
  3. walk-forward CV          - TimeSeriesSplit with an embargo gap of
                                `horizon` days so no training label's
                                forward window overlaps the test set.
                                NEVER shuffled K-fold: it leaks the future.
  4. Evaluation               - classification report + the number that
                                actually matters: expected value per trade
                                when the filter says "take it".

Run:  python run_ml_filter.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from sklearn.metrics import classification_report
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBClassifier

from bot.signals import ALL_SIGNALS

HORIZON = 10        # max holding period (trading days) for the triple barrier
PT_MULT = 2.0       # profit target = 2 x daily vol (asymmetric R:R, standard)
SL_MULT = 1.0       # stop loss     = 1 x daily vol
N_SPLITS = 5
SEED = 42


# ---------------------------------------------------------------------------
# Mock data - regime-switching volatility so the vol feature has signal
# ---------------------------------------------------------------------------

def make_mock_ohlcv(n: int = 2500, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    # Two-state Markov volatility regime (calm / stressed), persistent states.
    vols = np.array([0.008, 0.025])
    drifts = np.array([0.0006, -0.0004])
    stay_prob = 0.98
    states = np.empty(n, dtype=int)
    states[0] = 0
    flips = rng.random(n) > stay_prob
    for i in range(1, n):  # data GENERATION only - the pipeline itself is vectorized
        states[i] = states[i - 1] ^ flips[i]

    rets = rng.normal(drifts[states], vols[states])
    close = 100 * np.exp(np.cumsum(rets))

    open_ = np.empty(n)
    open_[0] = 100.0
    open_[1:] = close[:-1] * (1 + rng.normal(0, 0.002, n - 1))
    spread = np.abs(rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.integers(1_000_000, 5_000_000, n)

    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Feature engineering - everything at row t uses data through t-1 only
# ---------------------------------------------------------------------------

def engineer_features(df: pd.DataFrame, market: pd.DataFrame | None = None) -> pd.DataFrame:
    feats = pd.DataFrame(index=df.index)

    # The 5 classic signals (each already carries its own execution lag).
    for name, fn in ALL_SIGNALS.items():
        feats[f"sig_{name}"] = fn(df)

    close = df["close"]
    log_ret = np.log(close / close.shift(1))

    # 20-day realized volatility, annualized - THE risk-context feature.
    # rolling(20) covers t-19..t, so shift(1) makes row t cover t-20..t-1.
    feats["vol_20d"] = (log_ret.rolling(20).std() * np.sqrt(252)).shift(1)

    # Momentum context at two horizons (both lagged one day).
    feats["ret_5d"] = close.pct_change(5).shift(1)
    feats["ret_21d"] = close.pct_change(21).shift(1)

    # Trend context: distance from the 50-day mean (lagged).
    feats["dist_sma50"] = (close / close.rolling(50).mean() - 1).shift(1)

    # --- expanded set (2026-08-04) - each must EARN deployment via the
    # ablation in run_train_filter; all lagged one day like everything else.
    # Short-term reversal: daily single-stock returns mean-revert (Heston 2010).
    feats["ret_1d"] = close.pct_change(1).shift(1)
    # Overnight gap of the last completed session (returns accrue overnight -
    # Cooper 2008; a gap also flags news/attention).
    feats["gap_1d"] = (df["open"] / close.shift(1) - 1).shift(1)
    # Prior day's intraday range - complements close-to-close vol.
    feats["range_pct"] = ((df["high"] - df["low"]) / close).shift(1)
    # Relative volume - the attention/lottery-cohort marker from the research.
    feats["rel_volume"] = (df["volume"] / df["volume"].rolling(20).mean()).shift(1)
    # Distance from the (up to) 52-week high - anchoring/momentum context.
    # min_periods keeps early rows usable instead of burning a year of panel.
    feats["dist_52w_high"] = (close / close.rolling(252, min_periods=60).max() - 1).shift(1)
    # Volatility regime shift: recent vol vs baseline vol.
    feats["vol_ratio"] = (log_ret.rolling(5).std() / log_ret.rolling(20).std()).shift(1)

    # Market-regime context (pass market=SPY bars): the whole tape's trend
    # and stress level - a breakout in a calm bull tape is not the same trade
    # as one in a panic. Emitted ONLY when market data is provided, so
    # market-free callers (mock pipeline, tests) keep their feature shape.
    # Same lag discipline: reindex+ffill takes the market close ON each date,
    # then shift(1) makes row t see the market through t-1 only.
    if market is not None and len(market):
        mclose = market["close"].reindex(feats.index, method="ffill")
        mlog = np.log(mclose / mclose.shift(1))
        feats["mkt_ret_5d"] = mclose.pct_change(5).shift(1)
        feats["mkt_ret_21d"] = mclose.pct_change(21).shift(1)
        feats["mkt_vol_20d"] = (mlog.rolling(20).std() * np.sqrt(252)).shift(1)

    return feats


# ---------------------------------------------------------------------------
# Triple Barrier labeling (Lopez de Prado, "Advances in Financial ML" ch.3)
# ---------------------------------------------------------------------------

def triple_barrier_labels(
    df: pd.DataFrame,
    horizon: int = HORIZON,
    pt_mult: float = PT_MULT,
    sl_mult: float = SL_MULT,
    daily_vol: pd.Series | None = None,
) -> pd.DataFrame:
    """Label every day t as if entering LONG at day t's open.

    Three barriers around the entry price, scaled by volatility known at
    decision time (vol through t-1):
        upper  = entry * (1 + pt_mult * vol)   -> label +1 (profit) if hit first
        lower  = entry * (1 - sl_mult * vol)   -> label -1 (loss) if hit first
        vertical: `horizon` trading days       -> label  0 (expired)

    The path is checked on days t .. t+horizon-1 using highs (for the target)
    and lows (for the stop). If both barriers are touched on the SAME day the
    intraday ordering is unknowable from daily bars, so we conservatively
    assume the STOP was hit first (worst case - never flatters the model).

    `daily_vol` is injectable for deterministic testing; by default it is the
    20-day rolling std of log returns, lagged one day (no lookahead).

    Returns a DataFrame (label, exit_offset, realized_ret) on the subset of
    the index where a full horizon window and a valid vol estimate exist.
    NOTE: labels use future prices BY DESIGN - they are targets, not features.
    """
    n = len(df)
    if n < horizon + 21:
        raise ValueError("Not enough bars for labeling")

    close = df["close"]
    if daily_vol is None:
        log_ret = np.log(close / close.shift(1))
        daily_vol = log_ret.rolling(20).std().shift(1)

    entry = df["open"].to_numpy(float)
    vol = daily_vol.to_numpy(float)

    n_events = n - horizon + 1  # last t with a full window: t + horizon - 1 <= n - 1
    highs = sliding_window_view(df["high"].to_numpy(float), horizon)[:n_events]
    lows = sliding_window_view(df["low"].to_numpy(float), horizon)[:n_events]
    e = entry[:n_events]
    v = vol[:n_events]

    upper = e * (1 + pt_mult * v)
    lower = e * (1 - sl_mult * v)

    pt_hit = highs >= upper[:, None]
    sl_hit = lows <= lower[:, None]
    SENTINEL = horizon  # "never hit"
    first_pt = np.where(pt_hit.any(axis=1), pt_hit.argmax(axis=1), SENTINEL)
    first_sl = np.where(sl_hit.any(axis=1), sl_hit.argmax(axis=1), SENTINEL)

    label = np.zeros(n_events, dtype=int)
    label[(first_sl < SENTINEL) & (first_sl <= first_pt)] = -1  # ties -> stop first
    label[(first_pt < SENTINEL) & (first_pt < first_sl)] = 1

    exit_offset = np.where(
        label == 1, first_pt, np.where(label == -1, first_sl, horizon - 1)
    )
    expiry_close = df["close"].to_numpy(float)[
        np.arange(n_events) + (horizon - 1)
    ]
    # Honest fills on gap-throughs: if the exit day OPENED beyond the barrier,
    # a real order fills at that open, not at the barrier price. In a high-vol
    # universe, pretending stops fill exactly at the stop understates losses.
    # (Exit DAY is unchanged - only the fill price.)
    opens_w = sliding_window_view(df["open"].to_numpy(float), horizon)[:n_events]
    exit_open = opens_w[np.arange(n_events), exit_offset]
    pt_fill = np.where(exit_open >= upper, exit_open, upper)
    sl_fill = np.where(exit_open <= lower, exit_open, lower)
    realized = np.where(
        label == 1, pt_fill / e - 1,
        np.where(label == -1, sl_fill / e - 1, expiry_close / e - 1),
    )

    out = pd.DataFrame(
        {"label": label, "exit_offset": exit_offset, "realized_ret": realized},
        index=df.index[:n_events],
    )
    return out[~np.isnan(v)]


# ---------------------------------------------------------------------------
# Walk-forward training and evaluation
# ---------------------------------------------------------------------------

def get_cv(n_splits: int = N_SPLITS, gap: int = HORIZON) -> TimeSeriesSplit:
    """Expanding-window walk-forward splits with an embargo gap.

    The gap (= label horizon) purges training samples whose forward-looking
    label window would overlap the test period - without it, the last few
    training labels literally contain test-period prices.
    """
    return TimeSeriesSplit(n_splits=n_splits, gap=gap)


def build_dataset(df: pd.DataFrame, market: pd.DataFrame | None = None):
    feats = engineer_features(df, market=market)
    labels = triple_barrier_labels(df)
    data = feats.join(labels, how="inner").dropna()
    X = data[feats.columns]
    y = (data["label"] == 1).astype(int)  # "will the profit target be hit first?"
    meta = data[["label", "exit_offset", "realized_ret"]]
    return X, y, meta


def train_walk_forward(X: pd.DataFrame, y: pd.Series):
    """Out-of-sample predictions for every test fold; returns pooled results."""
    oos_pred = pd.Series(index=X.index, dtype=float)
    for fold, (tr, te) in enumerate(get_cv().split(X), 1):
        model = XGBClassifier(
            n_estimators=300, max_depth=3, learning_rate=0.05,
            subsample=0.9, colsample_bytree=0.9,
            eval_metric="logloss", random_state=SEED,
        )
        model.fit(X.iloc[tr], y.iloc[tr])
        proba = model.predict_proba(X.iloc[te])[:, 1]
        oos_pred.iloc[te] = proba
        print(f"  fold {fold}: train {len(tr):>5d} rows -> test {len(te):>5d} rows")
    return oos_pred.dropna()


def main() -> None:
    print("Building mock market and dataset...")
    df = make_mock_ohlcv()
    X, y, meta = build_dataset(df)
    print(f"Dataset: {len(X)} samples, {X.shape[1]} features\n")

    print("Walk-forward cross-validation (expanding window, embargo gap = horizon):")
    oos = train_walk_forward(X, y)
    y_oos = y.loc[oos.index]
    meta_oos = meta.loc[oos.index]

    print("\nClassification report (out-of-sample, threshold 0.5):")
    print(classification_report(y_oos, (oos > 0.5).astype(int), digits=3))

    print("Expected value per trade (the number that matters):")
    base = meta_oos["realized_ret"]
    print(f"  Take EVERY day (baseline):     {100 * base.mean():+.3f}%  ({len(base)} trades)")
    for thr in (0.5, 0.6):
        taken = meta_oos.loc[oos > thr, "realized_ret"]
        if len(taken):
            print(
                f"  Take only if P(win) > {thr:.1f}:     "
                f"{100 * taken.mean():+.3f}%  ({len(taken)} trades)"
            )
        else:
            print(f"  Take only if P(win) > {thr:.1f}:     no trades taken")
    print(
        "\nHonest reading: mock data contains no real edge, so the filter can"
        "\nonly learn volatility-regime structure (when stops are likely to be"
        "\nhit). On real data, run this exact pipeline before believing any"
        "\nsignal - and remember: a filter that says 'never trade' beats a"
        "\nfilter that flatters a losing strategy."
    )


if __name__ == "__main__":
    main()
