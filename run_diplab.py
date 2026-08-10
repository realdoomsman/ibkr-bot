"""Diplab: does buying the DIP on a big runner work? (user hypothesis lab)

Born 2026-08-10: SCKT opened +545%, collapsed -51% intraday, then bounced
+81% off the low - the user (correctly, that day) observed a dip-buy would
have paid. This study asks whether that is a repeatable edge or a memorable
exception, on the survivorship-free event cache (delisted included).

PRE-REGISTERED GRID (declared before any results were seen):
  Universe: cached polylab events whose day high reached >= +50% over open
            (the SCKT class - big runners with real dip room)
  Entry:    first 5-min bar whose low pierces running_intraday_high * (1-R),
            R in {0.15, 0.25, 0.35, 0.50}; fill AT the retrace level;
            entries only before 15:00 ET (live cutoff)
  Exit:     the live jeet ladder from entry (1/3 at +8%, 1/3 at +15%,
            remainder at the close - EOD flatten, no stop), 2% friction
  Reported: n, avg net, median, win%, and MAX ADVERSE EXCURSION - how far
            under the entry the KNIFE kept falling before any recovery
            (the no-stop pain this strategy would actually inflict)

LAB-ONLY. Nothing here gates entries; a wiring decision belongs to a gate,
and only if a cell clears the same bar the PMH filters had to clear
(n >= 250 AND net >= +1.5%) with a mechanical story.

Run:  python run_diplab.py [--max-events 400]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from bot import polygon

log = logging.getLogger("bot.diplab")

CACHE = Path("polylab_events.csv")
OUT = Path("diplab_results.csv")
RETRACES = (0.15, 0.25, 0.35, 0.50)  # pre-registered - do not extend
MIN_RUN = 0.50    # day high >= +50% over open = "big runner"
FRICTION = 0.02
RUNGS = (1.08, 1.15)
CUTOFF = dt.time(15, 0)


def replay_dip(bars: pd.DataFrame, retrace: float) -> dict | None:
    """One event x one retrace depth. None = never dipped that far in time."""
    rth = bars[[dt.time(9, 30) <= t.time() < dt.time(16, 0)
                for t in bars["date"]]]
    if len(rth) < 12:
        return None
    run_hi, entry, entry_i = 0.0, None, None
    rows = list(rth.itertuples())
    for i, b in enumerate(rows):
        run_hi = max(run_hi, float(b.high))
        level = run_hi * (1 - retrace)
        if float(b.low) <= level and b.date.time() <= CUTOFF and run_hi > 0:
            entry, entry_i = level, i
            break
    if entry is None:
        return None
    r1, r2 = entry * RUNGS[0], entry * RUNGS[1]
    hit1 = hit2 = False
    worst = entry
    for b in rows[entry_i:]:
        worst = min(worst, float(b.low))
        if not hit1 and float(b.high) >= r1:
            hit1 = True
        if hit1 and not hit2 and float(b.high) >= r2:
            hit2 = True
    close = float(rows[-1].close)
    gross = ((r1 if hit1 else close) + (r2 if hit2 else close) + close) \
        / (3 * entry) - 1
    return {"net_pct": round(100 * (gross - FRICTION), 2),
            "mae_pct": round(100 * (worst / entry - 1), 2),
            "rung1": hit1, "rung2": hit2}


def study(max_events: int = 400) -> list[str]:
    ev = pd.read_csv(CACHE, dtype={"symbol": str})
    big = ev[ev["high"] >= ev["open"] * (1 + MIN_RUN)]
    if len(big) > max_events:
        big = big.sample(max_events, random_state=7)  # fixed seed, no cherry-pick
    lines = [f"DIPLAB - dip-buying big runners (day high >= +50% over open): "
             f"{len(big)} events of {len(ev)} cached, survivorship-free"]
    results: dict[float, list[dict]] = {r: [] for r in RETRACES}
    rows_out = []
    for n, (_, e) in enumerate(big.iterrows(), 1):
        bars = polygon.minute_bars(e["symbol"], e["day"], minutes=5,
                                   adjusted=False)
        if bars is None:
            continue
        for r in RETRACES:
            got = replay_dip(bars, r)
            if got:
                results[r].append(got)
                rows_out.append({"day": e["day"], "symbol": e["symbol"],
                                 "retrace": r, **got})
        if n % 50 == 0:
            log.info("diplab: %d/%d events replayed", n, len(big))
    for r in RETRACES:
        res = results[r]
        if not res:
            lines.append(f"  dip -{int(r * 100)}%: no entries")
            continue
        net = pd.Series([x["net_pct"] for x in res])
        mae = pd.Series([x["mae_pct"] for x in res])
        lines.append(
            f"  dip -{int(r * 100):>2}%: n={len(res):<4} net {net.mean():+6.2f}% "
            f"med {net.median():+6.2f}%  win {100 * (net > 0).mean():.0f}%  "
            f"avg-worst-drawdown-after-entry {mae.mean():.1f}%")
    if rows_out:
        pd.DataFrame(rows_out).to_csv(OUT, index=False)
        lines.append(f"  (per-event rows -> {OUT})")
    lines.append("  Wiring bar (same as PMH filters): n>=250 AND net>=+1.5% "
                 "AND a mechanical story - decided at a gate, not today.")
    return lines


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-events", type=int, default=400)
    args = ap.parse_args()
    if not polygon.available():
        raise SystemExit("POLYGON_API_KEY not set")
    if not CACHE.exists():
        raise SystemExit("no polylab_events.csv - run run_polylab.py first")
    print("\n".join(study(args.max_events)))


if __name__ == "__main__":
    main()
