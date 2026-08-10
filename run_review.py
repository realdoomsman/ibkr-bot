"""Automated trade review: what happened, what it cost, and WHOSE fault it was.

Separates every outcome into two buckets:

  PROCESS (the bot's responsibility - fixable):
    slippage vs signal price, blocked/failed orders, repairs, errors,
    unconfirmed cancels, data problems. These are engineering defects.

  MARKET (not a mistake):
    rule-following trades that lost money. A trend strategy losing in a
    choppy stretch is its documented personality, not a bug. The fix for
    market outcomes is evidence at evaluation gates - never a mid-week tweak.

Runs daily after the close (scheduled), or on demand:
    python run_review.py            # today
    python run_review.py --days 7   # the last week

Strategy changes are deliberately NOT made from these reviews. Per the
research library (RESEARCH.md): measure constantly, change strategy only at
pre-registered evaluation gates with walk-forward evidence.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
from pathlib import Path

REVIEW_LOG = Path("reviews.log")
LATEST = Path("latest_review.txt")
SLIPPAGE_WARN_BPS = 20.0


def read_csv(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def in_window(ts: str, start: dt.datetime) -> bool:
    try:
        return dt.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S") >= start
    except ValueError:
        return False


def fifo_round_trips(executions: list[dict]) -> list[dict]:
    """Pair BUY/SELL executions per symbol into round trips (FIFO)."""
    trips = []
    open_lots: dict[str, list[dict]] = {}
    for e in sorted(executions, key=lambda r: r["time"]):
        sym, qty, px = e["symbol"], int(float(e["qty"])), float(e["price"])
        if e["side"] == "BUY":
            open_lots.setdefault(sym, []).append({"qty": qty, "px": px, "time": e["time"]})
            continue
        remaining = qty
        while remaining > 0 and open_lots.get(sym):
            lot = open_lots[sym][0]
            take = min(remaining, lot["qty"])
            trips.append({
                "symbol": sym, "qty": take,
                "entry_px": lot["px"], "exit_px": px,
                "entry_time": lot["time"], "exit_time": e["time"],
                "pnl": take * (px - lot["px"]),
            })
            lot["qty"] -= take
            remaining -= take
            if lot["qty"] == 0:
                open_lots[sym].pop(0)


    return trips


def count_log_events(paths: list[str], start: dt.datetime) -> dict[str, int]:
    patterns = {
        "errors": re.compile(r" ERROR bot\."),
        "blocked": re.compile(r"BLOCKED"),
        "repairs": re.compile(r"REPAIR"),
        "cancel_unconfirmed": re.compile(r"cancel NOT fully confirmed|cancel unconfirmed"),
        "orphans_cleaned": re.compile(r"orphan"),
        "data_skips": re.compile(r"bars < warmup|No (historical|intraday) data"),
    }
    counts = {k: 0 for k in patterns}
    stamp = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = stamp.match(line)
            if not m or not in_window(m.group(1), start):
                continue
            for key, pat in patterns.items():
                if pat.search(line):
                    counts[key] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily trade review with attribution")
    parser.add_argument("--days", type=int, default=1, help="Window in days (1 = today)")
    args = parser.parse_args()

    now = dt.datetime.now()
    start = (now - dt.timedelta(days=args.days - 1)).replace(hour=0, minute=0, second=0)
    L: list[str] = []
    add = L.append

    add(f"TRADE REVIEW  {start.date()} -> {now.strftime('%Y-%m-%d %H:%M')}")
    add("=" * 64)

    # ---- what happened -----------------------------------------------------
    # FIFO over the FULL blotter, then keep trips that EXITED in the window.
    # (Clipping executions first silently dropped every round trip whose BUY
    # fell outside the window - the daily sleeve and penny moon bags would
    # never have appeared in any review or in the promotion ledger.)
    executions_all = read_csv("trades_history.csv")
    executions = [e for e in executions_all if in_window(e["time"], start)]
    trips = [t for t in fifo_round_trips(executions_all)
             if in_window(t["exit_time"][:19], start)]
    wins = [t for t in trips if t["pnl"] > 0]
    gross = sum(t["pnl"] for t in trips)

    equity = [e for e in read_csv("equity_history.csv") if in_window(e["time"], start)]
    eq_change = None
    if len(equity) >= 2:
        eq_change = float(equity[-1]["net_liq"]) - float(equity[0]["net_liq"])

    add(f"Executions: {len(executions)}   Completed round trips: {len(trips)}")
    if trips:
        add(f"Round-trip P&L: ${gross:+,.2f}   win rate {100 * len(wins) / len(trips):.0f}%"
            f"   avg ${gross / len(trips):+,.2f}/trip")
        worst = min(trips, key=lambda t: t["pnl"])
        best = max(trips, key=lambda t: t["pnl"])
        add(f"Best:  {best['symbol']} ${best['pnl']:+,.2f}   "
            f"Worst: {worst['symbol']} ${worst['pnl']:+,.2f}")
    if eq_change is not None:
        add(f"Account change over window (marked): ${eq_change:+,.2f}")

    # ---- PROCESS bucket: the bot's responsibility --------------------------
    add("")
    add("PROCESS (bot's responsibility - fixable):")
    journal = [j for j in read_csv("journal.csv") if in_window(j["time"], start)]
    entry_rows = [
        j for j in journal
        if j["kind"] in ("entry_limit", "market_order") and j["fill_price"] and j["signal_price"]
        and float(j["fill_price"]) > 0 and int(float(j["filled"] or 0)) > 0
    ]
    process_flags = 0
    if entry_rows:
        slips = []
        for j in entry_rows:
            sig, fill = float(j["signal_price"]), float(j["fill_price"])
            side = 1 if j["action"] == "BUY" else -1
            slips.append(side * (fill - sig) / sig * 1e4)
        avg_bps = sum(slips) / len(slips)
        dollars = sum(
            abs(float(j["fill_price"]) - float(j["signal_price"])) * int(float(j["filled"]))
            for j in entry_rows
        )
        flag = "  <-- ABOVE TARGET, investigate" if avg_bps > SLIPPAGE_WARN_BPS else ""
        if avg_bps > SLIPPAGE_WARN_BPS:
            process_flags += 1
        add(f"  Slippage vs signal: avg {avg_bps:+.1f} bps over {len(slips)} fills"
            f" (~${dollars:,.2f} total){flag}")
    else:
        add("  Slippage vs signal: no journaled fills in window")

    logs = count_log_events(["intraday.log", "bot.log"], start)
    for label, key in [
        ("Errors in bot code paths", "errors"),
        ("Risk-blocked orders", "blocked"),
        ("Emergency repairs (shorts covered)", "repairs"),
        ("Unconfirmed cancels", "cancel_unconfirmed"),
        ("Orphan orders cleaned", "orphans_cleaned"),
        ("Data-shortage skips", "data_skips"),
    ]:
        n = logs[key]
        flag = ""
        if key in ("errors", "repairs", "cancel_unconfirmed") and n > 0:
            flag = "  <-- investigate in logs"
            process_flags += 1
        add(f"  {label}: {n}{flag}")

    # ---- FILTER scoreboard: is the advisor earning a gate? -----------------
    add("")
    add("FILTER (advisory scoreboard - gates nothing yet):")
    advice_rows = [j for j in journal if j["kind"] == "filter_advice"]

    # Match each completed round trip to the advice given at its entry.
    # Sleeve-aware: intraday advice precedes its fill by seconds; the
    # daily bot advises after the close and fills at the NEXT open, so a
    # wider pool and window are needed (advice may predate this review
    # window entirely - reload the journal with a 5-day lookback).
    advice_pool = [
        j for j in read_csv("journal.csv")
        if j["kind"] == "filter_advice" and "p=" in j["note"]
        and in_window(j["time"], start - dt.timedelta(days=10))
    ]  # 10d lookback: multi-day trips' entries can long precede their exit

    def advice_for(trip):
        best = None
        for j in advice_pool:
            if j["symbol"] != trip["symbol"]:
                continue
            try:
                adv_t = dt.datetime.strptime(j["time"], "%Y-%m-%d %H:%M:%S")
                ent_t = dt.datetime.strptime(trip["entry_time"][:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            window = (
                dt.timedelta(days=4)          # after-close advice -> next-open fill
                if "[daily]" in j["note"]
                else dt.timedelta(minutes=15)  # intraday: advice just before entry
            )
            if dt.timedelta(minutes=-1) <= ent_t - adv_t <= window:
                best = float(j["note"].split("p=")[1].split()[0])
        return best

    all_scored = [(advice_for(t), t["pnl"]) for t in trips]

    if not advice_rows:
        add("  No filter advice recorded in window.")
    else:
        import statistics as _st

        ps = [float(j["note"].split("p=")[1].split()[0]) for j in advice_rows
              if "p=" in j["note"]]
        add(f"  Opinions issued: {len(advice_rows)}   avg p_win {(_st.mean(ps) if ps else 0):.3f}")

        scored = [(p, pnl) for p, pnl in all_scored if p is not None]
        if scored:
            med = _st.median([p for p, _ in scored])
            hi = [pnl for p, pnl in scored if p >= med]
            lo = [pnl for p, pnl in scored if p < med]
            add(f"  Scored round trips: {len(scored)}")
            if hi:
                add(f"    higher-conviction half: avg ${_st.mean(hi):+,.2f}/trip ({len(hi)})")
            if lo:
                add(f"    lower-conviction half:  avg ${_st.mean(lo):+,.2f}/trip ({len(lo)})")
            add("  (If the higher-p half doesn't beat the lower-p half over weeks,")
            add("   the filter never earns a gate. That's the deal.)")
        else:
            add("  No completed round trips matched to advice yet.")

    # ---- Promotion ledger: the permanent record a gate decision reads ------
    # One row per review: actual P&L vs two counterfactuals - "filter as
    # gatekeeper" (only entries it rated TAKE) and "filter as veto" (block
    # only its SKIPs). Trips with no matched advice pass through both (a
    # gate can't judge what it never saw). Weeks of these rows ARE the
    # promotion evidence; a single day means nothing.
    try:
        import json as _json

        fmeta = _json.loads(Path("models/trade_filter_meta.json").read_text(encoding="utf-8"))
        take_thr = float(fmeta.get("take_threshold", 0.5))
        skip_thr = float(fmeta.get("skip_threshold", 0.35))
    except Exception:
        take_thr, skip_thr = 0.5, 0.35
    n_scored = sum(1 for p, _ in all_scored if p is not None)
    take_gated = sum(pnl for p, pnl in all_scored if p is None or p >= take_thr)
    veto_gated = sum(pnl for p, pnl in all_scored if p is None or p >= skip_thr)

    def _rewrite_ledger(path: str, fields: list[str], keep, new_rows: list[dict]) -> None:
        """Replace-by-date, atomic. Append-only ledgers double-count the
        moment the review is ever run twice in a day or with --days>1 -
        and a corrupted ledger IS corrupted promotion evidence."""
        import os

        rows = [r for r in read_csv(path) if keep(r)] + new_rows
        tmp = Path(path + ".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, restval="", extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        os.replace(tmp, path)

    if args.days != 1:
        add("  Ledger: not written (multi-day windows would double-count days).")
    else:
        today_iso = now.date().isoformat()
        try:
            _rewrite_ledger(
                "filter_scoreboard.csv",
                ["date", "days", "trips", "scored", "actual_pnl",
                 "take_gated_pnl", "veto_gated_pnl", "take_thr", "skip_thr"],
                lambda r: r.get("date") != today_iso,
                [{"date": today_iso, "days": 1, "trips": len(trips), "scored": n_scored,
                  "actual_pnl": round(gross, 2), "take_gated_pnl": round(take_gated, 2),
                  "veto_gated_pnl": round(veto_gated, 2),
                  "take_thr": take_thr, "skip_thr": skip_thr}],
            )
            # Per-trip ledger: daily totals cannot support significance tests
            # later; individual scored trips can (win rates, CIs, SKIP-bucket
            # analysis - where a veto filter's value actually lives).
            trip_rows = [
                {"date": today_iso, "symbol": t["symbol"],
                 "p": round(p, 4),
                 "verdict": "TAKE" if p >= take_thr else ("SKIP" if p < skip_thr else "NEUTRAL"),
                 "pnl": round(t["pnl"], 2), "qty": t["qty"],
                 "entry_time": t["entry_time"], "exit_time": t["exit_time"]}
                for (p, _), t in zip(all_scored, trips) if p is not None
            ]
            _rewrite_ledger(
                "filter_trades.csv",
                ["date", "symbol", "p", "verdict", "pnl", "qty", "entry_time", "exit_time"],
                lambda r: r.get("date") != today_iso,
                trip_rows,
            )
            add(f"  Ledger: actual ${gross:+,.2f} | if TAKE-only ${take_gated:+,.2f} | "
                f"if SKIPs vetoed ${veto_gated:+,.2f}  ({len(trip_rows)} scored trips "
                f"-> filter_trades.csv)")
        except Exception:
            add("  Ledger write failed (see logs).")

    # ---- MARKET bucket: not mistakes ---------------------------------------
    add("")
    add("MARKET (strategy personality - not bugs):")
    losers = [t for t in trips if t["pnl"] <= 0]
    add(f"  Rule-following losing trips: {len(losers)}"
        f" (${sum(t['pnl'] for t in losers):+,.2f})")
    add("  A trend rule losing in chop is its documented cost of doing business;")
    add("  the whipsaw count above is what buys participation in real trends.")
    try:
        from bot.data import fetch_yahoo_daily

        spy = fetch_yahoo_daily("SPY", years=1)["close"]
        n = min(args.days, len(spy) - 1)
        spy_ret = 100 * (float(spy.iloc[-1]) / float(spy.iloc[-1 - n]) - 1)
        add(f"  Context: SPY moved {spy_ret:+.2f}% over ~the same window.")
    except Exception:
        add("  Context: SPY comparison unavailable (no fresh data).")

    # ---- verdict -----------------------------------------------------------
    add("")
    add("VERDICT:")
    if process_flags == 0:
        add("  Process: CLEAN - every fill and control behaved. Green day for the")
        add("  bot regardless of P&L.")
    else:
        add(f"  Process: {process_flags} item(s) flagged above are the bot's to fix.")
    add("  Strategy: judged ONLY at evaluation gates (monthly, walk-forward),")
    add("  never from a single window. See RESEARCH.md - reacting to short-term")
    add("  P&L is the documented retail failure mode.")

    report = "\n".join(L)
    print(report)
    LATEST.write_text(report, encoding="utf-8")
    with REVIEW_LOG.open("a", encoding="utf-8") as f:
        f.write(report + "\n\n" + "#" * 64 + "\n\n")

    # Daily phone summary (the 16:50 --days 1 run only): "did it trade today?"
    # must never require an RDP session. Quiet priority - news, not alarms.
    if args.days == 1:
        try:
            import urllib.request

            from run_watchdog import _ntfy_topic

            topic = _ntfy_topic()
            if topic:
                if trips:
                    body = (f"{len(trips)} round trip(s), P&L ${gross:+,.2f}, "
                            f"win rate {100 * len(wins) / len(trips):.0f}%. ")
                elif executions:
                    body = (f"{len(executions)} execution(s), no completed "
                            f"round trips (positions may span days). ")
                else:
                    body = "No trades today. "
                body += ("Process CLEAN." if process_flags == 0
                         else f"{process_flags} process item(s) flagged - "
                              f"see latest_review.txt on the VM.")
                req = urllib.request.Request(
                    f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                    headers={"Title": f"Daily review {now.date().isoformat()}",
                             "Priority": "default", "Tags": "clipboard"})
                urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass  # the phone summary must never fail the review itself


if __name__ == "__main__":
    main()
