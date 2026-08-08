"""Stock screener: lower-priced, high-dollar-volume names, ranked by momentum.

This is a DATA SCREEN, not a recommendation engine. It finds candidates that
match objective criteria and prints the numbers that matter - including the
uncomfortable ones (volatility, drawdown). "Upside" cannot be screened for;
recent momentum and high volatility cut both directions.

Extra honesty for this corner of the market: cheap + heavily-traded is the
natural habitat of pump-and-dump promotions. A stock being on this list is
a reason to RESEARCH it (run_analyze.py, run_backtest.py), never to buy it.

Run:  python run_screener.py
      python run_screener.py --max-price 15 --min-dollar-vol 100 --top 10
"""

from __future__ import annotations

import argparse
import math

import pandas as pd

from bot.data import fetch_yahoo_daily

TRADING_DAYS = 252


def candidate_symbols(max_price: float, limit: int = 200) -> list[str]:
    """Pull high-activity US candidates from Yahoo's screener endpoints."""
    import yfinance as yf

    symbols: list[str] = []

    # Preferred: a custom query (price cap + volume floor server-side).
    try:
        q = yf.EquityQuery(
            "and",
            [
                yf.EquityQuery("lt", ["intradayprice", max_price]),
                yf.EquityQuery("gt", ["dayvolume", 5_000_000]),
                yf.EquityQuery("eq", ["region", "us"]),
            ],
        )
        res = yf.screen(q, sortField="dayvolume", sortAsc=False, size=limit)
        symbols = [item["symbol"] for item in res.get("quotes", []) if "symbol" in item]
    except Exception:
        pass

    # Add the big-mover lists in BOTH directions - "big price changes" is a
    # symmetric property, and yesterday's gainer list is tomorrow's loser list.
    for preset in ("most_actives", "day_gainers", "day_losers", "small_cap_gainers"):
        try:
            res = yf.screen(preset, size=100)
            symbols += [i["symbol"] for i in res.get("quotes", []) if "symbol" in i]
        except Exception:
            continue

    # Dedupe, drop non-common-stock tickers (units/warrants/funds often have
    # suffixes or carets), keep order.
    seen, out = set(), []
    for s in symbols:
        s = s.upper()
        if s in seen or not s.isalpha() or len(s) > 5:
            continue
        seen.add(s)
        out.append(s)
    return out


def score_symbol(symbol: str) -> dict | None:
    """Objective stats from 2 years of daily bars. None if data is unusable."""
    try:
        bars = fetch_yahoo_daily(symbol, years=2)
    except Exception:
        return None
    if len(bars) < 130:  # need ~6 months of history to say anything
        return None

    close = bars["close"]
    last = float(close.iloc[-1])
    dollar_vol = float((close * bars["volume"]).tail(21).mean())
    rets = close.pct_change().dropna()

    def mom(n: int) -> float | None:
        return 100 * (last / float(close.iloc[-n - 1]) - 1) if len(close) > n else None

    running_max = close.cummax()
    max_dd_2y = 100 * float(((close - running_max) / running_max).min())
    off_high = 100 * (last / float(close.max()) - 1)

    # "Big price changes", measured: last 3 months of daily returns.
    r3m = rets.tail(63)
    big_moves = int((r3m.abs() >= 0.10).sum())          # days that moved >= 10%
    best_day = 100 * float(r3m.max())
    worst_day = 100 * float(r3m.min())
    # Avg intraday range as a rough cost-of-trading proxy: wide-ranging cheap
    # stocks also tend to have wide spreads that eat round trips.
    day_range = 100 * float(((bars["high"] - bars["low"]) / bars["close"]).tail(21).mean())

    return {
        "symbol": symbol,
        "price": last,
        "dollar_vol_m": dollar_vol / 1e6,
        "mom_1m": mom(21),
        "mom_3m": mom(63),
        "mom_6m": mom(126),
        "ann_vol": 100 * float(rets.tail(126).std()) * math.sqrt(TRADING_DAYS),
        "max_dd_2y": max_dd_2y,
        "off_high": off_high,
        "big_moves_3m": big_moves,
        "best_day": best_day,
        "worst_day": worst_day,
        "day_range": day_range,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Low-price / high-volume momentum screen")
    parser.add_argument("--max-price", type=float, default=20.0, help="Max share price ($)")
    parser.add_argument("--min-dollar-vol", type=float, default=50.0,
                        help="Min avg daily dollar volume ($ millions)")
    parser.add_argument("--top", type=int, default=15, help="Rows to show")
    parser.add_argument("--max-candidates", type=int, default=60,
                        help="Cap on symbols to fetch history for")
    parser.add_argument("--penny", action="store_true",
                        help="Penny-stock mode: price <= $5, $-vol >= $5M, ranked by big daily moves")
    parser.add_argument("--rank", choices=["mom_3m", "big_moves_3m", "ann_vol"], default=None,
                        help="Ranking column (default: mom_3m, or big_moves_3m in --penny mode)")
    args = parser.parse_args()

    if args.penny:
        args.max_price = min(args.max_price, 5.0)
        args.min_dollar_vol = min(args.min_dollar_vol, 5.0)
    rank_col = args.rank or ("big_moves_3m" if args.penny else "mom_3m")

    print("Pulling candidates from Yahoo activity screens...")
    pool = candidate_symbols(args.max_price)
    if not pool:
        raise SystemExit("Screener endpoints returned nothing - try again later.")
    print(f"  {len(pool)} raw candidates; computing stats for up to {args.max_candidates}...")

    rows = []
    for symbol in pool[: args.max_candidates]:
        stats = score_symbol(symbol)
        if stats is None:
            continue
        if stats["price"] > args.max_price or stats["dollar_vol_m"] < args.min_dollar_vol:
            continue
        rows.append(stats)

    if not rows:
        raise SystemExit(
            "No symbols passed the filters. Loosen --max-price or --min-dollar-vol."
        )

    df = pd.DataFrame(rows).sort_values(rank_col, ascending=False).head(args.top)

    print(f"\nScreen: price <= ${args.max_price:.2f}, avg $-volume >= "
          f"${args.min_dollar_vol:.0f}M/day, ranked by {rank_col}")
    print("-" * 100)
    print(f"{'sym':<6}{'price':>7}{'$vol/d':>8}{'3m':>8}{'ann vol':>9}"
          f"{'>=10% days':>11}{'best day':>10}{'worst day':>10}{'2y maxDD':>10}{'range':>7}")
    for _, r in df.iterrows():
        def f(v, suffix="%"):
            return f"{v:+.0f}{suffix}" if v is not None else "n/a"
        print(
            f"{r['symbol']:<6}{r['price']:>7.2f}{r['dollar_vol_m']:>7.0f}M"
            f"{f(r['mom_3m']):>8}{r['ann_vol']:>8.0f}%"
            f"{r['big_moves_3m']:>11d}{f(r['best_day']):>10}{f(r['worst_day']):>10}"
            f"{r['max_dd_2y']:>9.0f}%{r['day_range']:>6.1f}%"
        )
    print("-" * 100)
    print(
        "How to read this honestly:\n"
        "  - '>=10% days' / 'best day' / 'worst day' are the same property. Every name\n"
        "    with monster up-days has matching down-days; the columns prove it per row.\n"
        "  - 'range' is the average intraday swing - a rough proxy for what a round\n"
        "    trip costs you in spread and slippage. At 5%+ the house edge is enormous.\n"
        "  - Sub-$1 prices add delisting/reverse-split risk on top of everything else.\n"
        "  - This corner of the market is where pump-and-dump promotions live. A stock\n"
        "    appearing here is a reason to RESEARCH it, never a reason to buy it:\n"
        "      python run_analyze.py --symbol XYZ\n"
        "      python run_backtest.py --source yahoo --symbol XYZ --strategy momentum\n"
        "  Nothing here is a recommendation."
    )

    out = "screen_results.csv"
    pd.DataFrame(rows).sort_values(rank_col, ascending=False).to_csv(out, index=False)
    print(f"\nFull results saved to {out}")


if __name__ == "__main__":
    main()
