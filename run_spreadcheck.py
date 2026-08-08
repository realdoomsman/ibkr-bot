"""Spread-meter calibration: our EDGE estimate vs REAL NBBO quotes.

The EDGE estimator (bot/scanner.edge_spread_pct) guesses the spread from
OHLC candles alone because until now we had no quote history. Polygon's
v3 quotes ARE the truth - so this script grades the guesser: for each
symbol it computes the EDGE estimate exactly the way the live engine does
(5-min bars, ~2-3 sessions) and compares it to the median quoted NBBO
spread sampled at 13 half-hour marks across the session.

What matters is not the raw error but TIER AGREEMENT: the engine only uses
the estimate through spread_tier() (full <=0.75% / half <=1.5% / reject).
If the tiers agree, the meter is doing its job; where they disagree, the
boundaries need moving before any gate hardens them into entry rules.

Run:  python run_spreadcheck.py                      # radar names + controls
      python run_spreadcheck.py --day 2026-08-07 GPUS WFF AAPL
Output: printed table + spreadcheck.csv (append; one row per symbol-day).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from bot import polygon
from bot.scanner import edge_spread_pct, spread_tier

log = logging.getLogger("bot.spreadcheck")

OUT = Path("spreadcheck.csv")
CONTROLS = ["AAPL", "SOFI", "SIRI"]  # liquid names with known-tight spreads


def last_weekday(today: dt.date | None = None) -> str:
    d = today or dt.date.today()
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def radar_symbols(limit: int = 12) -> list[str]:
    """Most recently captured radar names - the population we actually trade."""
    try:
        radar = pd.read_csv("penny_radar.csv", dtype={"symbol": str})
        recent = sorted(radar["date"].unique())[-2:]
        return list(dict.fromkeys(
            radar.loc[radar["date"].isin(recent), "symbol"]))[:limit]
    except Exception:
        return []


def check_symbol(symbol: str, day: str) -> dict | None:
    """One symbol-day: EDGE estimate (live-engine style) vs NBBO truth."""
    d = dt.date.fromisoformat(day)
    bars = polygon.minute_bars(symbol, day, minutes=5,
                               start=(d - dt.timedelta(days=4)).isoformat())
    est = None
    n_bars = 0
    if bars is not None:
        rth = bars[[dt.time(9, 30) <= t.time() < dt.time(16, 0)
                    for t in bars["date"]]]
        n_bars = len(rth)
        est = edge_spread_pct(rth)
    truth, n_q = polygon.quoted_spread_pct(symbol, day)
    if est is None and truth is None:
        return None
    return {"day": day, "symbol": symbol,
            "edge_pct": est, "edge_bars": n_bars,
            "nbbo_pct": truth, "nbbo_samples": n_q,
            "edge_tier": spread_tier(est), "nbbo_tier": spread_tier(truth),
            "tiers_agree": (spread_tier(est) == spread_tier(truth)
                            if est is not None and truth is not None else None)}


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", help="default: recent radar + controls")
    ap.add_argument("--day", default=last_weekday())
    args = ap.parse_args()

    if not polygon.available():
        raise SystemExit("POLYGON_API_KEY not set (env var or polygon_key.txt)")

    symbols = args.symbols or (radar_symbols() + CONTROLS)
    rows = [r for s in symbols if (r := check_symbol(s, args.day))]
    if not rows:
        raise SystemExit(f"no data for {symbols} on {args.day}")

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))

    scored = df.dropna(subset=["edge_pct", "nbbo_pct"])
    if len(scored):
        err = (scored["edge_pct"] - scored["nbbo_pct"]).abs()
        agree = scored["tiers_agree"].mean()
        print(f"\n{len(scored)} symbols scored | median abs error "
              f"{err.median():.3f} pct-pts | tier agreement {agree:.0%}")

    header = not OUT.exists()
    df.to_csv(OUT, mode="a", header=header, index=False)
    print(f"appended to {OUT}")


if __name__ == "__main__":
    main()
