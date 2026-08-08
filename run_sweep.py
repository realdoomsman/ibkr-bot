"""Parameter sweep - see how fragile a 'good' backtest really is.

Runs the full parameter grid for a strategy over the same data and prints
every result. What to look for:

  - Is the BEST result much better than the MEDIAN? The gap is roughly how
    much you'd be fooling yourself by quoting the best one.
  - Do neighboring parameter values perform similarly? A lonely spike of
    profit surrounded by losers is noise, not an edge.

Examples:
    python run_sweep.py --symbol SPY
    python run_sweep.py --symbol AAPL --strategy rsi_reversion
"""

from __future__ import annotations

import argparse
import statistics

from bot.backtest import run_backtest
from bot.data import fetch_yahoo_daily, synthetic_daily_bars
from bot.strategy import STRATEGIES


def main() -> None:
    parser = argparse.ArgumentParser(description="Parameter sweep")
    parser.add_argument("--source", choices=["demo", "yahoo"], default="yahoo")
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="sma_cross")
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--days", type=int, default=2520)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.source == "demo":
        bars = synthetic_daily_bars(args.symbol, days=args.days, seed=args.seed)
        print("(Synthetic random-walk data - any 'edge' found here is pure luck.)\n")
    else:
        bars = fetch_yahoo_daily(args.symbol, years=args.years)

    cls = STRATEGIES[args.strategy]
    rows = []
    for params in cls.param_grid():
        strat = cls(**params)
        if len(bars) <= strat.warmup + 10:
            continue
        r = run_backtest(bars, strat)
        rows.append((params, r))

    if not rows:
        raise SystemExit("Not enough data for any parameter combination")

    rows.sort(key=lambda pr: pr[1].total_return_pct, reverse=True)

    sym = bars.attrs.get("symbol", "?")
    print(f"Sweep: {args.strategy} on {sym}  ({bars.index[0].date()} -> {bars.index[-1].date()})")
    print("-" * 72)
    print(f"{'params':<32}{'return':>9}{'sharpe':>8}{'max DD':>9}{'trades':>8}")
    for params, r in rows:
        p = ", ".join(f"{k}={v}" for k, v in params.items()) or "(defaults)"
        print(
            f"{p:<32}{r.total_return_pct:>8.1f}%{r.sharpe:>8.2f}"
            f"{r.max_drawdown_pct:>8.1f}%{r.num_trades:>8d}"
        )

    returns = [r.total_return_pct for _, r in rows]
    buy_hold = rows[0][1].buy_hold_return_pct
    print("-" * 72)
    print(f"  Best:        {max(returns):>8.1f}%   <- what an over-fitter would quote")
    print(f"  Median:      {statistics.median(returns):>8.1f}%   <- closer to honest expectations")
    print(f"  Worst:       {min(returns):>8.1f}%")
    print(f"  Buy & hold:  {buy_hold:>8.1f}%   <- the bar to beat")


if __name__ == "__main__":
    main()
