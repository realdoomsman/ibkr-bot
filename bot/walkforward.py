"""Walk-forward validation: the honest way to evaluate a tunable strategy.

The problem it solves: if you tune parameters on ALL your data and then quote
the results, you've measured how well you can fit the past, not how well you
can trade the future. Walk-forward fixes this by repeatedly:

    1. optimizing parameters on an in-sample (IS) window,
    2. testing those frozen parameters on the NEXT out-of-sample (OOS) window,
    3. rolling both windows forward.

Only the stitched-together OOS segments count. If OOS results are much worse
than IS results (low "walk-forward efficiency"), the strategy is curve-fit.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .backtest import run_backtest
from .strategy import STRATEGIES


@dataclass
class WindowResult:
    is_start: str
    oos_start: str
    oos_end: str
    params: dict
    is_return_pct: float
    is_sharpe: float
    oos_return_pct: float


@dataclass
class WalkForwardResult:
    strategy: str
    symbol: str
    windows: list[WindowResult]
    stitched_oos_return_pct: float
    avg_is_return_pct: float
    wf_efficiency: float | None
    profitable_windows: int

    def summary(self) -> str:
        lines = [
            f"Walk-forward: {self.strategy} on {self.symbol}",
            "-" * 74,
            f"{'IS start':<12}{'OOS period':<24}{'params':<26}{'IS ret':>7}{'OOS ret':>8}",
        ]
        for w in self.windows:
            params = ", ".join(f"{k}={v}" for k, v in w.params.items()) or "(none)"
            lines.append(
                f"{w.is_start:<12}{w.oos_start + ' - ' + w.oos_end:<24}"
                f"{params:<26}{w.is_return_pct:>6.1f}%{w.oos_return_pct:>7.1f}%"
            )
        lines += [
            "-" * 74,
            f"  Stitched OOS return:      {self.stitched_oos_return_pct:.2f}%   <- the number that matters",
            f"  Avg in-sample return:     {self.avg_is_return_pct:.2f}%",
            f"  Profitable OOS windows:   {self.profitable_windows}/{len(self.windows)}",
        ]
        if self.wf_efficiency is not None:
            verdict = (
                "robust-ish" if self.wf_efficiency >= 0.6
                else "borderline" if self.wf_efficiency >= 0.4
                else "likely curve-fit"
            )
            lines.append(
                f"  Walk-forward efficiency:  {self.wf_efficiency:.2f}  ({verdict}; "
                f">0.6 is good, <0.5 is a warning)"
            )
        lines.append(
            "  Reminder: OOS windows are few - treat this as a smell test, not proof."
        )
        return "\n".join(lines)


def _pick_best_params(bars: pd.DataFrame, strategy_cls, grid: list[dict]) -> tuple[dict, float, float]:
    """Optimize on the in-sample window. Returns (params, is_return_pct, is_sharpe)."""
    best = None
    for params in grid:
        strat = strategy_cls(**params)
        if len(bars) <= strat.warmup + 10:
            continue
        r = run_backtest(bars, strat)
        key = (r.sharpe, r.total_return_pct)
        if best is None or key > best[0]:
            best = (key, params, r)
    if best is None:
        raise ValueError(
            "In-sample window too short for every parameter combination - "
            "increase --is-len or use more history"
        )
    _, params, r = best
    return params, r.total_return_pct, r.sharpe


def walk_forward(
    bars: pd.DataFrame,
    strategy_name: str,
    is_len: int = 504,   # ~2 years of in-sample bars
    oos_len: int = 126,  # ~6 months tested out-of-sample
) -> WalkForwardResult:
    strategy_cls = STRATEGIES[strategy_name]
    grid = strategy_cls.param_grid()

    windows: list[WindowResult] = []
    oos_growth = 1.0

    start = 0
    while start + is_len + oos_len <= len(bars):
        is_bars = bars.iloc[start : start + is_len]
        params, is_ret, is_sharpe = _pick_best_params(is_bars, strategy_cls, grid)

        # OOS run: include the IS window as indicator warmup, but only measure
        # equity from the OOS start. Signals in the OOS period therefore have
        # full history behind them without contaminating the measurement.
        strat = strategy_cls(**params)
        combined = bars.iloc[start : start + is_len + oos_len]
        r = run_backtest(combined, strat)
        oos_start_date = bars.index[start + is_len]
        # Measure from the LAST in-sample close, not the first OOS close:
        # equity points are marked at each bar's close, so baselining at the
        # first OOS bar would drop day one's P&L and absorb its trade costs.
        baseline_date = bars.index[start + is_len - 1]
        oos_curve = r.equity_curve[r.equity_curve.index >= baseline_date]
        if len(oos_curve) < 2:
            break
        oos_ret = float(oos_curve.iloc[-1] / oos_curve.iloc[0] - 1)
        oos_growth *= 1 + oos_ret

        windows.append(
            WindowResult(
                is_start=str(bars.index[start].date()),
                oos_start=str(oos_start_date.date()),
                oos_end=str(combined.index[-1].date()),
                params=params,
                is_return_pct=is_ret,
                is_sharpe=is_sharpe,
                oos_return_pct=100 * oos_ret,
            )
        )
        start += oos_len

    if not windows:
        raise ValueError(
            f"Not enough data: need at least {is_len + oos_len} bars, have {len(bars)}"
        )

    stitched = 100 * (oos_growth - 1)
    # Compare average per-window OOS return to average IS return, scaled to the
    # same number of TRADABLE bars. The IS backtest is forced flat during the
    # strategy's own warmup, so its return is earned over (is_len - warmup)
    # bars, while the OOS run is active from day one - scaling by raw is_len
    # would overstate efficiency by up to ~2x for slow strategies.
    avg_is = sum(w.is_return_pct for w in windows) / len(windows)
    avg_oos = sum(w.oos_return_pct for w in windows) / len(windows)
    scaled_is = [
        w.is_return_pct * (oos_len / (is_len - strategy_cls(**w.params).warmup))
        for w in windows
    ]
    avg_is_scaled = sum(scaled_is) / len(scaled_is)
    wfe = None
    if avg_is_scaled > 1e-9:
        wfe = avg_oos / avg_is_scaled

    return WalkForwardResult(
        strategy=strategy_name,
        symbol=bars.attrs.get("symbol", "?"),
        windows=windows,
        stitched_oos_return_pct=stitched,
        avg_is_return_pct=avg_is,
        wf_efficiency=wfe,
        profitable_windows=sum(1 for w in windows if w.oos_return_pct > 0),
    )
