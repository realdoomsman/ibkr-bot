"""The league table: every strategy vs every symbol vs buy-and-hold.

This is the honest overview - one command, real data, all strategies, and the
"do nothing" benchmark on every row. Optionally writes a visual HTML report.

Examples:
    python run_compare.py
    python run_compare.py --symbols SPY QQQ NVDA --years 5
    python run_compare.py --report            # also writes report.html
"""

from __future__ import annotations

import argparse
import math

from bot.backtest import run_backtest
from bot.data import fetch_yahoo_daily
from bot.strategy import make_strategy

STRATS = {
    "sma_cross": "SMA cross (trend)",
    "rsi_reversion": "RSI reversion",
    "momentum": "Momentum",
}
DEFAULT_BASKET = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TLT", "GLD"]
TRADING_DAYS = 252


def bh_stats(bars) -> dict:
    """Buy-and-hold benchmark stats, same conventions as the backtester."""
    closes = bars["close"]
    curve = closes.iloc[1:]
    total = float(closes.iloc[-1] / bars["open"].iloc[1] - 1)
    years = len(curve) / TRADING_DAYS
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else 0.0
    dd = float(((curve - curve.cummax()) / curve.cummax()).min())
    rets = curve.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * math.sqrt(TRADING_DAYS)) if rets.std() > 0 else 0.0
    return {
        "total": 100 * total, "cagr": 100 * cagr,
        "sharpe": sharpe, "max_dd": 100 * dd, "curve": curve,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare all strategies across symbols")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_BASKET)
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--report", action="store_true", help="Also write report.html")
    args = parser.parse_args()

    beats = {name: 0 for name in STRATS}
    sections = []

    for symbol in args.symbols:
        try:
            bars = fetch_yahoo_daily(symbol, years=args.years)
        except Exception as e:
            print(f"\n{symbol}: SKIPPED ({e})")
            continue

        bh = bh_stats(bars)
        print(f"\n{symbol}  ({bars.index[0].date()} -> {bars.index[-1].date()})")
        print("-" * 74)
        print(f"{'strategy':<22}{'return':>10}{'CAGR':>8}{'sharpe':>8}{'max DD':>9}{'trades':>8}")

        curves = {}
        rows = []
        for name, label in STRATS.items():
            r = run_backtest(bars, make_strategy(name))
            beat = r.total_return_pct > bh["total"]
            if beat:
                beats[name] += 1
            print(
                f"{label:<22}{r.total_return_pct:>9.1f}%{r.cagr_pct:>7.1f}%"
                f"{r.sharpe:>8.2f}{r.max_drawdown_pct:>8.1f}%{r.num_trades:>8d}"
                + ("   <- beat B&H" if beat else "")
            )
            curves[label] = r.equity_curve
            rows.append((
                label,
                [f"{r.total_return_pct:+.1f}%", f"{r.cagr_pct:+.1f}%",
                 f"{r.sharpe:.2f}", f"{r.max_drawdown_pct:.1f}%", str(r.num_trades)],
                beat, False,
            ))

        print(
            f"{'Buy & hold':<22}{bh['total']:>9.1f}%{bh['cagr']:>7.1f}%"
            f"{bh['sharpe']:>8.2f}{bh['max_dd']:>8.1f}%{'-':>8}"
        )
        rows.append((
            "Buy & hold (benchmark)",
            [f"{bh['total']:+.1f}%", f"{bh['cagr']:+.1f}%",
             f"{bh['sharpe']:.2f}", f"{bh['max_dd']:.1f}%", "-"],
            None, True,
        ))

        if args.report:
            from bot.report import equity_chart_png

            sections.append({
                "symbol": symbol,
                "chart_png": equity_chart_png(curves, ("Buy & hold", bh["curve"])),
                "rows": rows,
            })

    n = len([s for s in args.symbols])
    print("\n" + "=" * 74)
    print("League summary - times each strategy beat buy-and-hold:")
    for name, label in STRATS.items():
        print(f"  {label:<22} {beats[name]}/{n} symbols")
    print("  (A strategy that can't beat doing nothing has no reason to trade.)")

    if args.report and sections:
        from bot.report import render_report

        path = render_report(sections, years=args.years)
        print(f"\nVisual report written to {path}")


if __name__ == "__main__":
    main()
