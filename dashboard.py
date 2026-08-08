"""Local performance dashboard for the trading bots.

A small Flask site at http://localhost:8050 that polls the IBKR paper
account once a minute (its own client id, read-only requests) and shows:

  - account equity, cash, day P&L, equity curve, positions, blotter
  - P&L attribution per SLEEVE (which bot is earning/losing)
  - the AI trade filter's report card: model facts, today's opinions, and
    the cumulative promotion ledger (actual P&L vs what gating would have
    done) - the evidence that decides if the ML ever gets veto power
  - the Penny Lab: radar picks with forward returns, live DCA ladders,
    and the Penny Brain's status and latest predictions
  - last nightly review, inline

It never places orders. If TWS is down it shows the last snapshot and says
so - the dashboard degrades to stale, never to wrong. Everything beyond the
account panels is read from the fleet's own CSV/JSON files on disk.

Run:  python dashboard.py     (or let the "IBKR Dashboard" scheduled task)
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import html
import json
import re
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template_string

POLL_SECONDS = 60
PORT = 8050
EQUITY_CSV = Path("equity_history.csv")
TRADES_CSV = Path("trades_history.csv")

app = Flask(__name__)
_lock = threading.Lock()
_state: dict = {"ok": False, "error": "starting up...", "updated": None}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _append_csv(path: Path, row: dict) -> None:
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if not exists:
            w.writeheader()
        w.writerow(row)


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# IBKR polling (read-only)
# ---------------------------------------------------------------------------


def poll_once() -> dict:
    from ib_async import IB

    ib = IB()
    ib.connect("127.0.0.1", 7497, clientId=45, timeout=10)
    try:
        acct = {}
        for v in ib.accountSummary():
            if v.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower") and v.currency == "USD":
                acct[v.tag] = float(v.value)
        prev_day = None
        for v in ib.accountValues():
            if v.tag == "PreviousDayEquityWithLoanValue" and v.currency == "USD":
                try:
                    prev_day = float(v.value)
                except ValueError:
                    pass

        positions = []
        for item in ib.portfolio():
            positions.append({
                "symbol": item.contract.symbol,
                "qty": int(item.position),
                "avg_cost": round(item.averageCost, 4),
                "price": round(item.marketPrice, 4),
                "value": round(item.marketValue, 2),
                "unrealized": round(item.unrealizedPNL, 2),
                "realized": round(item.realizedPNL, 2),
            })

        fills = ib.reqExecutions()
    finally:
        ib.disconnect()

    # Accumulate the blotter across days (dedupe on execId).
    known = {r["exec_id"] for r in _read_csv(TRADES_CSV)}
    for f in fills:
        e = f.execution
        if e.execId in known:
            continue
        _append_csv(TRADES_CSV, {
            "exec_id": e.execId,
            "time": e.time.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": f.contract.symbol,
            "side": "BUY" if e.side == "BOT" else "SELL",
            "qty": int(e.shares),
            "price": e.price,
        })

    net_liq = acct.get("NetLiquidation", 0.0)
    unrealized = round(sum(p["unrealized"] for p in positions), 2)
    now = dt.datetime.now()

    # One equity snapshot per poll -> the equity curve grows over time.
    _append_csv(EQUITY_CSV, {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "net_liq": round(net_liq, 2),
        "cash": round(acct.get("TotalCashValue", 0.0), 2),
        "unrealized": unrealized,
    })

    equity_history = _read_csv(EQUITY_CSV)[-2000:]
    trades = _read_csv(TRADES_CSV)

    today = now.strftime("%Y-%m-%d")
    trades_today = [t for t in trades if t["time"].startswith(today)]
    if prev_day and prev_day > 0:
        day_base = prev_day
    else:
        first_today = next((r for r in equity_history if r["time"].startswith(today)), None)
        day_base = float(first_today["net_liq"]) if first_today else net_liq

    return {
        "ok": True,
        "error": None,
        "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "net_liq": round(net_liq, 2),
        "cash": round(acct.get("TotalCashValue", 0.0), 2),
        "buying_power": round(acct.get("BuyingPower", 0.0), 2),
        "day_pnl": round(net_liq - day_base, 2),
        "day_pnl_pct": round(100 * (net_liq / day_base - 1), 3) if day_base else 0.0,
        "unrealized": unrealized,
        "positions": sorted(positions, key=lambda p: p["symbol"]),
        "trades": list(reversed(trades[-40:])),
        "trades_today": len(trades_today),
        "equity_history": [[r["time"], float(r["net_liq"])] for r in equity_history],
    }


def poller() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    while True:
        global _state
        try:
            snap = poll_once()
            with _lock:
                _state = snap
        except Exception as e:
            with _lock:
                _state = {**_state, "ok": False, "error": f"TWS unreachable: {e}"}
        time.sleep(POLL_SECONDS)


# ---------------------------------------------------------------------------
# File-based intelligence panels (no IBKR needed; rebuilt on each request)
# ---------------------------------------------------------------------------


def _sleeve_attribution(journal: list[dict]) -> list[dict]:
    """P&L per sleeve: FIFO round trips from the blotter, each attributed to
    the sleeve whose journal entry most recently preceded the trip's entry."""
    from run_review import fifo_round_trips

    trips = fifo_round_trips(_read_csv(TRADES_CSV))
    tags = []
    for j in journal:
        m = re.search(r"\[(\w+)\]", j.get("note", "") or "")
        if m and j.get("action") == "BUY":
            tags.append((j["symbol"], j["time"], m.group(1)))
    agg: dict[str, dict] = {}
    for t in trips:
        sleeve, best_t = "untagged", ""
        for sym, tt, s in tags:
            if sym == t["symbol"] and tt <= t["entry_time"][:19] and tt > best_t:
                sleeve, best_t = s, tt
        a = agg.setdefault(sleeve, {"sleeve": sleeve, "trips": 0, "wins": 0, "pnl": 0.0})
        a["trips"] += 1
        a["wins"] += 1 if t["pnl"] > 0 else 0
        a["pnl"] += t["pnl"]
    out = []
    for a in agg.values():
        out.append({**a, "pnl": round(a["pnl"], 2),
                    "win_pct": round(100 * a["wins"] / a["trips"]) if a["trips"] else 0})
    return sorted(out, key=lambda a: -abs(a["pnl"]))


def file_stats() -> dict:
    out: dict = {}
    journal = _read_csv(Path("journal.csv"))
    today = dt.date.today().isoformat()

    try:
        out["sleeves"] = _sleeve_attribution(journal)
    except Exception:
        out["sleeves"] = []

    # ---- AI filter report card + promotion ledger
    try:
        meta = json.loads(Path("models/trade_filter_meta.json").read_text(encoding="utf-8"))
        ab = meta.get("ablation", {})
        out["filter_meta"] = {
            "trained": (meta.get("trained") or "")[:16].replace("T", " "),
            "samples": meta.get("samples"),
            "feature_set": meta.get("feature_set"),
            "take_thr": meta.get("take_threshold"),
            "skip_thr": meta.get("skip_threshold"),
            "auc_base": (ab.get("base") or {}).get("auc"),
            "auc_expanded": (ab.get("expanded") or {}).get("auc"),
            "oos_baseline_ev": meta.get("oos_baseline_ev_pct"),
            "oos_take_ev": meta.get("oos_take05_ev_pct"),
        }
    except Exception:
        out["filter_meta"] = None
    try:
        cum = cum_t = cum_v = 0.0
        ledger = []
        for r in _read_csv(Path("filter_scoreboard.csv")):
            cum += _f(r.get("actual_pnl"))
            cum_t += _f(r.get("take_gated_pnl"))
            cum_v += _f(r.get("veto_gated_pnl"))
            ledger.append([r.get("date", ""), round(cum, 2), round(cum_t, 2), round(cum_v, 2)])
        out["ledger"] = ledger[-90:]
    except Exception:
        out["ledger"] = []
    try:
        advice = []
        for j in journal:
            if j.get("kind") == "filter_advice" and j.get("time", "").startswith(today):
                m = re.search(r"\[(\w+)\] p=([\d.]+)\s+(\w+)", j.get("note", "") or "")
                if m:
                    advice.append({"time": j["time"][11:16], "symbol": j["symbol"],
                                   "sleeve": m.group(1), "p": float(m.group(2)),
                                   "verdict": m.group(3)})
        out["advice_today"] = advice[-14:]
    except Exception:
        out["advice_today"] = []

    # ---- Penny Lab
    try:
        radar = _read_csv(Path("penny_radar.csv"))
        out["radar"] = [{k: r.get(k, "") for k in
                         ("date", "symbol", "price", "dollar_vol_m", "best_day",
                          "fwd_1d", "fwd_5d", "fwd_20d", "status")}
                        for r in radar[-12:]][::-1]
        stats = {}
        for col in ("fwd_1d", "fwd_5d", "fwd_20d"):
            vals = [_f(r[col], None) for r in radar if r.get(col) not in ("", None)]
            vals = [v for v in vals if v is not None]
            if vals:
                stats[col] = {"avg": round(sum(vals) / len(vals), 1),
                              "pos": round(100 * sum(1 for v in vals if v > 0) / len(vals)),
                              "n": len(vals)}
        out["penny_stats"] = stats
    except Exception:
        out["radar"], out["penny_stats"] = [], {}
    try:
        st = json.loads(Path("pennymo_state.json").read_text(encoding="utf-8"))
        out["ladders"] = [
            {"symbol": s, "entry": p.get("entry"), "initial": p.get("initial"),
             "rungs": f"{sum(bool(x) for x in p.get('rungs', []))}/{len(p.get('rungs', []))}",
             "hw": p.get("hw"), "since": p.get("date")}
            for s, p in (st.get("positions") or {}).items()
        ]
    except Exception:
        out["ladders"] = []
    try:
        bmeta = json.loads(Path("models/penny_brain_meta.json").read_text(encoding="utf-8"))
        out["brain"] = {"trained": (bmeta.get("trained") or "")[:16].replace("T", " "),
                        "auc": bmeta.get("holdout_auc"),
                        "n_train": bmeta.get("n_train")}
    except Exception:
        out["brain"] = None
    try:
        out["brain_preds"] = _read_csv(Path("penny_predictions.csv"))[-10:][::-1]
    except Exception:
        out["brain_preds"] = []

    # ---- research lab ledger + last report
    try:
        out["lab"] = [{"date": r.get("date", ""), "study": r.get("study", ""),
                       "metrics": (r.get("metrics", "") or "")[:200]}
                      for r in _read_csv(Path("lab_ledger.csv"))[-8:]][::-1]
    except Exception:
        out["lab"] = []
    try:
        out["lab_report"] = html.escape(
            Path("latest_lab.txt").read_text(encoding="utf-8")[-4000:])
    except Exception:
        out["lab_report"] = None

    # ---- watchdog health (health.json, written every 30 min in RTH)
    try:
        out["health"] = json.loads(Path("health.json").read_text(encoding="utf-8"))
    except Exception:
        out["health"] = None

    # ---- last nightly review, verbatim
    try:
        txt = Path("latest_review.txt").read_text(encoding="utf-8")
        out["review"] = html.escape(txt[-6000:])
    except Exception:
        out["review"] = None
    return out


# ---------------------------------------------------------------------------
# Web app
# ---------------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bot dashboard</title>
<style>
  :root {
    --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --sec:#52514e;
    --muted:#898781; --grid:#e1e0d9; --blue:#2a78d6; --up:#006300; --down:#d03b3b;
    --amber:#a05a00; --violet:#7048b8;
  }
  body { background:var(--page); color:var(--ink); margin:0;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .wrap { max-width:1050px; margin:0 auto; padding:24px 18px 60px; }
  h1 { font-size:20px; margin:0; }
  .status { color:var(--muted); font-size:12px; margin:4px 0 18px; }
  .status .err { color:var(--down); font-weight:600; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
  .tile { background:var(--surface); border:1px solid rgba(11,11,11,.10);
          border-radius:10px; padding:12px 14px; }
  .tile .l { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
  .tile .v { font-size:22px; margin-top:4px; font-variant-numeric:tabular-nums; }
  .card { background:var(--surface); border:1px solid rgba(11,11,11,.10);
          border-radius:10px; padding:16px; margin-top:16px; }
  .card h2 { font-size:14px; margin:0 0 10px; color:var(--sec); }
  .sub { color:var(--muted); font-size:12px; margin:-6px 0 10px; }
  table { width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }
  th { color:var(--muted); font-weight:500; font-size:11px; text-transform:uppercase;
       letter-spacing:.04em; text-align:right; border-bottom:1px solid var(--grid); padding:4px 8px; }
  th:first-child, td:first-child { text-align:left; }
  td { padding:5px 8px; border-bottom:1px solid var(--grid); text-align:right; }
  tr:last-child td { border-bottom:none; }
  .pos { color:var(--up); } .neg { color:var(--down); }
  .note { color:var(--sec); font-size:12px; margin-top:16px; line-height:1.5; }
  .legend { font-size:11px; color:var(--muted); margin-bottom:6px; }
  .legend span { margin-right:14px; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:4px; }
  pre { font-size:11.5px; line-height:1.45; overflow-x:auto; background:var(--page);
        border:1px solid var(--grid); border-radius:8px; padding:12px; margin:0; }
  details summary { cursor:pointer; color:var(--sec); font-size:13px; }
  svg text { font-family:inherit; }
</style></head><body><div class="wrap">
  <h1>Paper trading dashboard <span style="color:var(--muted);font-weight:400">DUR638895</span></h1>
  <div class="status" id="status">loading...</div>
  <div id="health"></div>
  <div class="tiles" id="tiles"></div>
  <div class="card"><h2>Account equity</h2><div id="chart">collecting snapshots...</div></div>
  <div class="card"><h2>Sleeve performance</h2>
    <div class="sub">completed round trips, attributed to the bot that opened them</div>
    <div id="sleeves">-</div></div>
  <div class="card"><h2>AI trade filter</h2>
    <div class="sub">advisory only - it earns veto power on this ledger, or never</div>
    <div id="filter">-</div></div>
  <div class="card"><h2>Penny lab</h2>
    <div class="sub">radar picks, live DCA ladders, and the Penny Brain</div>
    <div id="penny">-</div></div>
  <div class="card"><h2>Research lab</h2>
    <div class="sub">the always-on backtesting brain - one study every evening; reports only, never self-tunes</div>
    <div id="lab">-</div></div>
  <div class="card"><h2>Open positions</h2><div id="positions">-</div></div>
  <div class="card"><h2>Recent trades</h2><div id="trades">-</div></div>
  <div class="card"><h2>Last nightly review</h2><div id="review">-</div></div>
  <div class="note"><b>Reading honestly:</b> this is a PAPER account. Fills are simulated
  from top-of-book and systematically optimistic - especially in low-priced fast movers -
  so treat gains as an upper bound and losses as a floor. Day P&amp;L is measured against
  IBKR's previous-day equity.</div>
</div>
<script>
const $ = id => document.getElementById(id);
const fmt = (v, d=2) => (+v).toLocaleString('en-US', {minimumFractionDigits:d, maximumFractionDigits:d});
const money = v => (v<0?'-$':'$') + fmt(Math.abs(v));
const cls = v => v >= 0 ? 'pos' : 'neg';
const sign = v => (v>=0?'+':'') ;

function tile(label, html) {
  return `<div class="tile"><div class="l">${label}</div><div class="v">${html}</div></div>`;
}

function lineChart(rows, series, colors, ylab) {
  // rows: [label, v1, v2, ...]; series: names for v1.. ; one shared y-scale
  if (!rows || rows.length < 2) return '<span class="sub">appears once there are 2+ days of data</span>';
  const W=980, H=220, P=48;
  const all = rows.flatMap(r => r.slice(1).map(Number));
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi - lo < 1) { hi += 1; lo -= 1; }
  const pad = (hi-lo)*0.08; lo -= pad; hi += pad;
  const x = i => P + (W-P-10) * i/(rows.length-1);
  const y = v => (H-24) - (H-34)*(v-lo)/(hi-lo);
  const grid = [0,.5,1].map(f => {
    const v = lo + (hi-lo)*f, yy = y(v).toFixed(1);
    return `<line x1="${P}" y1="${yy}" x2="${W-10}" y2="${yy}" stroke="var(--grid)"/>` +
           `<text x="${P-6}" y="${+yy+4}" text-anchor="end" font-size="10" fill="var(--muted)">${fmt(v,0)}</text>`;
  }).join('');
  const paths = series.map((s, k) =>
    `<path d="${rows.map((r,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(+r[k+1]).toFixed(1)).join(' ')}"
       fill="none" stroke="${colors[k]}" stroke-width="2"/>`).join('');
  const legend = series.map((s,k) =>
    `<span><span class="dot" style="background:${colors[k]}"></span>${s}</span>`).join('');
  return `<div class="legend">${legend}</div>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${grid}${paths}
    <text x="${P}" y="${H-4}" font-size="10" fill="var(--muted)">${rows[0][0]}</text>
    <text x="${W-10}" y="${H-4}" text-anchor="end" font-size="10" fill="var(--muted)">${rows[rows.length-1][0]}</text>
    </svg>`;
}

function tbl(headers, rows) {
  return `<table><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr>` +
    rows.map(r=>`<tr>${r.map(c=>`<td${typeof c==='object'?` class="${c.c}"`:''}>${typeof c==='object'?c.v:c}</td>`).join('')}</tr>`).join('') + '</table>';
}

async function refresh() {
  let d;
  try { d = await (await fetch('/api/stats')).json(); }
  catch { $('status').innerHTML = '<span class="err">dashboard server unreachable</span>'; return; }

  $('status').innerHTML = d.ok
    ? `updated ${d.updated} &middot; auto-refreshes every 30s`
    : `<span class="err">${d.error||'no data yet'}</span> &middot; showing last snapshot` +
      (d.updated ? ` from ${d.updated}` : '');

  // Fleet health banner (from the watchdog's health.json)
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  if (d.health) {
    const h = d.health;
    const ageMin = Math.round((Date.now() - new Date(h.checked).getTime()) / 60000);
    let items = (h.problems || []).map(esc);
    if (!h.tws_ok && !items.some(i => i.indexOf('TWS') !== -1))
      items.unshift('TWS DOWN - the fleet cannot trade');
    // A silent watchdog is itself a problem - never show stale data as green.
    if (ageMin > 45) items.unshift(`watchdog itself is SILENT - last check ${ageMin} min ago`);
    if (items.length) {
      $('health').innerHTML = `<div class="card" style="border-color:var(--down)">
        <h2 style="color:var(--down)">FLEET HEALTH: ${items.length} problem(s)</h2>` +
        items.map(i => `<div class="sub" style="color:var(--down)">&#9888; ${i}</div>`).join('') +
        `<div class="sub">watchdog checked ${ageMin} min ago</div></div>`;
    } else {
      $('health').innerHTML = `<div class="sub" style="color:var(--up);margin-bottom:8px">
        &#10003; fleet healthy (watchdog checked ${ageMin} min ago)</div>`;
    }
  } else {
    $('health').innerHTML = '<div class="sub" style="margin-bottom:8px">watchdog has not reported yet</div>';
  }

  if (d.net_liq !== undefined) {
    $('tiles').innerHTML =
      tile('Net liquidation', money(d.net_liq)) +
      tile('Day P&L', `<span class="${cls(d.day_pnl)}">${sign(d.day_pnl)}${money(d.day_pnl)}
          <span style="font-size:13px">(${sign(d.day_pnl_pct)}${fmt(d.day_pnl_pct,2)}%)</span></span>`) +
      tile('Unrealized P&L', `<span class="${cls(d.unrealized)}">${sign(d.unrealized)}${money(d.unrealized)}</span>`) +
      tile('Cash', money(d.cash)) +
      tile('Open positions', d.positions.length) +
      tile('Trades today', d.trades_today);
    $('chart').innerHTML = lineChart(d.equity_history.map(h=>[h[0].slice(0,16), h[1]]),
                                     ['net liquidation'], ['var(--blue)']);
    $('positions').innerHTML = d.positions.length === 0 ? 'flat - no open positions' :
      tbl(['symbol','qty','avg cost','price','value','unrealized P&L'],
        d.positions.map(p=>[p.symbol, fmt(p.qty,0), fmt(p.avg_cost), fmt(p.price), money(p.value),
          {v: sign(p.unrealized)+money(p.unrealized), c: cls(p.unrealized)}]));
    $('trades').innerHTML = d.trades.length === 0 ? 'no trades recorded yet' :
      tbl(['time','symbol','side','qty','price'],
        d.trades.map(t=>[t.time, t.symbol, {v:t.side, c:t.side==='BUY'?'pos':'neg'}, fmt(+t.qty,0), fmt(+t.price)]));
  }

  // ---- sleeves
  $('sleeves').innerHTML = (d.sleeves && d.sleeves.length) ?
    tbl(['sleeve','round trips','win rate','P&L'],
      d.sleeves.map(s=>[s.sleeve, s.trips, s.win_pct+'%', {v: sign(s.pnl)+money(s.pnl), c: cls(s.pnl)}]))
    : '<span class="sub">no completed round trips yet</span>';

  // ---- AI filter
  let fhtml = '';
  if (d.filter_meta) {
    const m = d.filter_meta;
    fhtml += `<div class="tiles" style="margin-bottom:12px">` +
      tile('Model trained', m.trained || '?') +
      tile('Training samples', m.samples ? fmt(m.samples,0) : '?') +
      tile('Deployed features', m.feature_set || '?') +
      tile('OOS AUC', m.auc_base != null ? fmt(m.auc_base,3) : '?') +
      tile('TAKE threshold', m.take_thr != null ? fmt(m.take_thr,2) : '?') +
      tile('OOS EV: picks vs all', (m.oos_take_ev!=null&&m.oos_baseline_ev!=null)
          ? `${sign(m.oos_take_ev)}${fmt(m.oos_take_ev,2)}% <span style="font-size:13px;color:var(--muted)">vs ${sign(m.oos_baseline_ev)}${fmt(m.oos_baseline_ev,2)}%</span>` : '?') +
      `</div>`;
  }
  fhtml += lineChart(d.ledger, ['actual P&L','if TAKE-gated','if SKIPs vetoed'],
                     ['var(--blue)','var(--violet)','var(--amber)']);
  if (d.advice_today && d.advice_today.length) {
    fhtml += '<div style="margin-top:12px"></div>' + tbl(['time','symbol','sleeve','P(win)','verdict'],
      d.advice_today.map(a=>[a.time, a.symbol, a.sleeve, fmt(a.p,3),
        {v:a.verdict, c:a.verdict==='TAKE'?'pos':(a.verdict==='SKIP'?'neg':'')}]));
  } else {
    fhtml += '<div class="sub" style="margin-top:10px">no opinions issued today (yet)</div>';
  }
  $('filter').innerHTML = fhtml;

  // ---- penny lab
  let ph = '';
  const ps = d.penny_stats || {};
  if (Object.keys(ps).length) {
    ph += `<div class="tiles" style="margin-bottom:12px">` +
      Object.entries({fwd_1d:'day 1', fwd_5d:'day 5', fwd_20d:'day 20'}).map(([k,label]) =>
        ps[k] ? tile(`Picks: ${label}`, `<span class="${cls(ps[k].avg)}">${sign(ps[k].avg)}${fmt(ps[k].avg,1)}%</span>
          <span style="font-size:13px;color:var(--muted)">${ps[k].pos}% up, n=${ps[k].n}</span>`) : ''
      ).join('') + `</div>`;
  }
  if (d.brain) {
    ph += `<div class="sub">Penny Brain: trained ${d.brain.trained} on ${d.brain.n_train} picks` +
          (d.brain.auc != null ? `, held-out AUC ${fmt(d.brain.auc,3)}` : '') + `</div>`;
  } else {
    ph += `<div class="sub">Penny Brain: still collecting resolved picks - trains itself automatically</div>`;
  }
  if (d.ladders && d.ladders.length) {
    ph += '<div class="sub" style="margin-top:8px"><b>Live ladders (no stops - user directive):</b></div>' +
      tbl(['symbol','entry','shares','rungs sold','high-water','since'],
        d.ladders.map(l=>[l.symbol, fmt(l.entry), fmt(l.initial,0), l.rungs, fmt(l.hw), l.since]));
  }
  if (d.radar && d.radar.length) {
    ph += '<div class="sub" style="margin-top:8px"><b>Latest radar picks:</b></div>' +
      tbl(['date','symbol','price','$vol (M)','best day','day 1','day 5','day 20','status'],
        d.radar.map(r=>[r.date, r.symbol, r.price, r.dollar_vol_m, r.best_day+'%',
          ...['fwd_1d','fwd_5d','fwd_20d'].map(k => r[k]===''||r[k]==null ? '-' :
            ({v: sign(+r[k])+fmt(+r[k],1)+'%', c: cls(+r[k])})),
          r.status]));
  }
  $('penny').innerHTML = ph || '<span class="sub">no penny data yet</span>';

  // ---- research lab
  let lh = '';
  if (d.lab && d.lab.length) {
    lh += tbl(['date','study','headline'], d.lab.map(r=>[r.date, r.study,
      `<span style="font-size:12px;color:var(--sec)">${r.metrics.replace(/[{}"]/g,'').slice(0,110)}</span>`]));
  }
  if (d.lab_report) {
    lh += `<div style="margin-top:10px"></div><details><summary>latest lab report</summary><pre>${d.lab_report}</pre></details>`;
  }
  $('lab').innerHTML = lh || '<span class="sub">first study runs at 18:30 on a weekday</span>';

  // ---- review
  $('review').innerHTML = d.review
    ? `<details open><summary>full text</summary><pre>${d.review}</pre></details>`
    : '<span class="sub">no review yet - first one lands at 16:50 on a trading day</span>';
}
refresh();
setInterval(refresh, 30000);
</script></body></html>"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/api/stats")
def stats():
    with _lock:
        snap = dict(_state)
    try:
        snap.update(file_stats())
    except Exception as e:
        snap["file_stats_error"] = str(e)
    return jsonify(snap)


if __name__ == "__main__":
    threading.Thread(target=poller, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False)
