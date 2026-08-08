"""A small, honest backtester.

Honest means:
- No lookahead: signals are computed from data through yesterday's close,
  and executed at TODAY's open.
- Costs are modeled: commission and slippage on every trade.
- Results are compared against simple buy-and-hold, because a strategy
  that underperforms doing nothing is not a strategy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .strategy import Strategy

TRADING_DAYS = 252


@dataclass
class BacktestResult:
    symbol: str
    start: str
    end: str
    starting_cash: float
    ending_equity: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    num_trades: int
    win_rate_pct: float | None
    buy_hold_return_pct: float
    equity_curve: pd.Series = field(repr=False)

    def summary(self) -> str:
        win = f"{self.win_rate_pct:.0f}%" if self.win_rate_pct is not None else "n/a"
        lines = [
            f"Backtest: {self.symbol}  ({self.start} -> {self.end})",
            "-" * 52,
            f"  Starting cash:      ${self.starting_cash:>12,.2f}",
            f"  Ending equity:      ${self.ending_equity:>12,.2f}",
            f"  Total return:       {self.total_return_pct:>12.2f}%",
            f"  CAGR:               {self.cagr_pct:>12.2f}%",
            f"  Max drawdown:       {self.max_drawdown_pct:>12.2f}%",
            f"  Sharpe (daily ann): {self.sharpe:>12.2f}",
            f"  Trades:             {self.num_trades:>12d}",
            f"  Win rate:           {win:>12}",
            f"  Buy & hold return:  {self.buy_hold_return_pct:>12.2f}%",
        ]
        if self.total_return_pct < self.buy_hold_return_pct:
            lines.append("  NOTE: strategy UNDERPERFORMED buy-and-hold here.")
        return "\n".join(lines)


def run_backtest(
    bars: pd.DataFrame,
    strategy: Strategy,
    starting_cash: float = 20_000.0,
    commission_per_share: float = 0.005,
    min_commission: float = 1.0,
    slippage_bps: float = 5.0,
    cost_multiplier: float = 1.0,
) -> BacktestResult:
    """Walk the bars day by day, trading at the open after each signal change.

    `cost_multiplier` scales all costs - rerun with 2.0 as a stress test:
    a strategy that dies when costs double never had a real edge.
    """
    if len(bars) <= strategy.warmup + 1:
        raise ValueError("Not enough bars for this strategy's warmup period")

    commission_per_share *= cost_multiplier
    min_commission *= cost_multiplier
    slippage_bps *= cost_multiplier

    cash = starting_cash
    shares = 0
    equity_points: list[float] = []
    trade_pnls: list[float] = []
    entry_cost: float | None = None
    num_trades = 0

    opens = bars["open"].to_numpy()
    closes = bars["close"].to_numpy()

    for i in range(1, len(bars)):
        # Decide using ONLY bars[0:i] - i.e. data through yesterday's close.
        current = 1.0 if shares > 0 else 0.0
        target = strategy.target_position(bars.iloc[:i], current=current)
        price = opens[i]

        if target > 0 and shares == 0:
            slip = price * (1 + slippage_bps / 10_000)  # buys fill slightly worse
            # Reserve the commission before sizing so cash can never go negative
            # (that would silently model free margin).
            budget = cash * target
            qty = int((budget - min_commission) // (slip + commission_per_share))
            if qty > 0:
                fee = max(min_commission, qty * commission_per_share)
                cash -= qty * slip + fee
                shares = qty
                entry_cost = qty * slip + fee
                num_trades += 1
        elif target == 0 and shares > 0:
            slip = price * (1 - slippage_bps / 10_000)  # sells fill slightly worse
            fee = max(min_commission, shares * commission_per_share)
            proceeds = shares * slip - fee
            cash += proceeds
            if entry_cost is not None:
                trade_pnls.append(proceeds - entry_cost)
            shares = 0
            entry_cost = None
            num_trades += 1

        equity_points.append(cash + shares * closes[i])

    equity = pd.Series(equity_points, index=bars.index[1:], name="equity")
    ending = float(equity.iloc[-1])
    total_ret = ending / starting_cash - 1

    years = len(equity) / TRADING_DAYS
    cagr = (ending / starting_cash) ** (1 / years) - 1 if years > 0 else 0.0

    running_max = equity.cummax()
    max_dd = float(((equity - running_max) / running_max).min())

    daily_ret = equity.pct_change().dropna()
    sharpe = 0.0
    if len(daily_ret) > 1 and daily_ret.std() > 0:
        sharpe = float(daily_ret.mean() / daily_ret.std() * math.sqrt(TRADING_DAYS))

    buy_hold = closes[-1] / opens[1] - 1

    win_rate = None
    if trade_pnls:
        win_rate = 100.0 * sum(1 for p in trade_pnls if p > 0) / len(trade_pnls)

    return BacktestResult(
        symbol=bars.attrs.get("symbol", "?"),
        start=str(bars.index[0].date()),
        end=str(bars.index[-1].date()),
        starting_cash=starting_cash,
        ending_equity=ending,
        total_return_pct=100 * total_ret,
        cagr_pct=100 * cagr,
        max_drawdown_pct=100 * max_dd,
        sharpe=sharpe,
        num_trades=num_trades,
        win_rate_pct=win_rate,
        buy_hold_return_pct=100 * buy_hold,
        equity_curve=equity,
    )
