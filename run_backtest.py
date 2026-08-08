"""Run a backtest on demo, free real (Yahoo), or IBKR data.

Examples:
    python run_backtest.py                                  # synthetic demo data
    python run_backtest.py --source yahoo --symbol SPY      # REAL data, no account needed
    python run_backtest.py --source yahoo --symbol SPY --strategy rsi_reversion
    python run_backtest.py --source yahoo --symbol SPY --stress   # double all costs
    python run_backtest.py --source ibkr --symbol SPY       # via TWS (must be running)
"""

from __future__ import annotations

import argparse
import logging

from bot.backtest import run_backtest
from bot.data import fetch_yahoo_daily, synthetic_daily_bars
from bot.strategy import STRATEGIES, make_strategy


def load_bars(args):
    if args.source == "demo":
        print(
            "(Using SYNTHETIC random-walk data - great for learning the mechanics,\n"
            " meaningless for judging profitability. Try --source yahoo for real data.)\n"
        )
        return synthetic_daily_bars(args.symbol, days=args.days, seed=args.seed)
    if args.source == "yahoo":
        return fetch_yahoo_daily(args.symbol, years=args.years)
    if args.source == "ibkr":
        from ib_async import IB

        from bot.config import load_config
        from bot.data import fetch_daily_bars

        cfg = load_config()
        ib = IB()
        ib.connect(cfg.ib.host, cfg.ib.port, clientId=cfg.ib.client_id + 1, timeout=15)
        try:
            return fetch_daily_bars(ib, args.symbol, days=args.days)
        finally:
            ib.disconnect()
    raise ValueError(args.source)


def build_strategy(args):
    if args.strategy == "sma_cross":
        return make_strategy("sma_cross", fast=args.fast, slow=args.slow)
    return make_strategy(args.strategy)  # library defaults for the others


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest a strategy")
    parser.add_argument("--source", choices=["demo", "yahoo", "ibkr"], default="demo")
    parser.add_argument("--symbol", default="DEMO", help="Ticker, e.g. SPY")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="sma_cross")
    parser.add_argument("--years", type=int, default=10, help="Years of yahoo history")
    parser.add_argument("--days", type=int, default=756, help="Bars for demo/ibkr data")
    parser.add_argument("--fast", type=int, default=20, help="Fast SMA (sma_cross only)")
    parser.add_argument("--slow", type=int, default=50, help="Slow SMA (sma_cross only)")
    parser.add_argument("--cash", type=float, default=20_000)
    parser.add_argument("--seed", type=int, default=None, help="Random seed for demo data")
    parser.add_argument(
        "--stress", action="store_true",
        help="Double commissions and slippage - fragile strategies die here",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    bars = load_bars(args)
    strategy = build_strategy(args)
    result = run_backtest(
        bars, strategy,
        starting_cash=args.cash,
        cost_multiplier=2.0 if args.stress else 1.0,
    )
    if args.stress:
        print("(STRESS MODE: all trading costs doubled)\n")
    print(result.summary())

    out = f"equity_{args.symbol}_{args.strategy}.csv"
    result.equity_curve.to_csv(out)
    print(f"\nEquity curve saved to {out}")


if __name__ == "__main__":
    main()
