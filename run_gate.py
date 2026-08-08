"""Evaluation gate: the day the accumulated evidence decides.

Reads EVERYTHING the fleet has recorded - round trips, the filter's
promotion ledger, radar outcomes, brain grades, lab results, watchdog
history - and scores it against gate_criteria.yaml, which was written
BEFORE the evidence window opened and must never be edited between gates.

Output: PASS / FAIL / INSUFFICIENT DATA per criterion, in plain language,
to gate_report.txt. This script only REPORTS - promotions, demotions, and
any real-money discussion happen by a human reading this report.

Run at gates (first one: 2026-09-04), or any time for a progress preview:
    python run_gate.py
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import yaml


def _read_csv(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def main() -> None:
    crit = yaml.safe_load(Path("gate_criteria.yaml").read_text(encoding="utf-8"))
    win_start = str(crit["evidence_window"]["start"])
    today = dt.date.today().isoformat()
    L: list[str] = []
    add = L.append
    add(f"EVALUATION GATE PREVIEW  {today}   (evidence window since {win_start})")
    add("=" * 68)
    add("Criteria pre-registered 2026-08-07 - never edited after data existed.")

    # ---- sleeves: round trips from the review's per-trip machinery --------
    add("")
    add("SLEEVES (need >= %d trips each, net > $%d, <%d%% process errors):"
        % (crit["sleeve_promotion"]["min_round_trips"],
           crit["sleeve_promotion"]["min_net_pnl_usd"],
           100 * crit["sleeve_promotion"]["max_process_error_share"]))
    trips_by_sleeve: dict[str, list[float]] = {}
    try:
        from run_review import fifo_round_trips

        execs = [e for e in _read_csv("trades_history.csv")
                 if e.get("time", "") >= win_start]
        trips = fifo_round_trips(execs)
        journal = [j for j in _read_csv("journal.csv") if j.get("time", "") >= win_start]
        import re

        tags = [(j["symbol"], j["time"], m.group(1)) for j in journal
                if j.get("action") == "BUY"
                and (m := re.search(r"\[(\w+)\]", j.get("note", "") or ""))]
        for t in trips:
            sleeve = "untagged"
            best = ""
            for sym, tt, s in tags:
                if sym == t["symbol"] and tt <= t["entry_time"][:19] and tt > best:
                    sleeve, best = s, tt
            trips_by_sleeve.setdefault(sleeve, []).append(t["pnl"])
    except Exception as e:
        add(f"  (trip data unavailable: {e})")
    total_trips = sum(len(v) for v in trips_by_sleeve.values())
    if trips_by_sleeve:
        for sleeve, pnls in sorted(trips_by_sleeve.items()):
            n, net = len(pnls), sum(pnls)
            enough = n >= crit["sleeve_promotion"]["min_round_trips"]
            pos = net > crit["sleeve_promotion"]["min_net_pnl_usd"]
            verdict = ("PASS" if enough and pos
                       else "FAIL (losing)" if enough else f"INSUFFICIENT ({n} trips)")
            add(f"  {sleeve:<12} {n:>4} trips  net ${net:+10,.2f}   -> {verdict}")
    else:
        add("  no round trips recorded in the window yet")

    # ---- ML filter --------------------------------------------------------
    add("")
    fc = crit["filter_promotion"]
    add(f"ML FILTER (needs >= {fc['min_scored_trips']} scored trips over "
        f">= {fc['min_trading_days']} days, gated > actual, vetoes negative):")
    sb = [r for r in _read_csv("filter_scoreboard.csv") if r.get("date", "") >= win_start]
    ft = [r for r in _read_csv("filter_trades.csv") if r.get("date", "") >= win_start]
    if sb:
        actual = sum(_f(r["actual_pnl"]) for r in sb)
        gated = sum(_f(r["take_gated_pnl"]) for r in sb)
        scored = sum(int(_f(r.get("scored"), 0)) for r in sb)
        skips = [_f(r["pnl"]) for r in ft if r.get("verdict") == "SKIP"]
        add(f"  {len(sb)} days | scored trips {scored} | actual ${actual:+,.2f} "
            f"vs TAKE-gated ${gated:+,.2f}")
        if skips:
            add(f"  SKIP bucket: {len(skips)} trips, net ${sum(skips):+,.2f} "
                f"(must be negative for promotion)")
        enough = scored >= fc["min_scored_trips"] and len(sb) >= fc["min_trading_days"]
        if not enough:
            add("  -> INSUFFICIENT DATA: stays advisory")
        elif gated > actual and (not skips or sum(skips) < 0):
            add("  -> PASSES the pre-registered bar: promotion is on the gate agenda")
        else:
            add("  -> FAILS: stays advisory (or gets retired if it keeps failing)")
    else:
        add("  no scoreboard days in the window yet")

    # ---- penny brain + radar ---------------------------------------------
    add("")
    bc = crit["brain_promotion"]
    add(f"PENNY BRAIN (needs >= {bc['min_resolved_predictions']} resolved "
        f"predictions, KEEPS beats FADES by {bc['keeps_group_must_beat_fades_by_pct']}pp):")
    try:
        import pandas as pd

        preds = pd.read_csv("penny_predictions.csv").drop_duplicates(["date", "symbol"])
        radar = pd.read_csv("penny_radar.csv")
        m = preds.merge(radar[["date", "symbol", "fwd_5d"]], on=["date", "symbol"])
        m = m.dropna(subset=["fwd_5d"])
        if len(m) == 0:
            add("  no resolved predictions yet")
        else:
            hi = pd.to_numeric(m[m["p"] >= 0.5]["fwd_5d"], errors="coerce")
            lo = pd.to_numeric(m[m["p"] < 0.5]["fwd_5d"], errors="coerce")
            add(f"  {len(m)} resolved | KEEPS avg {hi.mean():+.1f}% (n={len(hi)}) "
                f"vs FADES avg {lo.mean():+.1f}% (n={len(lo)})")
            if len(m) < bc["min_resolved_predictions"]:
                add("  -> INSUFFICIENT DATA")
            elif len(hi) and len(lo) and (hi.mean() - lo.mean()) >= bc["keeps_group_must_beat_fades_by_pct"]:
                add("  -> PASSES the pre-registered bar")
            else:
                add("  -> FAILS: stays a commentator")
    except Exception:
        add("  no prediction data yet")

    # ---- operations -------------------------------------------------------
    add("")
    add("OPERATIONS (any blackout day in the window disqualifies live talk):")
    blackout_days: set[str] = set()
    try:
        for line in Path("watchdog.log").read_text(encoding="utf-8", errors="ignore").splitlines():
            if "TWS UNREACHABLE" in line and line[:10] >= win_start:
                blackout_days.add(line[:10])
    except Exception:
        pass
    add(f"  TWS-unreachable days since {win_start}: "
        f"{len(blackout_days)}{' ' + str(sorted(blackout_days)) if blackout_days else ''}")

    # ---- the big one ------------------------------------------------------
    add("")
    lm = crit["live_money_discussion"]
    add("REAL-MONEY DISCUSSION requires ALL of:")
    add(f"  [{'x' if total_trips >= lm['min_total_round_trips'] else ' '}] "
        f">= {lm['min_total_round_trips']} total round trips (have {total_trips})")
    passing = sum(1 for pnls in trips_by_sleeve.values()
                  if len(pnls) >= crit["sleeve_promotion"]["min_round_trips"]
                  and sum(pnls) > 0)
    add(f"  [{'x' if passing >= lm['min_sleeves_passing_promotion'] else ' '}] "
        f">= {lm['min_sleeves_passing_promotion']} sleeve(s) passing promotion (have {passing})")
    add(f"  [{'x' if len(blackout_days) <= lm['max_blackout_days_in_window'] else ' '}] "
        f"<= {lm['max_blackout_days_in_window']} blackout days (have {len(blackout_days)})")
    add(f"  [?] real-time market data active >= {lm['requires_realtime_data_weeks']} weeks "
        f"(check subscription status manually)")
    add("")
    add("This report DECIDES NOTHING. A human reads it at the gate.")

    report = "\n".join(L)
    print(report)
    Path("gate_report.txt").write_text(report + "\n", encoding="utf-8")
    print("\n-> gate_report.txt")


if __name__ == "__main__":
    main()
