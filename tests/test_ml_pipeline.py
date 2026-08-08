"""Leak-hunting test suite for the signal library and ML pipeline.

The three deadliest bugs in quant pipelines are lookahead bias, off-by-one
shifts, and train/test leakage. Each gets a direct, mechanical test here:
we PERTURB specific rows and assert that signals which claim to only use
the past really do only use the past.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.signals import ALL_SIGNALS
from run_ml_backtest import apply_costs
from run_ml_filter import (
    HORIZON,
    build_dataset,
    engineer_features,
    get_cv,
    make_mock_ohlcv,
    triple_barrier_labels,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return make_mock_ohlcv(n=600, seed=7)


def perturb_row(df: pd.DataFrame, k: int, factor: float = 1.5) -> pd.DataFrame:
    """Return a copy with row k's prices AND volume scaled (a big, visible
    shock). Volume included so volume-derived features (rel_volume) are
    genuinely exercised by the lag tests, not vacuously passed."""
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        out.iloc[k, out.columns.get_loc(col)] *= factor
    vcol = out.columns.get_loc("volume")
    out.iloc[k, vcol] = int(out.iloc[k, vcol] * factor)
    return out


# ---------------------------------------------------------------------------
# Signal contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,fn", list(ALL_SIGNALS.items()))
def test_signal_output_contract(bars, name, fn):
    sig = fn(bars)
    assert isinstance(sig, pd.Series)
    assert sig.index.equals(bars.index)
    assert set(np.unique(sig)) <= {-1, 0, 1}, f"{name} emitted values outside -1/0/1"
    assert np.issubdtype(sig.dtype, np.integer)


@pytest.mark.parametrize("name,fn", list(ALL_SIGNALS.items()))
def test_signal_fires_sometimes(bars, name, fn):
    # A signal that never fires can't leak - but also can't be a feature.
    assert (fn(bars) != 0).any(), f"{name} never fired on 600 varied bars"


# ---------------------------------------------------------------------------
# Lookahead bias - the core tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,fn", list(ALL_SIGNALS.items()))
@pytest.mark.parametrize("k", [250, 400, 599])
def test_signal_ignores_its_own_day(bars, name, fn, k):
    """Off-by-one check: signal at row k must be decided BEFORE day k's open,
    so shocking day k's own prices must not change signal[k]."""
    base = fn(bars)
    shocked = fn(perturb_row(bars, k))
    assert base.iloc[k] == shocked.iloc[k], (
        f"{name}: signal at row {k} changed when row {k}'s own prices changed "
        f"- the execution lag is broken (lookahead by one day)"
    )


@pytest.mark.parametrize("name,fn", list(ALL_SIGNALS.items()))
@pytest.mark.parametrize("k", [300, 450])
def test_signal_ignores_the_future(bars, name, fn, k):
    """Shocking day k must leave every signal up to and including row k
    untouched (information can only flow forward)."""
    base = fn(bars)
    shocked = fn(perturb_row(bars, k))
    pd.testing.assert_series_equal(
        base.iloc[: k + 1], shocked.iloc[: k + 1],
        obj=f"{name} history before a future shock",
    )


@pytest.mark.parametrize("name,fn", list(ALL_SIGNALS.items()))
def test_signal_prefix_stability(bars, name, fn):
    """Truncation check: computing on the first 500 bars must reproduce the
    first 500 values of the full computation - i.e. adding new days never
    rewrites history."""
    full = fn(bars)
    trunc = fn(bars.iloc[:500])
    pd.testing.assert_series_equal(full.iloc[:500], trunc, obj=f"{name} prefix")


def test_vol_feature_is_lagged(bars):
    """The 20-day realized vol at row k must not include day k's return."""
    k = 350
    base = engineer_features(bars)["vol_20d"]
    shocked = engineer_features(perturb_row(bars, k))["vol_20d"]
    assert base.iloc[k] == pytest.approx(shocked.iloc[k]), (
        "vol_20d at row k reacted to row k's own price - missing shift(1)"
    )
    # ...and it SHOULD react the following day (sanity that the test can fail).
    assert base.iloc[k + 1] != pytest.approx(shocked.iloc[k + 1])


def test_every_feature_is_lagged(bars):
    """EVERY engineered feature at row k must ignore row k's own prices.

    Runs over engineer_features' full output, so any future feature added
    without its shift(1) fails here automatically instead of leaking quietly."""
    k = 350
    base = engineer_features(bars)
    shocked = engineer_features(perturb_row(bars, k))
    for col in base.columns:
        b, s = base[col].iloc[k], shocked[col].iloc[k]
        if pd.isna(b) and pd.isna(s):
            continue
        assert b == pytest.approx(s), (
            f"{col} at row k reacted to row k's own price - missing shift(1)"
        )


# ---------------------------------------------------------------------------
# Triple barrier labeling - deterministic scenarios
# ---------------------------------------------------------------------------


def flat_bars(n: int = 45, price: float = 100.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    p = np.full(n, price)
    return pd.DataFrame(
        {"open": p, "high": p.copy(), "low": p.copy(), "close": p.copy(),
         "volume": np.full(n, 1_000_000)},
        index=idx,
    )


def const_vol(df: pd.DataFrame, v: float = 0.01) -> pd.Series:
    return pd.Series(v, index=df.index)


def test_barrier_profit_target():
    # Entry 100, vol 1% -> target 102, stop 99. High touches 103 at offset 2.
    df = flat_bars()
    df.iloc[7, df.columns.get_loc("high")] = 103.0
    out = triple_barrier_labels(df, horizon=10, daily_vol=const_vol(df))
    row = out.iloc[5]  # event day t=5: window covers days 5..14
    assert row["label"] == 1
    assert row["exit_offset"] == 2
    assert row["realized_ret"] == pytest.approx(0.02)


def test_barrier_stop_loss():
    df = flat_bars()
    df.iloc[12, df.columns.get_loc("low")] = 98.0  # breaches 99 stop
    out = triple_barrier_labels(df, horizon=10, daily_vol=const_vol(df))
    row = out.iloc[10]
    assert row["label"] == -1
    assert row["exit_offset"] == 2
    assert row["realized_ret"] == pytest.approx(-0.01)


def test_barrier_same_day_tie_is_conservative():
    # Both barriers pierced on the same day -> must assume the STOP hit first.
    df = flat_bars()
    df.iloc[20, df.columns.get_loc("high")] = 103.0
    df.iloc[20, df.columns.get_loc("low")] = 98.5
    out = triple_barrier_labels(df, horizon=10, daily_vol=const_vol(df))
    row = out.iloc[20]
    assert row["label"] == -1, "same-day tie must resolve to the stop (worst case)"
    assert row["exit_offset"] == 0


def test_barrier_time_expiry():
    df = flat_bars()
    out = triple_barrier_labels(df, horizon=10, daily_vol=const_vol(df))
    row = out.iloc[30]  # perfectly flat window -> nothing hit
    assert row["label"] == 0
    assert row["exit_offset"] == 10 - 1
    assert row["realized_ret"] == pytest.approx(0.0)


def test_barrier_labels_do_not_extend_past_data():
    # Every labeled event must have a full horizon of future bars.
    df = flat_bars(n=45)
    out = triple_barrier_labels(df, horizon=10, daily_vol=const_vol(df))
    assert len(out) == 45 - 10 + 1


# ---------------------------------------------------------------------------
# Walk-forward CV - leakage structure
# ---------------------------------------------------------------------------


def test_walk_forward_is_chronological_with_embargo():
    X = np.zeros((500, 3))
    prev_test_start = -1
    for tr, te in get_cv().split(X):
        # 1. Training data strictly precedes test data (no shuffling).
        assert tr.max() < te.min()
        # 2. Embargo: the last training label's forward window (HORIZON days)
        #    must end BEFORE the test set begins.
        assert tr.max() + HORIZON < te.min(), (
            "gap too small: training labels overlap the test period"
        )
        # 3. Folds roll forward in time.
        assert te.min() > prev_test_start
        prev_test_start = te.min()


def test_dataset_is_aligned_and_clean():
    df = make_mock_ohlcv(n=800, seed=11)
    X, y, meta = build_dataset(df)
    assert X.index.equals(y.index) and X.index.equals(meta.index)
    assert not X.isna().any().any(), "NaNs in features would crash or bias training"
    assert set(y.unique()) <= {0, 1}


# ---------------------------------------------------------------------------
# Backtest cost model
# ---------------------------------------------------------------------------


def test_costs_reduce_returns_correctly():
    # raw +2%, 5bps slippage per side, 0.1% round-trip fee:
    # (1.02 * 0.9995/1.0005) * 0.999 - 1 = 0.0179617...
    assert apply_costs(0.02) == pytest.approx(0.0179617, abs=1e-6)
    # Costs must always hurt:
    assert apply_costs(0.0) < 0.0
    assert apply_costs(-0.01) < -0.01


def test_costs_scale_with_parameters():
    assert apply_costs(0.02, fee=0.0, slippage_bps=0.0) == pytest.approx(0.02)
    assert apply_costs(0.02, fee=0.002) < apply_costs(0.02, fee=0.001)
