"""Monte Carlo bootstrap: turn one backtest into a DISTRIBUTION of outcomes.

A single backtest gives you one number - the path history happened to take.
This tool resamples the strategy's actual daily returns (in blocks, to keep
short-term streakiness) into thousands of alternate years, then reports the
spread: what a lucky year, a typical year, and an unlucky year look like,
and the probability of losing money at all.

Example:
    python run_montecarlo.py --symbol SPY --strategy momentum
"""

from __future__ import annotations

import argparse

import numpy as np

from bot.backtest import run_backtest
from bot.data import fetch_yahoo_daily
from bot.strategy import STRATEGIES, make_strategy


def block_bootstrap_paths(
    daily_returns: np.ndarray,
    horizon: int,
    sims: int,
    block: int,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (ending_multiples, max_drawdowns) for `sims` resampled paths."""
    rng = np.random.default_rng(seed)
    n = len(daily_returns)
    if n < block:
        raise ValueError("Not enough return history for the chosen block size")

    endings = np.empty(sims)
    max_dds = np.empty(sims)
    blocks_per_path = int(np.ceil(horizon / block))

    for k in range(sims):
        # Inclusive upper bound: the block containing the most recent day
        # must be drawable too (rng.integers' range is half-open).
        starts = rng.integers(0, n - block + 1, size=blocks_per_path)
        path = np.concatenate([daily_returns[s : s + block] for s in starts])[:horizon]
        # Prepend the 1.0 starting stake so a loss on day one of a path
        # counts as drawdown - without it, first-day crashes are invisible.
        curve = np.concatenate(([1.0], np.cumprod(1 + path)))
        endings[k] = curve[-1]
        running_max = np.maximum.accumulate(curve)
        max_dds[k] = ((curve - running_max) / running_max).min()

    return endings, max_dds


def main() -> None:
    parser = argparse.ArgumentParser(description="Monte Carlo bootstrap of a strategy")
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="sma_cross")
    parser.add_argument("--years", type=int, default=10, help="History to resample from")
    parser.add_argument("--horizon", type=int, default=252, help="Days per simulated path (252 = 1y)")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--block", type=int, default=21, help="Resampling block size in days")
    args = parser.parse_args()

    bars = fetch_yahoo_daily(args.symbol, years=args.years)
    strategy = make_strategy(args.strategy)
    result = run_backtest(bars, strategy)
    rets = result.equity_curve.pct_change().dropna().to_numpy()
    # Drop the strategy's forced-flat warmup stretch: those structural 0%
    # days are a data-availability artifact, and resampling them would pull
    # every simulated statistic toward zero. (Genuine in-cash days AFTER
    # warmup are real strategy behavior and stay in the pool.)
    rets = rets[strategy.warmup - 2 :]

    endings, max_dds = block_bootstrap_paths(rets, args.horizon, args.sims, args.block)
    ret_pct = 100 * (endings - 1)

    p = lambda q, a: float(np.percentile(a, q))
    horizon_years = args.horizon / 252

    print(f"Monte Carlo: {args.strategy} on {args.symbol}")
    print(f"  ({args.sims:,} bootstrap paths of {args.horizon} days "
          f"(~{horizon_years:.1f}y), resampled from {len(rets):,} real days)")
    print("-" * 56)
    print(f"  Historical backtest CAGR:      {result.cagr_pct:>8.1f}%")
    print()
    print(f"  Simulated {horizon_years:.1f}y return distribution:")
    print(f"    Unlucky (5th pct):           {p(5, ret_pct):>8.1f}%")
    print(f"    Below average (25th pct):    {p(25, ret_pct):>8.1f}%")
    print(f"    Median:                      {p(50, ret_pct):>8.1f}%")
    print(f"    Above average (75th pct):    {p(75, ret_pct):>8.1f}%")
    print(f"    Lucky (95th pct):            {p(95, ret_pct):>8.1f}%")
    print()
    print(f"  Probability of a LOSING period: {100 * float((endings < 1).mean()):>6.1f}%")
    print(f"  Median max drawdown:            {100 * p(50, max_dds):>6.1f}%")
    print(f"  Bad-case max drawdown (5th):    {100 * p(5, max_dds):>6.1f}%")
    print()
    print("  Caveats: resampling the past cannot invent regimes the past")
    print("  didn't contain, and it underestimates long crises. Treat the")
    print("  spread as a MINIMUM amount of uncertainty, not the full truth.")


if __name__ == "__main__":
    main()
