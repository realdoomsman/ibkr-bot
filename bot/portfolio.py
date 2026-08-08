"""Portfolio-level backtesting: one account, many symbols, shared cash.

Testing symbols in isolation misses what actually determines account outcomes:
diversification (uncorrelated losers hurt less), cash constraints (you can't
buy everything at once), and how drawdowns overlap. This runs ONE account
across a basket, equal-weighting capital per symbol, with the same honest
no-lookahead, costs-included mechanics as the single-symbol backtester.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .strategy import Strategy

TRADING_DAYS = 252


@dataclass
class PortfolioResult:
    symbols: list[str]
    start: str
    end: str
    starting_cash: float
    ending_equity: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    num_trades: int
    equity_curve: pd.Series = field(repr=False)
    yearly_returns_pct: pd.Series = field(repr=False)

    def summary(self) -> str:
        return "\n".join([
            f"Portfolio backtest: {', '.join(self.symbols)}",
            f"  Period:             {self.start} -> {self.end}",
            "-" * 52,
            f"  Starting cash:      ${self.starting_cash:>12,.2f}",
            f"  Ending equity:      ${self.ending_equity:>12,.2f}",
            f"  Total return:       {self.total_return_pct:>12.2f}%",
            f"  CAGR:               {self.cagr_pct:>12.2f}%",
            f"  Max drawdown:       {self.max_drawdown_pct:>12.2f}%",
            f"  Sharpe (daily ann): {self.sharpe:>12.2f}",
            f"  Trades:             {self.num_trades:>12d}",
        ])


def run_portfolio_backtest(
    bars_by_symbol: dict[str, pd.DataFrame],
    strategy: Strategy,
    starting_cash: float = 20_000.0,
    commission_per_share: float = 0.005,
    min_commission: float = 1.0,
    slippage_bps: float = 5.0,
) -> PortfolioResult:
    symbols = list(bars_by_symbol)
    if not symbols:
        raise ValueError("No symbols provided")

    # Align every symbol on the dates they ALL traded.
    common = None
    for df in bars_by_symbol.values():
        common = df.index if common is None else common.intersection(df.index)
    common = common.sort_values()
    if len(common) <= strategy.warmup + 1:
        raise ValueError("Not enough overlapping history for this strategy's warmup")
    aligned = {s: df.loc[common] for s, df in bars_by_symbol.items()}

    weight = 1.0 / len(symbols)
    cash = starting_cash
    shares: dict[str, int] = {s: 0 for s in symbols}
    trades = 0
    equity_points: list[float] = []

    for i in range(1, len(common)):
        # Size positions off total equity as of yesterday's close.
        prev_equity = cash + sum(
            shares[s] * float(aligned[s]["close"].iloc[i - 1]) for s in symbols
        )
        # Two-phase day: decide everything first, then execute ALL sells
        # before ANY buy. All fills happen at the same day's open, so a
        # same-day rotation must let exit proceeds fund the new entry -
        # otherwise results would depend on the order symbols were listed.
        targets = {
            s: strategy.target_position(
                aligned[s].iloc[:i], current=1.0 if shares[s] > 0 else 0.0
            )
            for s in symbols
        }

        for s in symbols:  # phase 1: sells credit cash
            if targets[s] == 0 and shares[s] > 0:
                price = float(aligned[s]["open"].iloc[i])
                slip = price * (1 - slippage_bps / 10_000)
                fee = max(min_commission, shares[s] * commission_per_share)
                cash += shares[s] * slip - fee
                shares[s] = 0
                trades += 1

        for s in symbols:  # phase 2: buys spend it
            target = targets[s]
            if target > 0 and shares[s] == 0:
                price = float(aligned[s]["open"].iloc[i])
                slip = price * (1 + slippage_bps / 10_000)
                # Fractional targets scale the entry; the cash cap applies last.
                # (Positions are exited only at target == 0 - no partial rebalancing.)
                budget = min(prev_equity * weight * target, cash)
                qty = int((budget - min_commission) // (slip + commission_per_share))
                if qty > 0:
                    fee = max(min_commission, qty * commission_per_share)
                    cost = qty * slip + fee
                    if cost <= cash:
                        cash -= cost
                        shares[s] = qty
                        trades += 1

        equity_points.append(
            cash + sum(shares[s] * float(aligned[s]["close"].iloc[i]) for s in symbols)
        )

    equity = pd.Series(equity_points, index=common[1:], name="equity")
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

    # Calendar-year returns (first year measured from starting cash).
    year_end = equity.resample("YE").last()
    prev_end = year_end.shift(1)
    prev_end.iloc[0] = starting_cash
    yearly = (100 * (year_end / prev_end - 1)).rename(lambda ts: ts.year)

    return PortfolioResult(
        symbols=symbols,
        start=str(common[0].date()),
        end=str(common[-1].date()),
        starting_cash=starting_cash,
        ending_equity=ending,
        total_return_pct=100 * total_ret,
        cagr_pct=100 * cagr,
        max_drawdown_pct=100 * max_dd,
        sharpe=sharpe,
        num_trades=trades,
        equity_curve=equity,
        yearly_returns_pct=yearly,
    )
