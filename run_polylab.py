"""Polylab: the gapper replay with NO survivorship bias (Polygon history).

Every prior event study had a blind spot: Yahoo only remembers tickers that
survived. The delisted corpses - the worst possible outcomes of buying penny
spikes - were invisible, which flatters every number. Polygon's grouped
daily aggregates include them, so this study replays the live jeet ladder
over the ENTIRE market's gappers, dead or alive, on RAW (unadjusted) prices.

Event (matches live entry): open $0.20-7.00, day high >= open*1.12 (the
mover trigger was reachable), dollar volume >= $3M. Replay (daily-bar
approximation of the ladder): enter at open*1.12, sell 1/3 at +8% if the
high reached it, 1/3 at +15%, remainder at the close (EOD flatten; the
intraday trail can't be modeled from OHLC - conservative). Friction 2%.

Cohorts mirror the SmallCapLab findings this must corroborate or destroy:
  ALL events            (their baseline:      -4.44% avg, 70% win)
  broke premarket high  (their PMH cohort:    ~0.00%, 83% win)
  ... with PMH set < 8AM (the wired filter:   +2.50%, 90% win)
PM highs come from Polygon extended-hours minute bars per event (1 call
each - the expensive part; cached forever in polylab_events.csv).
Sequencing caveat (same as the lab's other studies): daily bars can't
prove the PMH break happened BEFORE the +12% trigger - approximation.

Runs Mondays in the nightly lab (falls back to the old Yahoo event study
without a key). Manual:  python run_polylab.py --sessions 10
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
from pathlib import Path

import pandas as pd

from bot import polygon

log = logging.getLogger("bot.polylab")

CACHE = Path("polylab_events.csv")
FRICTION = 0.02
LO, HI = 0.20, 7.00
MIN_DVOL = 3e6
GAIN = 0.12
RUNGS = (1.08, 1.15)


def replay(open_px: float, high: float, close: float) -> dict:
    """The live ladder, approximated from one daily bar."""
    entry = open_px * (1 + GAIN)
    r1, r2 = entry * RUNGS[0], entry * RUNGS[1]
    hit1, hit2 = high >= r1, high >= r2
    gross = ((r1 if hit1 else close) + (r2 if hit2 else close) + close) \
        / (3 * entry) - 1
    return {"net_pct": round(100 * (gross - FRICTION), 2),
            "rung1": hit1, "rung2": hit2}


def scan_session(day: str) -> list[dict] | None:
    """All qualifying gapper events for one session, replayed + PMH-tagged.
    None = no market data (holiday/outage); [] = real session, zero events."""
    g = polygon.grouped_daily(day, adjusted=False)
    if g is None:
        return None
    g = g[[bool(s.isalpha() and len(s) <= 5) for s in g["symbol"].astype(str)]]
    g = g[(g["open"] >= LO) & (g["open"] <= HI)
          & (g["high"] >= g["open"] * (1 + GAIN))
          & (g["volume"] * g["vwap"] >= MIN_DVOL)]
    events = []
    for _, r in g.iterrows():
        ev = {"day": day, "symbol": r["symbol"],
              "open": round(float(r["open"]), 4),
              "high": round(float(r["high"]), 4),
              "close": round(float(r["close"]), 4),
              "dvol_m": round(float(r["volume"] * r["vwap"]) / 1e6, 1),
              "pmh": None, "pmh_time": None, "pmh_status": "failed"}
        ev.update(replay(ev["open"], ev["high"], ev["close"]))
        try:
            # adjusted=False is LOAD-BEARING (review finding): the daily bars
            # above are raw; adjusted PM bars come back on a different price
            # scale for anything that split after the event day - which in
            # this death-bound tier is exactly the corpse cohort. A cross-
            # scale 'high >= pmh' silently ejects them from the PMH cohorts.
            bars = polygon.minute_bars(r["symbol"], day, minutes=5,
                                       adjusted=False)
            if bars is not None:
                pm = bars[[t.time() < dt.time(9, 30) for t in bars["date"]]]
                if len(pm):
                    i = pm["high"].idxmax()
                    ev["pmh"] = round(float(pm.loc[i, "high"]), 4)
                    ev["pmh_time"] = pm.loc[i, "date"].strftime("%H:%M")
                    ev["pmh_status"] = "ok"
                else:
                    ev["pmh_status"] = "empty"  # genuinely no premarket tape
            # bars None = fetch failed -> status stays "failed": a transient
            # outage must be distinguishable from no-PM-trading forever
            # (review finding: cached days are never re-scanned).
        except Exception:
            pass
        events.append(ev)
    return events


def build_events(sessions: int) -> pd.DataFrame:
    """Walk back through real sessions, scanning any not already cached.
    The cache only ever GROWS - each nightly run deepens the history."""
    prev = pd.read_csv(CACHE, dtype={"symbol": str}) if CACHE.exists() \
        else pd.DataFrame()
    have = set(prev["day"]) if len(prev) else set()
    d = dt.date.today() - dt.timedelta(days=1)
    found, attempts = 0, 0
    while found < sessions and attempts < sessions * 2 + 15:
        attempts += 1
        if d.weekday() < 5:
            day = d.isoformat()
            if day in have:
                found += 1
            else:
                evs = scan_session(day)
                if evs is not None:  # None = holiday/outage, not a session
                    found += 1
                    log.info("polylab %s: %d gapper events", day, len(evs))
                    if evs:
                        prev = pd.concat([prev, pd.DataFrame(evs)],
                                         ignore_index=True)
                        # checkpoint per session: an hour-long backfill must
                        # survive interruption with its progress intact
                        tmp = CACHE.with_suffix(".csv.tmp")
                        prev.to_csv(tmp, index=False)
                        os.replace(tmp, CACHE)
        d -= dt.timedelta(days=1)
    return prev


def cohort(df: pd.DataFrame, label: str) -> tuple[dict, str]:
    if not len(df):
        return {}, f"  {label:<26} (no events)"
    net = pd.to_numeric(df["net_pct"], errors="coerce").dropna()
    m = {"n": int(len(net)), "net_avg": round(float(net.mean()), 2),
         "median": round(float(net.median()), 2),
         "win": round(100 * float((net > 0).mean()))}
    return m, (f"  {label:<26} n={m['n']:<5} net {m['net_avg']:+6.2f}%  "
               f"med {m['median']:+6.2f}%  win {m['win']}%")


def study(sessions: int = 90) -> tuple[dict, list[str]]:
    ev = build_events(sessions)
    lines = [f"POLYLAB - survivorship-FREE gapper replay ({ev['day'].nunique() if len(ev) else 0} "
             f"sessions, raw prices, delisted included)"]
    if not len(ev):
        return {"events": 0}, lines + ["  no events collected"]
    # Rows whose PMH fetch FAILED are unusable for any cohort (unknowable
    # which side of the split they'd land) - excluded from ALL three so the
    # cohorts stay comparable; the count is reported, never hidden.
    if "pmh_status" not in ev.columns:
        ev["pmh_status"] = "ok"  # pre-fix cache rows (purged, but be safe)
    failed = int((ev["pmh_status"] == "failed").sum())
    ev = ev[ev["pmh_status"] != "failed"]
    broke = ev[pd.to_numeric(ev["pmh"], errors="coerce").notna()
               & (ev["high"] >= pd.to_numeric(ev["pmh"], errors="coerce"))]
    early = broke[broke["pmh_time"].astype(str) < "08:00"]
    out: dict = {"events": int(len(ev)), "pmh_fetch_failed": failed}
    for label, df, key in (("ALL events", ev, "all"),
                           ("broke premarket high", broke, "pmh_break"),
                           ("...and PMH set <8AM", early, "early_pmh")):
        m, line = cohort(df, label)
        out[key] = m
        lines.append(line)
    if failed:
        lines.append(f"  ({failed} events excluded: PM-bar fetch failed - retried "
                     f"on no run, visible here so the gap is never silent)")
    lines.append("  (targets from SmallCapLab history: -4.44% / ~0.00% / +2.50%)")
    return out, lines


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=90)
    ap.add_argument("--lo", type=float, default=None,
                    help="alternate price band low (user 2026-08-11: test $2-300)")
    ap.add_argument("--hi", type=float, default=None)
    ap.add_argument("--gain", type=float, default=None,
                    help="alternate entry trigger (2026-08-27 trigger sweep: "
                         "does a lower %%-over-open beat the 12%% default?)")
    args = ap.parse_args()
    if not polygon.available():
        raise SystemExit("POLYGON_API_KEY not set (env var or polygon_key.txt)")
    global LO, HI, CACHE, GAIN
    if args.lo is not None and args.hi is not None:
        # Alternate band = its own cache; never contaminate the penny study.
        LO, HI = args.lo, args.hi
        CACHE = Path(f"polylab_events_{int(args.lo)}_{int(args.hi)}.csv")
    if args.gain is not None:
        GAIN = args.gain
        CACHE = Path(f"polylab_events_g{int(args.gain * 100)}"
                     f"_{int(LO)}_{int(HI)}.csv")
    _, lines = study(args.sessions)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
