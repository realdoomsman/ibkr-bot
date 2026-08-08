"""Self-contained HTML report with equity-curve charts and stats tables.

One chart per symbol: the three strategies against buy-and-hold, all indexed
to $100 at the start so they share a single honest axis. Colors follow the
validated reference palette (fixed slot order; benchmark rides in neutral
gray so it reads as the reference line, not a competitor).
"""

from __future__ import annotations

import base64
import html
import io
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# Reference palette (light mode) - validated slot order, do not shuffle.
SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
GOOD = "#006300"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]  # blue, orange, aqua
BENCH = "#898781"


def equity_chart_png(curves: dict[str, pd.Series], benchmark: tuple[str, pd.Series]) -> bytes:
    """Line chart of strategy equity vs buy-and-hold, indexed to 100."""
    fig, ax = plt.subplots(figsize=(8.8, 3.4), dpi=140)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    b_label, b_curve = benchmark
    ax.plot(
        b_curve.index, 100 * b_curve / b_curve.iloc[0],
        color=BENCH, lw=2, ls=(0, (4, 3)), label=b_label, zorder=2,
    )
    for i, (label, curve) in enumerate(curves.items()):
        ax.plot(
            curve.index, 100 * curve / curve.iloc[0],
            color=SERIES[i % len(SERIES)], lw=2, label=label, zorder=3,
        )

    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    ax.set_ylabel("Growth of $100", color=SECONDARY, fontsize=9)
    ax.margins(x=0.01)
    leg = ax.legend(
        loc="upper left", fontsize=8, ncol=2, frameon=False,
        labelcolor=SECONDARY, handlelength=1.6,
    )
    for line in leg.get_lines():
        line.set_linewidth(2.5)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _png_tag(png: bytes, alt: str) -> str:
    b64 = base64.b64encode(png).decode()
    return f'<img alt="{html.escape(alt)}" src="data:image/png;base64,{b64}">'


def _fmt_row(label: str, cells: list[str], beat: bool | None, is_bench: bool) -> str:
    badge = ""
    if beat is True:
        badge = f' <span style="color:{GOOD};font-size:12px">&#10003; beat B&amp;H</span>'
    style = f'color:{SECONDARY};font-style:italic' if is_bench else f"color:{INK}"
    tds = "".join(f'<td style="text-align:right">{c}</td>' for c in cells)
    return f'<tr><td style="{style}">{html.escape(label)}{badge}</td>{tds}</tr>'


def render_report(
    sections: list[dict],
    path: str = "report.html",
    years: int = 10,
) -> str:
    """sections: [{symbol, chart_png, rows: [(label, stats_cells, beat, is_bench)]}]"""
    parts = [f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strategy comparison report</title>
<style>
  body {{ background:{PAGE}; color:{INK}; margin:0;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
  .wrap {{ max-width: 980px; margin: 0 auto; padding: 28px 20px 60px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color:{SECONDARY}; font-size: 13px; margin-bottom: 24px; }}
  .card {{ background:{SURFACE}; border:1px solid rgba(11,11,11,0.10);
           border-radius: 10px; padding: 18px 18px 10px; margin-bottom: 22px; }}
  .card h2 {{ font-size: 16px; margin: 0 0 10px; }}
  img {{ max-width: 100%; height: auto; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0 8px;
           font-size: 13px; font-variant-numeric: tabular-nums; }}
  th {{ color:{MUTED}; font-weight: 500; text-align: right; font-size: 11px;
        text-transform: uppercase; letter-spacing: .04em;
        border-bottom: 1px solid {GRID}; padding: 4px 8px; }}
  th:first-child {{ text-align: left; }}
  td {{ padding: 5px 8px; border-bottom: 1px solid {GRID}; }}
  tr:last-child td {{ border-bottom: none; }}
  .note {{ background:{SURFACE}; border:1px solid rgba(11,11,11,0.10);
           border-radius: 10px; padding: 14px 18px; color:{SECONDARY};
           font-size: 13px; line-height: 1.5; }}
</style></head><body><div class="wrap">
<h1>Strategy comparison report</h1>
<div class="sub">Three strategies vs buy-and-hold &middot; {years} years of real daily data
(Yahoo Finance, dividend/split adjusted) &middot; generated {date.today().isoformat()}
&middot; costs modeled: IBKR-style commissions + 5bps slippage</div>"""]

    for s in sections:
        header = (
            "<tr><th>Strategy</th><th>Total return</th><th>CAGR</th>"
            "<th>Sharpe</th><th>Max drawdown</th><th>Trades</th></tr>"
        )
        rows = "".join(_fmt_row(*row) for row in s["rows"])
        parts.append(
            f'<div class="card"><h2>{html.escape(s["symbol"])}</h2>'
            f'{_png_tag(s["chart_png"], f"Equity curves for {s['symbol']}")}'
            f"<table>{header}{rows}</table></div>"
        )

    parts.append(f"""<div class="note"><b>How to read this honestly.</b>
Every line is net of commissions and slippage and computed with no lookahead
(signals use data through yesterday's close, fills happen at today's open).
The gray dashed line is buy-and-hold &mdash; the "do nothing" benchmark; a strategy
that trails it is losing money relative to not existing. These are single
backtests on one historical decade: they measure the past, not the future.
Before trusting any strategy, run <code>run_sweep.py</code> (parameter fragility)
and <code>run_walkforward.py</code> (out-of-sample validity). Nothing here is
financial advice.</div>
</div></body></html>""")

    out = "".join(parts)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return path
