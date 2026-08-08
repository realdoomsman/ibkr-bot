"""Portfolio backtest: one account trading a whole basket, plus a year-by-year
regime table against buy-and-holding the benchmark.

Examples:
    python run_portfolio.py
    python run_portfolio.py --strategy momentum --symbols SPY QQQ GLD TLT
"""

from __future__ import annotations

import argparse

import pandas as pd

from bot.data import fetch_yahoo_daily
from bot.portfolio import run_portfolio_backtest
from bot.strategy import STRATEGIES, make_strategy

DEFAULT_BASKET = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TLT", "GLD"]


def benchmark_yearly(symbol: str, years: int, index: pd.DatetimeIndex) -> pd.Series:
    closes = fetch_yahoo_daily(symbol, years=years)["close"]
    # Clip to the portfolio's exact window so a truncated basket calendar
    # never gets compared against a longer benchmark year.
    closes = closes[(closes.index >= index[0]) & (closes.index <= index[-1])]
    if closes.empty:
        return pd.Series(dtype=float)
    year_end = closes.resample("YE").last()
    prev = year_end.shift(1)
    # Anchor the first year only if the benchmark actually covers the
    # portfolio start; otherwise leave it NaN so the partial year prints '-'.
    if closes.index[0] <= index[0] + pd.Timedelta(days=7):
        prev.iloc[0] = float(closes.iloc[0])
    return (100 * (year_end / prev - 1)).dropna().rename(lambda ts: ts.year)


def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio-level backtest")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_BASKET)
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="sma_cross")
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--cash", type=float, default=20_000)
    parser.add_argument("--benchmark", default="SPY")
    args = parser.parse_args()

    bars = {}
    for s in args.symbols:
        try:
            bars[s] = fetch_yahoo_daily(s, years=args.years)
        except Exception as e:
            print(f"{s}: skipped ({e})")

    strategy = make_strategy(args.strategy)
    result = run_portfolio_backtest(bars, strategy, starting_cash=args.cash)

    print(f"Strategy: {args.strategy} (equal-weight across {len(bars)} symbols)\n")
    print(result.summary())

    bench = benchmark_yearly(args.benchmark, args.years, result.equity_curve.index)
    print(f"\nYear-by-year vs buy-and-hold {args.benchmark}:")
    print("-" * 44)
    print(f"{'year':<8}{'portfolio':>12}{args.benchmark + ' B&H':>14}")
    for year, ret in result.yearly_returns_pct.items():
        b = bench.get(year)
        b_txt = f"{b:>+12.1f}%" if b is not None else f"{'-':>13}"
        marker = "  <-" if b is not None and ret > b else ""
        print(f"{year:<8}{ret:>+11.1f}%{b_txt}{marker}")
    print(
        "\nThe regime table is the point: a strategy that only wins in one kind"
        "\nof market (calm bull years) will eventually meet the other kind."
    )

    result.equity_curve.to_csv("equity_portfolio.csv")
    print("\nEquity curve saved to equity_portfolio.csv")


if __name__ == "__main__":
    main()
