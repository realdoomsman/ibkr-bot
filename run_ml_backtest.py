"""Backtest simulation of the ML trade filter, with realistic costs.

Uses the walk-forward OUT-OF-SAMPLE probabilities only (never a model's
opinion of its own training data), takes at most one position at a time,
and charges 5bps slippage per side plus a 0.1% round-trip fee on every
trade. Prints institutional-style risk metrics: Sharpe ratio and Maximum
Drawdown alongside the raw performance.

Run:  python run_ml_backtest.py
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from run_ml_filter import build_dataset, make_mock_ohlcv, train_walk_forward

FEE_ROUND_TRIP = 0.001   # 0.1% per round trip (the spec)
SLIPPAGE_BPS = 5.0       # 5 basis points paid on each side

TRADING_DAYS = 252


def apply_costs(
    raw_ret: float,
    fee: float = FEE_ROUND_TRIP,
    slippage_bps: float = SLIPPAGE_BPS,
) -> float:
    """Convert a frictionless trade return into a net-of-costs return.

    Entry fills slippage_bps WORSE (higher), exit slippage_bps worse (lower):
        net_growth = (1 + raw) * (1 - slip) / (1 + slip)
    then the round-trip fee comes off the proceeds:
        net = net_growth * (1 - fee) - 1
    """
    slip = slippage_bps / 10_000
    return (1 + raw_ret) * (1 - slip) / (1 + slip) * (1 - fee) - 1


def simulate(meta: pd.DataFrame, proba: pd.Series, threshold: float):
    """Sequential one-position-at-a-time simulation over the OOS period.

    Enter at day t when P(win) > threshold; the position then occupies the
    account until the triple-barrier exit (exit_offset days later), so
    overlapping signals are naturally skipped - exactly like the live bot.
    """
    p = proba.reindex(meta.index).to_numpy(float)
    exit_off = meta["exit_offset"].to_numpy(int)
    raw = meta["realized_ret"].to_numpy(float)

    equity = [1.0]
    trade_rets: list[float] = []
    holds: list[int] = []
    i, n = 0, len(meta)
    while i < n:
        if p[i] > threshold:
            r = apply_costs(raw[i])
            trade_rets.append(r)
            holds.append(int(exit_off[i]) + 1)
            equity.append(equity[-1] * (1 + r))
            i += int(exit_off[i]) + 1  # busy until the barrier exit
        else:
            i += 1
    return np.array(trade_rets), np.array(holds), np.array(equity)


def print_metrics(label: str, trade_rets: np.ndarray, holds: np.ndarray, equity: np.ndarray):
    if len(trade_rets) == 0:
        print(f"  {label:<28} no trades taken")
        return
    total = equity[-1] - 1
    running_max = np.maximum.accumulate(equity)
    max_dd = float(((equity - running_max) / running_max).min())
    avg_hold = float(holds.mean())
    trades_per_year = TRADING_DAYS / avg_hold
    sharpe = 0.0
    if len(trade_rets) > 1 and trade_rets.std() > 0:
        sharpe = float(trade_rets.mean() / trade_rets.std() * math.sqrt(trades_per_year))
    win_rate = float((trade_rets > 0).mean())
    print(
        f"  {label:<28} {len(trade_rets):>4d} trades | win {100 * win_rate:>4.0f}% | "
        f"avg {100 * trade_rets.mean():>+6.3f}% | total {100 * total:>+7.1f}% | "
        f"Sharpe {sharpe:>5.2f} | MaxDD {100 * max_dd:>6.1f}%"
    )


def main() -> None:
    print("Building dataset and walk-forward OOS predictions...")
    df = make_mock_ohlcv()
    X, y, meta = build_dataset(df)
    oos = train_walk_forward(X, y)
    meta_oos = meta.loc[oos.index]
    print(f"\nSimulating over {len(meta_oos)} out-of-sample days "
          f"({meta_oos.index[0].date()} -> {meta_oos.index[-1].date()})")
    print(f"Costs: {SLIPPAGE_BPS:.0f}bps slippage/side + {100 * FEE_ROUND_TRIP:.1f}% fee per round trip\n")

    baseline = simulate(meta_oos, pd.Series(1.0, index=meta_oos.index), threshold=0.0)
    print_metrics("Take every day (baseline)", *baseline)
    for thr in (0.50, 0.55, 0.60):
        print_metrics(f"Filter: P(win) > {thr:.2f}", *simulate(meta_oos, oos, thr))

    print(
        "\nHonest reading: on mock data the filter can only learn volatility"
        "\nstructure, so treat this run as a PIPELINE test, not a strategy."
        "\nThe pattern to look for on real data: the filter should cut the"
        "\ntrade count sharply while improving avg return per trade and MaxDD."
        "\nIf it doesn't, the signals carry no information the filter can use."
    )


if __name__ == "__main__":
    main()
