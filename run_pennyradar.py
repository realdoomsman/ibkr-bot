"""Penny Radar: watch the spike-tier movers and MEASURE what happens next.

The trading baskets keep their liquidity floors (below them, simulated fills
are fiction). But watching costs nothing and measuring settles arguments:
every morning this logs the wildest low-priced movers, then fills in each
past pick's forward returns (+1, +5, +20 trading days) as the future arrives.

After a few weeks, penny_radar.csv IS the answer to "are these spikes worth
chasing?" - computed from your own watchlist, not from anyone's opinion.

Also feeds the top names into the news watcher (news_extra.txt) so their
headlines appear in your feed.

Run:  python run_pennyradar.py                 # daily capture + update
      python run_pennyradar.py --add BJDX      # force-track a specific name
Scheduled 8:20 AM weekdays.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("bot.pennyradar")

RADAR_CSV = Path("penny_radar.csv")
NEWS_EXTRA = Path("news_extra.txt")
MAX_PRICE = 5.0
MIN_DVOL_M = 1.0          # watch-only floor - deliberately tiny
TOP_N = 8
COLS = ["date", "symbol", "captured", "price", "dollar_vol_m", "ann_vol", "best_day",
        "mom_1m", "max_dd_2y", "sec_flags", "fwd_1d", "fwd_5d", "fwd_20d", "status"]


def save_radar(radar: pd.DataFrame) -> None:
    """Atomic write - two processes touch this file (radar task + pennymo's
    mid-session refresh); a torn write would orphan every tracked pick."""
    import os

    tmp = RADAR_CSV.with_suffix(".csv.tmp")
    radar.to_csv(tmp, index=False)
    os.replace(tmp, RADAR_CSV)


def load_radar() -> pd.DataFrame:
    if RADAR_CSV.exists():
        return pd.read_csv(RADAR_CSV, dtype={"symbol": str})
    return pd.DataFrame(columns=COLS)


def capture_today(radar: pd.DataFrame, extra: list[str]) -> pd.DataFrame:
    from run_screener import candidate_symbols, score_symbol

    today = dt.date.today().isoformat()
    already = set(radar.loc[radar["date"] == today, "symbol"])

    picks: list[dict] = []
    try:
        pool = candidate_symbols(max_price=MAX_PRICE)
    except Exception as e:
        log.warning("screener unavailable (%s) - manual adds only", e)
        pool = []
    from bot.config import BLOCKED_SYMBOLS

    scored = []
    for sym in list(dict.fromkeys([s.upper() for s in extra] + pool))[:70]:
        if sym in BLOCKED_SYMBOLS:
            continue  # no ETFs, no gambling stocks (user directives 2026-08-05)
        row = score_symbol(sym)
        if row is None:
            if sym in extra:
                log.warning("%s: no scoreable data", sym)
            continue
        forced = row["symbol"] in extra
        if forced or (row["price"] <= MAX_PRICE and row["dollar_vol_m"] >= MIN_DVOL_M):
            scored.append((forced, row))
    scored.sort(key=lambda fr: (not fr[0], -(fr[1]["big_moves_3m"] or 0)))

    for forced, row in scored[: TOP_N + len(extra)]:
        if row["symbol"] in already:
            continue
        try:
            from bot.edgar import dilution_flags, is_china_based

            if is_china_based(row["symbol"]):
                log.info("RADAR skip %s: China/HK-based company (user directive "
                         "2026-08-05)", row["symbol"])
                continue
            sec_flags = dilution_flags(row["symbol"])
        except Exception:
            sec_flags = ""
        pick = {
            "date": today, "symbol": row["symbol"],
            "captured": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "sec_flags": sec_flags,
            "price": round(row["price"], 4),
            "dollar_vol_m": round(row["dollar_vol_m"], 2),
            "ann_vol": round(row["ann_vol"], 1),
            "best_day": round(row["best_day"], 1),
            "mom_1m": round(row["mom_1m"], 1) if row["mom_1m"] is not None else None,
            "max_dd_2y": round(row["max_dd_2y"], 1),
            "fwd_1d": None, "fwd_5d": None, "fwd_20d": None, "status": "tracking",
        }
        picks.append(pick)
        log.info("RADAR +%s: $%.2f, %.0f%% vol, best day %+.0f%%%s",
                 row["symbol"], row["price"], row["ann_vol"], row["best_day"],
                 f"  DILUTION FLAGS: {sec_flags}" if sec_flags else "")
        # Advisory Penny Brain score, once enough picks have resolved to
        # train one (run_pennybrain). Logged AND ledgered so every
        # prediction gets graded against the outcome later - never a gate.
        try:
            from run_pennybrain import score_pick

            p = score_pick(pick)
            if p is not None:
                log.info("BRAIN %s: P(still up in 5d) = %.2f", row["symbol"], p)
                import csv as _csv

                pred = Path("penny_predictions.csv")
                fresh = not pred.exists() or pred.stat().st_size == 0
                seen = set()
                if not fresh:
                    with pred.open(newline="", encoding="utf-8") as f:
                        seen = {(r.get("date"), r.get("symbol"))
                                for r in _csv.DictReader(f)}
                if (today, row["symbol"]) not in seen:  # one prediction per pick
                    with pred.open("a", newline="", encoding="utf-8") as f:
                        w = _csv.DictWriter(f, fieldnames=["date", "symbol", "captured", "p"],
                                            restval="", extrasaction="ignore")
                        if fresh:
                            w.writeheader()
                        w.writerow({"date": today, "symbol": row["symbol"],
                                    "captured": pick["captured"], "p": round(p, 4)})
        except Exception:
            log.debug("penny brain unavailable", exc_info=True)
    return pd.concat([radar, pd.DataFrame(picks)], ignore_index=True) if picks else radar


def update_forward_returns(radar: pd.DataFrame) -> pd.DataFrame:
    """Fill each pick's forward returns as the future arrives.

    Anchored at the first price actually tradeable AFTER the pick existed:
      - pre-open captures (the 8:20 task): the pick day's OPEN
      - intraday/after-close captures (pennymo's refresh): the NEXT
        session's open - anchoring those at the pick day's open would credit
        the pick with hours of run-up that happened BEFORE it was made,
        poisoning both the scoreboard and the Penny Brain's labels.
    Legacy rows without a capture time are treated as post-open (correct for
    the rows that existed when this was written).

    A transient fetch failure just retries tomorrow; a pick is only marked
    delisted-suspect when its price history has verifiably STOPPED.
    """
    from bot.data import fetch_yahoo_daily

    today = pd.Timestamp.today().normalize()
    for i, row in radar.iterrows():
        if row["status"] not in ("tracking",) or pd.notna(row["fwd_20d"]):
            continue
        try:
            bars = fetch_yahoo_daily(row["symbol"], years=1)
        except Exception:
            log.debug("%s: fetch failed - will retry next run", row["symbol"])
            continue
        rec_date = pd.Timestamp(row["date"])
        cap = str(row.get("captured") or "")
        pre_open = len(cap) >= 16 and cap[:10] == str(row["date"]) and cap[11:16] < "09:30"
        pos = bars.index.searchsorted(rec_date, side="left" if pre_open else "right")
        if pos >= len(bars):
            if (today - bars.index[-1]).days > 10:
                radar.loc[i, "status"] = "no_data(delisted?)"
            continue
        base = float(bars["open"].iloc[pos])
        if not np.isfinite(base) or base <= 0:
            base = float(row["price"])
        for col, off in (("fwd_1d", 0), ("fwd_5d", 4), ("fwd_20d", 19)):
            if pd.isna(row[col]) and pos + off < len(bars):
                radar.loc[i, col] = round(
                    100 * (float(bars["close"].iloc[pos + off]) / base - 1), 1
                )
        if pd.notna(radar.loc[i, "fwd_20d"]):
            radar.loc[i, "status"] = "complete"
        elif (today - bars.index[-1]).days > 10:
            radar.loc[i, "status"] = "no_data(delisted?)"  # stopped trading mid-window
    return radar


def print_scoreboard(radar: pd.DataFrame) -> None:
    print(f"\nPenny Radar: {len(radar)} picks tracked since inception")
    print("  (returns measured pick-day open -> session close: day 1 / 5 / 20)")
    for col, label in (("fwd_1d", "day 1"), ("fwd_5d", "day 5"), ("fwd_20d", "day 20")):
        vals = pd.to_numeric(radar[col], errors="coerce").dropna()
        if len(vals):
            pos = 100 * (vals > 0).mean()
            print(f"  {label:>8}: avg {vals.mean():+6.1f}%   median {vals.median():+6.1f}%   "
                  f"positive {pos:.0f}%   (n={len(vals)})")
    dead = (radar["status"] == "no_data(delisted?)").sum()
    if dead:
        print(f"  possibly delisted since pick: {dead}")
    if not len(radar):
        print("  (first picks appear after the next market-day capture)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Penny-mover radar (watch-only)")
    parser.add_argument("--add", nargs="*", default=[], help="Force-track specific symbols")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("pennyradar.log", encoding="utf-8")],
    )

    radar = load_radar()
    radar = capture_today(radar, [s.upper() for s in args.add])
    radar = update_forward_returns(radar)
    save_radar(radar)

    # Feed the freshest picks to the news watcher.
    recent = radar[radar["status"] == "tracking"]["symbol"].drop_duplicates().tail(5)
    NEWS_EXTRA.write_text("\n".join(recent) + "\n", encoding="utf-8")

    print_scoreboard(radar)
    print(f"\nData: {RADAR_CSV} | news watcher now also follows: {', '.join(recent) if len(recent) else '-'}")


if __name__ == "__main__":
    main()
