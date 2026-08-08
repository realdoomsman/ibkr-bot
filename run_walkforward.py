"""Walk-forward validation - the honest test for a tunable strategy.

Optimizes parameters on ~2 years of data, tests them frozen on the next
~6 months, rolls forward, and stitches the out-of-sample segments together.

Examples:
    python run_walkforward.py --symbol SPY                       # sma_cross on real data
    python run_walkforward.py --symbol SPY --strategy momentum
    python run_walkforward.py --source demo --symbol DEMO        # synthetic data
"""

from __future__ import annotations

import argparse

from bot.data import fetch_yahoo_daily, synthetic_daily_bars
from bot.strategy import STRATEGIES
from bot.walkforward import walk_forward


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward validation")
    parser.add_argument("--source", choices=["demo", "yahoo"], default="yahoo")
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="sma_cross")
    parser.add_argument("--years", type=int, default=10, help="Years of yahoo history")
    parser.add_argument("--days", type=int, default=2520, help="Bars of demo data")
    parser.add_argument("--is-len", type=int, default=504, help="In-sample bars (~2y)")
    parser.add_argument("--oos-len", type=int, default=126, help="Out-of-sample bars (~6m)")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.source == "demo":
        bars = synthetic_daily_bars(args.symbol, days=args.days, seed=args.seed)
        print("(Synthetic random-walk data - any 'edge' found here is pure luck.)\n")
    else:
        bars = fetch_yahoo_daily(args.symbol, years=args.years)

    result = walk_forward(bars, args.strategy, is_len=args.is_len, oos_len=args.oos_len)
    print(result.summary())


if __name__ == "__main__":
    main()
