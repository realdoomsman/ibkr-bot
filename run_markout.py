"""Markout rig: grade every fill against the REAL tape (Polygon ticks).

Two questions no journal row could answer until we had tick history:

1. TIMING - after we trade, which way does the tape go? Markout at +1/10/
   30/60s, signed by side: positive = the tape kept moving our way (bought
   and it rose / sold and it fell); a consistently NEGATIVE 10-60s markout
   on entries means we buy exhaustion tops.
2. PAPER-FILL HONESTY - IBKR paper fills at top-of-book fantasy prices on
   thin names. tape_gap_bps compares our fill to the first real print at
   the same moment: a BUY consistently "filling" below the actual tape is
   free money that will NOT exist live. This number is the paper-to-live
   haircut the gate must apply.

Timing caveat: t0 is the journal stamp (written seconds after the fill
confirms), not the exchange fill time - markouts are shifted a few seconds
late. Fine for 10-60s reads; take the +1s column as approximate.

Runs automatically before every nightly lab (run_lab imports markout_today);
manual:  python run_markout.py [--day 2026-08-07]
Output: markouts.csv (replace-by-day, atomic) + a one-line summary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from bot import polygon

log = logging.getLogger("bot.markout")

NY = ZoneInfo("America/New_York")
OUT = Path("markouts.csv")
JOURNAL = Path("journal.csv")
DELTAS = (1, 10, 30, 60)
MAX_FILLS = 60  # per day - well above any real day, guards a runaway journal


def fills_for_day(day: str) -> pd.DataFrame:
    j = pd.read_csv(JOURNAL, dtype={"symbol": str})
    j = j[(j["time"].astype(str).str.startswith(day))
          & (pd.to_numeric(j["filled"], errors="coerce").fillna(0) > 0)
          & (pd.to_numeric(j["fill_price"], errors="coerce").fillna(0) > 0)]
    if len(j) > MAX_FILLS:
        # Keep the LATEST fills (the EOD-flatten exits are the ones this rig
        # exists to grade) and say so - a silent cap reads as full coverage.
        log.warning("markout: %d fills > cap %d - grading the latest %d only",
                    len(j), MAX_FILLS, MAX_FILLS)
        j = j.tail(MAX_FILLS)
    return j


def markout_row(sym: str, action: str, fill_px: float, t0: dt.datetime) -> dict:
    side = 1 if action.upper() == "BUY" else -1
    row: dict = {"symbol": sym, "action": action.upper(),
                 "time": t0.strftime("%H:%M:%S"), "fill_px": fill_px}
    tape0, off0 = polygon.trade_at(sym, t0)
    if tape0:
        # negative for a BUY = we "filled" below the real tape = paper fantasy
        row["tape_gap_bps"] = round(side * (fill_px - tape0) / tape0 * 1e4, 1)
        row["tape0_offset_s"] = round(off0, 1) if off0 is not None else None
    for d in DELTAS:
        px, off = polygon.trade_at(sym, t0 + dt.timedelta(seconds=d))
        # a print landing >5x the horizon late measures a different regime
        if px and off is not None and off <= max(5.0, d * 5):
            row[f"mo_{d}s_bps"] = round(side * (px - fill_px) / fill_px * 1e4, 1)
    return row


def markout_today(day: str | None = None) -> str | None:
    """Compute markouts for one day's fills; returns a summary line or None.
    Never raises - the lab calls this as a best-effort pre-step."""
    try:
        if not polygon.available() or not JOURNAL.exists():
            return None
        day = day or dt.date.today().isoformat()
        fills = fills_for_day(day)
        if not len(fills):
            return None
        rows = []
        for _, f in fills.iterrows():
            try:
                t0 = dt.datetime.strptime(str(f["time"]), "%Y-%m-%d %H:%M:%S") \
                    .replace(tzinfo=NY)
                rows.append({"day": day,
                             **markout_row(str(f["symbol"]), str(f["action"]),
                                           float(f["fill_price"]), t0)})
            except Exception:
                log.debug("markout failed for a fill", exc_info=True)
        if not rows:
            return None
        df = pd.DataFrame(rows)
        prev = pd.read_csv(OUT) if OUT.exists() else pd.DataFrame()
        if len(prev):
            prev = prev[prev["day"] != day]
        tmp = OUT.with_suffix(".csv.tmp")
        pd.concat([prev, df], ignore_index=True).to_csv(tmp, index=False)
        os.replace(tmp, OUT)

        bits = [f"MARKOUTS {day}: {len(df)} fills"]
        for act in ("BUY", "SELL"):
            sub = df[df["action"] == act]
            if not len(sub):
                continue
            # Report every horizon that HAS data (review finding: the old
            # ternary emitted a bare 'buy' token and dropped a resolved mean
            # whenever only one horizon printed in time).
            parts = []
            for d_s in (10, 60):
                m = pd.to_numeric(sub.get(f"mo_{d_s}s_bps"),
                                  errors="coerce").dropna()
                if len(m):
                    parts.append(f"+{d_s}s {m.mean():+.0f}bps")
            if parts:
                bits.append(f"{act.lower()} " + " ".join(parts))
        gap = pd.to_numeric(df.get("tape_gap_bps"), errors="coerce").dropna()
        if len(gap):
            bits.append(f"paper-vs-tape {gap.mean():+.0f}bps")
        return " | ".join(bits)
    except Exception:
        log.debug("markout_today failed", exc_info=True)
        return None


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None, help="ISO date (default today)")
    args = ap.parse_args()
    line = markout_today(args.day)
    print(line or "no fills / no key / no journal for that day")


if __name__ == "__main__":
    main()
