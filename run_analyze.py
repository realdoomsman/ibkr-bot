"""On-demand research note for one symbol: price stats, real headlines, and
(if ANTHROPIC_API_KEY is set) a structured AI read - advisory only.

Example:
    python run_analyze.py --symbol NVDA
"""

from __future__ import annotations

import argparse
import logging

from bot import ai_analyst
from bot.data import fetch_headlines, fetch_yahoo_daily


def main() -> None:
    parser = argparse.ArgumentParser(description="Research note for a symbol")
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--years", type=int, default=2)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    symbol = args.symbol.upper()

    bars = fetch_yahoo_daily(symbol, years=args.years)
    close = bars["close"]
    print(f"Research note: {symbol}  (as of {bars.index[-1].date()})")
    print("-" * 56)
    print(f"  Last close:   {close.iloc[-1]:.2f}")
    for label, n in [("1 week", 5), ("1 month", 21), ("3 months", 63), ("1 year", 252)]:
        if len(close) > n:
            print(f"  {label + ' return:':<16}{100 * (close.iloc[-1] / close.iloc[-n - 1] - 1):>+8.1f}%")

    headlines = fetch_headlines(symbol)
    if headlines:
        print("\nRecent headlines (Yahoo Finance):")
        for h in headlines:
            print(f"  - {h}")
    else:
        print("\n(No headlines available right now.)")

    if not ai_analyst.is_enabled():
        print(
            "\nAI read: disabled. Set ANTHROPIC_API_KEY to enable a structured "
            "Claude analysis of the stats and headlines above."
        )
        return

    print("\nAsking Claude for a structured read...")
    analysis = ai_analyst.analyze_symbol(symbol, bars, headlines=headlines)
    if analysis is None:
        print("AI analysis unavailable (see log for the reason).")
        return
    print("-" * 56)
    print(f"  Sentiment:   {analysis.sentiment}  (confidence {analysis.confidence:.2f})")
    print(f"  Summary:     {analysis.summary}")
    if analysis.key_risks:
        print("  Key risks:")
        for r in analysis.key_risks:
            print(f"    - {r}")
    print(
        "\n  Reminder: this is one model's structured opinion of PAST data and"
        "\n  headlines. It predicts nothing. Advisory input only."
    )


if __name__ == "__main__":
    main()
