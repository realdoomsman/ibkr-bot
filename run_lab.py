"""Research Lab: the fleet's always-on backtesting brain.

Every weekday evening (scheduled 18:30, after the review), the lab runs one
study from a fixed rotation and appends the results to a permanent ledger:

  Monday     event_study      - the penny ladder replayed over history, on a
                                universe that grows (and de-biases) daily via
                                the point-in-time screens archive
  Tuesday    walkforward      - the daily basket x all 3 strategies, honest
                                IS/OOS walk-forward (the number that matters:
                                stitched OOS return + WFE)
  Wednesday  ladder_sweep     - PRE-REGISTERED parameter grid for the ladder
                                (entry gain x rungs x trail). Registered
                                2026-08-04; the grid never changes between
                                gates, because a grid you keep editing until
                                it wins is how backtests lie.
  Thursday   walkforward      - the intraday basket symbols (daily-bar proxy
                                until the intraday archive is deep enough)
  Friday     digest           - the week in one page: lab results, promotion
                                ledger totals, radar base rates

THE LAB REPORTS. IT NEVER CHANGES CONFIG. Strategy changes happen at the
monthly evaluation gates, by a human reading this ledger - automation that
rewrites its own strategy nightly is the documented overfitting machine
(Bailey/Lopez de Prado; Quantopian's 888-algo R^2 < 0.025).

Outputs: lab_ledger.csv (one row per date+study, atomic) + latest_lab.txt.
Run:  python run_lab.py                # today's rotation slot
      python run_lab.py --study event_study
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path

LEDGER = Path("lab_ledger.csv")
REPORT = Path("latest_lab.txt")

# Pre-registered 2026-08-04 - do not edit between evaluation gates.
SWEEP_GRID = [
    {"entry_gain": eg, "rungs": r, "trail": tr}
    for eg in (0.10, 0.12, 0.15)
    for r in ((1.4, 1.8), (1.5, 2.0), (1.6, 2.5))
    for tr in (0.25, 0.30, 0.40)
]


def _ledger_write(date: str, study_name: str, metrics: dict) -> None:
    """One row per (date, study), replace-on-rerun, atomic."""
    rows = []
    if LEDGER.exists():
        with LEDGER.open(newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f)
                    if not (r.get("date") == date and r.get("study") == study_name)]
    rows.append({"date": date, "study": study_name,
                 "metrics": json.dumps(metrics, default=str)})
    tmp = Path("lab_ledger.csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "study", "metrics"],
                           restval="", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, LEDGER)


def _basket(kind: str) -> list[str]:
    from bot.config import load_config

    cfg = load_config()
    return list(cfg.strategy.symbols if kind == "daily" else cfg.intraday.symbols)


# ---- studies ---------------------------------------------------------------


def study_event_study() -> tuple[dict, list[str]]:
    from run_eventstudy import study, universe

    syms = universe()
    df, s = study(verbose=False, syms=syms)
    lines = [f"EVENT STUDY - {s.get('events', 0)} spike entries, "
             f"{s.get('symbols', 0)} symbols (universe {len(syms)})"]
    if s.get("events"):
        lines.append(f"  net avg {s['net_avg_pct']:+.2f}% | median {s['net_median_pct']:+.2f}% "
                     f"| winners {s['win_pct']}% | bags {s['bag_pct']}% "
                     f"(tranche {s['bag_tranche_avg_pct']}%)")
        if df is not None:
            df.to_csv("eventstudy_results.csv", index=False)
    return s, lines


def study_walkforward(kind: str) -> tuple[dict, list[str]]:
    import pandas as pd

    from bot.data import fetch_yahoo_daily
    from bot.strategy import STRATEGIES
    from bot.walkforward import walk_forward

    today = pd.Timestamp.today().normalize()
    out: dict = {"basket": kind}
    lines = [f"WALK-FORWARD ({kind} basket) - stitched OOS return / WFE / profitable windows"]
    for sym in _basket(kind):
        for strat in STRATEGIES:
            key = f"{sym}:{strat}"
            try:
                bars = fetch_yahoo_daily(sym, years=5)
                bars = bars[bars.index < today]
                r = walk_forward(bars, strat)
                out[key] = {
                    "oos_pct": round(r.stitched_oos_return_pct, 2),
                    "wfe": round(r.wf_efficiency, 2) if r.wf_efficiency is not None else None,
                    "wins": f"{r.profitable_windows}/{len(r.windows)}",
                }
                lines.append(f"  {key:<22} {out[key]['oos_pct']:+8.2f}%   "
                             f"WFE {out[key]['wfe']}   {out[key]['wins']}")
            except Exception as e:
                out[key] = {"error": str(e)[:80]}
                lines.append(f"  {key:<22} skipped ({str(e)[:50]})")
    return out, lines


def study_ladder_sweep() -> tuple[dict, list[str]]:
    from run_eventstudy import study, universe

    syms = universe()
    results = []
    for params in SWEEP_GRID:
        _, s = study(verbose=False, syms=syms, **params)
        if s.get("events", 0) >= 30:
            results.append({**params, "events": s["events"],
                            "net_avg": s["net_avg_pct"], "median": s["net_median_pct"],
                            "win": s["win_pct"], "bag": s["bag_pct"]})
    results.sort(key=lambda r: -r["net_avg"])
    lines = [f"LADDER SWEEP - {len(SWEEP_GRID)} pre-registered combos, "
             f"{len(results)} with >=30 events. Top 5 by net avg:"]
    for r in results[:5]:
        lines.append(f"  gain {r['entry_gain']:.2f} rungs {r['rungs']} trail {r['trail']:.2f}"
                     f" -> avg {r['net_avg']:+.2f}% med {r['median']:+.2f}%"
                     f" win {r['win']}% bags {r['bag']}% ({r['events']} ev)")
    lines.append("  (A grid's best cell is ALWAYS partly luck - decisions only at gates,")
    lines.append("   and only if a cell beats the live config both here AND live.)")
    return {"top": results[:10], "combos": len(SWEEP_GRID)}, lines


def study_digest() -> tuple[dict, list[str]]:
    import pandas as pd

    lines = ["WEEKLY DIGEST"]
    out: dict = {}
    try:
        led = pd.read_csv(LEDGER)
        lines.append(f"  Lab runs recorded: {len(led)} "
                     f"({', '.join(led['study'].value_counts().index[:4])})")
        out["lab_rows"] = int(len(led))
    except Exception:
        lines.append("  No lab ledger yet.")
    try:
        sb = pd.read_csv("filter_scoreboard.csv")
        out["filter_days"] = int(len(sb))
        out["actual_cum"] = round(float(sb["actual_pnl"].sum()), 2)
        out["take_cum"] = round(float(sb["take_gated_pnl"].sum()), 2)
        lines.append(f"  Promotion ledger: {len(sb)} days | actual ${out['actual_cum']:+,.2f}"
                     f" | TAKE-gated ${out['take_cum']:+,.2f}")
    except Exception:
        lines.append("  No promotion ledger yet.")
    try:
        radar = pd.read_csv("penny_radar.csv")
        f5 = pd.to_numeric(radar["fwd_5d"], errors="coerce").dropna()
        out["radar_picks"] = int(len(radar))
        if len(f5):
            out["radar_5d_avg"] = round(float(f5.mean()), 1)
            lines.append(f"  Radar: {len(radar)} picks | day-5 avg {out['radar_5d_avg']:+.1f}%"
                         f" | still-up {100 * (f5 > 0).mean():.0f}%")
        else:
            lines.append(f"  Radar: {len(radar)} picks, none resolved yet.")
    except Exception:
        lines.append("  No radar data yet.")
    return out, lines


ROTATION = {
    0: ("event_study", study_event_study),
    1: ("walkforward_daily", lambda: study_walkforward("daily")),
    2: ("ladder_sweep", study_ladder_sweep),
    3: ("walkforward_intraday", lambda: study_walkforward("intraday")),
    4: ("digest", study_digest),
}
BY_NAME = {"event_study": study_event_study,
           "walkforward_daily": lambda: study_walkforward("daily"),
           "walkforward_intraday": lambda: study_walkforward("intraday"),
           "ladder_sweep": study_ladder_sweep,
           "digest": study_digest}


def main() -> None:
    ap = argparse.ArgumentParser(description="Nightly research lab")
    ap.add_argument("--study", choices=sorted(BY_NAME), help="Override the rotation")
    args = ap.parse_args()

    today = dt.date.today()
    if args.study:
        name, fn = args.study, BY_NAME[args.study]
    else:
        name, fn = ROTATION.get(today.weekday(), ("event_study", study_event_study))

    print(f"Research Lab {today.isoformat()} - running: {name}\n")
    metrics, lines = fn()
    _ledger_write(today.isoformat(), name, metrics)

    header = [f"RESEARCH LAB  {today.isoformat()}  ({name})", "=" * 60]
    footer = ["", "The lab reports; it never changes config. Strategy decisions",
              "happen at monthly evaluation gates, by a human, from this ledger."]
    report = "\n".join(header + lines + footer)
    print(report)
    REPORT.write_text(report + "\n", encoding="utf-8")
    print(f"\n-> lab_ledger.csv ({name} row for {today.isoformat()}) + latest_lab.txt")

    # Friday digest goes to the phone (quiet priority - news, not alarms).
    if name == "digest":
        try:
            import urllib.request

            from run_watchdog import _ntfy_topic

            topic = _ntfy_topic()
            if topic:
                body = "\n".join(lines)[:3500]
                req = urllib.request.Request(
                    f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                    headers={"Title": "IBKR fleet: weekly digest",
                             "Priority": "default", "Tags": "bar_chart"})
                urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass


if __name__ == "__main__":
    main()
