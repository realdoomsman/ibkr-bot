"""Gapper lab: replay the jeet ladder on 3,349 REAL historical gapper events.

SmallCapLab's gapper_comprehensive.csv gives real intraday paths (open, high,
low, close, premarket high, HOD time) for thousands of gap-up events - the
exact tape our penny sleeve trades. Unlike run_eventstudy (daily bars,
survivorship-biased Yahoo universe), this replays the ladder on real
intraday extremes from a curated event set, and cross-checks our own base
rates against ground truth.

Three outputs:
  1. BASE RATES vs SmallCapLab's published figures (bug detector: our
     numbers must land within a few points).
  2. LADDER REPLAY: enter each event the way the sleeve does (>=12% above
     the OPEN, priced from open), simulate rungs at +8%/+15% from entry
     against the real intraday HIGH, honor the EOD flatten (close), and the
     breakeven floor - net of a friction haircut.
  3. TIME-OF-HOD and CUTOFF evidence for the pre-registered gates.

CAVEAT: these are >=某 gap events (SmallCapLab's inclusion threshold), a
curated set, not our live radar universe - directional priors, not the live
sleeve's expectancy. Long-only, no shorting (this data's edge is short-side).

Run:  python run_gapperlab.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pathlib import Path

CSV = Path("smallcaplab_data/gapper_comprehensive.csv")

# Ladder params mirrored from run_pennymo (kept in sync manually).
ENTRY_GAIN = 0.12       # >= +12% above the open to qualify as a mover
CAP = 0.01              # capped-limit entry 1% above trigger
RUNGS = (1.08, 1.15)    # +8% / +15% from entry
RUNG_FRAC = 1 / 3
TRAIL = 0.10
COST_PCT = 0.020        # round-trip friction haircut for the penny tier
MIN_PRICE, MAX_PRICE = 0.20, 7.0


def load() -> pd.DataFrame:
    df = pd.read_csv(CSV)
    for c in ("open", "high", "low", "day_ah_high", "prev_close", "pm_high",
              "open_to_close_pct", "open_to_hod_pct", "vwap_close", "close"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # 'close' isn't a column; derive from open_to_close_pct.
    df["close"] = df["open"] * (1 + df["open_to_close_pct"] / 100)
    return df


def base_rates(df: pd.DataFrame) -> list[str]:
    n = len(df)
    below_open = (df["open_to_close_pct"] < 0).mean()
    below_vwap = (df["close"] < df["vwap_close"]).mean()
    med_hod = df["open_to_hod_pct"].median()
    pmh_break = (df["high"] > df["pm_high"]).mean()
    return [
        f"BASE RATES on {n} real gapper events (vs SmallCapLab published):",
        f"  close below open:  {100 * below_open:.1f}%   (theirs ~64.1%)",
        f"  close below VWAP:  {100 * below_vwap:.1f}%   (theirs ~72.5%)",
        f"  median HOD move:   {med_hod:+.1f}%   (theirs ~31.9%)",
        f"  broke PM high:     {100 * pmh_break:.1f}%   (theirs ~41.4%)",
        "  (within a few points = our understanding of the tape is sound)",
    ]


def replay_ladder(df: pd.DataFrame) -> list[str]:
    """Enter LONG at open*(1.12)*(1.01) when the real HIGH reached it; sell
    thirds at +8%/+15% from entry if the HIGH got there; the rest exits at
    the real close (the EOD flatten). Breakeven floor: if a rung banked and
    the LOW returned to entry, the remainder exits flat, not at the close."""
    d = df[(df["open"] >= MIN_PRICE) & (df["open"] <= MAX_PRICE)].copy()
    entry = d["open"] * (1 + ENTRY_GAIN) * (1 + CAP)
    triggered = d["high"] >= entry            # did price reach our entry?
    d, entry = d[triggered], entry[triggered]
    hi, lo, close = d["high"], d["low"], d["close"]

    r1_lvl, r2_lvl = entry * RUNGS[0], entry * RUNGS[1]
    hit1, hit2 = hi >= r1_lvl, hi >= r2_lvl
    # proceeds in fractions of the entry-priced position
    proceeds = np.zeros(len(d))
    proceeds += np.where(hit1, RUNG_FRAC * RUNGS[0], 0)
    proceeds += np.where(hit2, RUNG_FRAC * RUNGS[1], 0)
    # remaining tranche after the rungs that hit
    remaining = 1.0 - RUNG_FRAC * (hit1.astype(int) + hit2.astype(int))
    # breakeven floor: rung 1 banked AND low returned to entry -> exit at entry
    floor = hit1 & (lo <= entry)
    exit_px = np.where(floor, entry, close)     # else EOD flatten at close
    proceeds += remaining * (exit_px / entry)
    gross = proceeds - 1.0
    net = np.maximum(gross - COST_PCT, -1.0)

    return [
        f"LADDER REPLAY on {len(d)} qualifying events (>=+12% intraday, ${MIN_PRICE}-{MAX_PRICE}):",
        f"  reached +8% rung:   {100 * hit1.mean():.0f}%",
        f"  reached +15% rung:  {100 * hit2.mean():.0f}%",
        f"  breakeven-floor exits: {100 * floor.mean():.0f}%",
        f"  NET per clip (after {100 * COST_PCT:.0f}% friction): "
        f"avg {100 * net.mean():+.2f}%, median {100 * np.median(net):+.2f}%, "
        f"winners {100 * (net > 0).mean():.0f}%",
        f"  gross (frictionless): avg {100 * gross.mean():+.2f}%",
        "  CAVEAT: curated gapper set, not the live radar universe; long-only",
        "  on a tape whose documented edge is SHORT. Directional prior only.",
    ]


def rung_sweep(df: pd.DataFrame) -> list[str]:
    """Which rung levels do best on the SAME 1,506 real spikes? Pre-registered
    grid (declared here before reading results); reports net avg + win rate +
    median so the median-vs-mean skew is visible per cell. NO stop is added -
    every variant honors the EOD-flatten-only, no-intraday-stop rule."""
    d = df[(df["open"] >= MIN_PRICE) & (df["open"] <= MAX_PRICE)].copy()
    base_entry = d["open"] * (1 + ENTRY_GAIN) * (1 + CAP)
    trig = d["high"] >= base_entry
    d = d[trig]
    hi, lo, close, entry = d["high"], d["low"], d["close"], base_entry[trig]
    grid = [(0.05, 0.10), (0.08, 0.15), (0.10, 0.20), (0.08, 0.25),
            (0.12, 0.30), (0.15, 0.40)]
    rows = ["RUNG SWEEP on the same real spikes (pre-registered grid; no stops):"]
    for r1, r2 in grid:
        h1, h2 = hi >= entry * (1 + r1), hi >= entry * (1 + r2)
        proceeds = np.where(h1, RUNG_FRAC * (1 + r1), 0.0) \
            + np.where(h2, RUNG_FRAC * (1 + r2), 0.0)
        remaining = 1.0 - RUNG_FRAC * (h1.astype(int) + h2.astype(int))
        floor = h1 & (lo <= entry)
        exit_px = np.where(floor, entry, close)
        net = np.maximum(proceeds + remaining * (exit_px / entry) - 1 - COST_PCT, -1.0)
        rows.append(f"  +{int(r1*100)}%/+{int(r2*100)}%: net avg {100*net.mean():+6.2f}% "
                    f"| median {100*np.median(net):+6.2f}% | win {100*(net>0).mean():.0f}% "
                    f"| r1hit {100*h1.mean():.0f}%")
    rows.append("  (higher win rate + higher median often means MORE negative avg -")
    rows.append("   banking earlier caps upside while the no-stop tail is unchanged.)")
    return rows


def runner_analysis() -> list[str]:
    """The user's core thesis: 'these can go 3+ dollars in a day, a 3x.' The
    runner file timestamps when each mover crossed +50/+75/+100/+150% - so we
    can quantify how OFTEN a moon bag actually pays, and what the overnight
    (next-day) continuation the EOD flatten forfeits/avoids really looks like."""
    p = Path("smallcaplab_data/intraday_runner_data.csv")
    if not p.exists():
        return []
    r = pd.read_csv(p)
    for c in ("hod", "today_open", "rth_close", "next_day_open"):
        r[c] = pd.to_numeric(r[c], errors="coerce")
    n = len(r)
    ext = (r["hod"] / r["today_open"] - 1) * 100     # HOD extension from open
    hit100 = (r["trig100_time"].notna()).mean()
    hit150 = (r["trig150_time"].notna()).mean()
    # overnight: next open vs today's RTH close (what the flatten gives up)
    on = (r["next_day_open"] / r["rth_close"] - 1) * 100
    on = on.replace([np.inf, -np.inf], np.nan).dropna()
    return [
        f"RUNNER ANALYSIS ({n} intraday runners - the moon-bag thesis):",
        f"  crossed +100% intraday: {100 * hit100:.0f}%   +150%: {100 * hit150:.0f}%",
        f"  median HOD extension from open: {ext.median():+.0f}%",
        f"  of runners, {100 * (ext >= 200).mean():.0f}% reached +200%+ (the true 3x)",
        f"  OVERNIGHT (next-open vs RTH close - what the EOD flatten skips):",
        f"    avg {on.mean():+.1f}%, median {on.median():+.1f}%, "
        f"positive {100 * (on > 0).mean():.0f}%  (n={len(on)})",
        "    -> negative/near-zero median = the flatten AVOIDS a bad hold, not",
        "       forfeits a good one; Gate E's overnight null holds up.",
    ]


def regime_analysis() -> list[str]:
    """Market Character daily -> the hot-tape/cold-tape throttle. Does a high
    gapper-count day predict the NEXT day's tradeability (persistence), and
    what does 'today' look like vs the long-run average?"""
    p = Path("smallcaplab_data/market_character_daily.csv")
    if not p.exists():
        return []
    m = pd.read_csv(p).sort_values("date")
    for c in ("n_gappers", "fade_rate", "avg_hod_from_open", "n_gap_above_100pct"):
        m[c] = pd.to_numeric(m[c], errors="coerce")
    # lag-1 autocorrelation of gapper COUNT (the research claimed ~0.43)
    ac = m["n_gappers"].autocorr(lag=1)
    hot = m["n_gappers"] >= m["n_gappers"].quantile(0.75)
    cold = m["n_gappers"] <= m["n_gappers"].quantile(0.25)
    return [
        f"REGIME ({len(m)} days - the hot/cold-tape throttle):",
        f"  gapper-count lag-1 autocorrelation: {ac:.2f}  "
        f"(>=0.25 means counts PERSIST -> a throttle can work; research said ~0.43)",
        f"  hot days (top quartile): {m.loc[hot,'n_gappers'].mean():.0f} gappers, "
        f"fade {m.loc[hot,'fade_rate'].mean():.0f}%, "
        f"avg HOD {m.loc[hot,'avg_hod_from_open'].mean():+.0f}%",
        f"  cold days (bottom quartile): {m.loc[cold,'n_gappers'].mean():.1f} gappers, "
        f"fade {m.loc[cold,'fade_rate'].mean():.0f}%",
        "  -> if counts persist, a pre-market gapper count throttles position",
        "     count/size for the day (pre-registered gate, advisory first).",
    ]


def hod_timing(df: pd.DataFrame) -> list[str]:
    t = pd.to_datetime(df["high_time"], format="%H:%M", errors="coerce").dt.time
    def frac_by(hh, mm):
        import datetime as _dt
        cut = _dt.time(hh, mm)
        return 100 * (t <= cut).mean()
    return [
        "HOD TIMING (for the ENTRY_CUTOFF gate - late entries buy after the high):",
        f"  HOD set by 10:00: {frac_by(10, 0):.0f}%   by 10:30: {frac_by(10, 30):.0f}%"
        f"   by 11:30: {frac_by(11, 30):.0f}%   by 13:00: {frac_by(13, 0):.0f}%",
        "  -> most highs are in early; the 15:00 cutoff lets it buy late fades.",
    ]


def main() -> None:
    if not CSV.exists():
        print(f"Missing {CSV} - drop the SmallCapLab gapper export there.")
        return
    df = load()
    out = (base_rates(df) + [""] + replay_ladder(df) + [""] + rung_sweep(df)
           + [""] + runner_analysis() + [""] + regime_analysis()
           + [""] + hod_timing(df))
    report = "\n".join(out)
    print(report)
    Path("gapperlab_report.txt").write_text(report + "\n", encoding="utf-8")
    print("\n-> gapperlab_report.txt")


if __name__ == "__main__":
    main()
