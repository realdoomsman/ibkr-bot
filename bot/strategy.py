"""Strategy interface and three classic example strategies.

A strategy answers one question: given the price history up to now (and what
I currently hold), what fraction of my allocation should be in this symbol?
(0.0 = flat, 1.0 = fully allocated.)

The three strategies here represent the three classic families:
  - sma_cross      trend following   ("ride what's already moving")
  - rsi_reversion  mean reversion    ("buy panicky dips in an uptrend")
  - momentum       time-series momentum ("own it only if it's up over N months")

None of them is a money printer. They exist so you can COMPARE approaches
honestly using the backtest / sweep / walk-forward tools.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from itertools import product

import pandas as pd


class Strategy(ABC):
    """Base class for all strategies."""

    #: Bars required before the strategy can produce a meaningful answer.
    warmup: int = 1

    @abstractmethod
    def target_position(self, bars: pd.DataFrame, current: float = 0.0) -> float:
        """Return desired allocation in [0.0, 1.0].

        `bars`    - price history up to 'now'. Never contains future rows;
                    peeking ahead is lookahead bias, the classic way backtests
                    produce fantasy profits.
        `current` - the allocation currently held (0.0 or 1.0). Lets
                    strategies use different entry and exit thresholds
                    (hysteresis) while staying stateless between calls.

        Semantics in the execution engines: a value in (0, 1) scales the
        ENTRY size; positions are exited only when the target returns 0.
        There is no partial rebalancing between fractional levels.
        """

    @classmethod
    def param_grid(cls) -> list[dict]:
        """Parameter combinations for sweep / walk-forward tools."""
        return [{}]


# ---------------------------------------------------------------------------


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-12)
    return 100 - 100 / (1 + rs)


class SmaCross(Strategy):
    """Trend following: in the market while the fast SMA is above the slow SMA."""

    def __init__(self, fast: int = 20, slow: int = 50):
        if fast >= slow:
            raise ValueError("fast period must be shorter than slow period")
        self.fast = int(fast)
        self.slow = int(slow)
        self.warmup = self.slow + 1

    def target_position(self, bars: pd.DataFrame, current: float = 0.0) -> float:
        if len(bars) < self.warmup:
            return 0.0
        close = bars["close"]
        fast_ma = close.rolling(self.fast).mean().iloc[-1]
        slow_ma = close.rolling(self.slow).mean().iloc[-1]
        return 1.0 if fast_ma > slow_ma else 0.0

    @classmethod
    def param_grid(cls) -> list[dict]:
        fasts = [10, 20, 50]
        slows = [50, 100, 200]
        return [{"fast": f, "slow": s} for f, s in product(fasts, slows) if f < s]


class RsiReversion(Strategy):
    """Mean reversion: buy short-term panic dips, but only in a long-term uptrend.

    Entry: RSI(period) drops below `buy_below` while price is above its 200-day
    SMA (the trend filter - never catch falling knives in a downtrend).
    Exit: RSI recovers above `exit_above`.
    """

    def __init__(self, period: int = 2, buy_below: float = 10.0,
                 exit_above: float = 60.0, trend_sma: int = 200):
        self.period = int(period)
        self.buy_below = float(buy_below)
        self.exit_above = float(exit_above)
        self.trend_sma = int(trend_sma)
        self.warmup = self.trend_sma + 1

    def target_position(self, bars: pd.DataFrame, current: float = 0.0) -> float:
        if len(bars) < self.warmup:
            return 0.0
        close = bars["close"]
        trend = close.rolling(self.trend_sma).mean().iloc[-1]
        if close.iloc[-1] < trend:
            return 0.0  # downtrend: stay out regardless
        r = _rsi(close, self.period).iloc[-1]
        if current == 0.0:
            return 1.0 if r < self.buy_below else 0.0
        return 0.0 if r > self.exit_above else current

    @classmethod
    def param_grid(cls) -> list[dict]:
        return [
            {"buy_below": b, "exit_above": e}
            for b, e in product([5, 10, 15], [50, 60, 70])
        ]


class Momentum(Strategy):
    """Time-series momentum: own it only if it's up over the lookback window."""

    def __init__(self, lookback: int = 126):
        self.lookback = int(lookback)
        self.warmup = self.lookback + 1

    def target_position(self, bars: pd.DataFrame, current: float = 0.0) -> float:
        if len(bars) < self.warmup:
            return 0.0
        close = bars["close"]
        past = close.iloc[-self.lookback - 1]
        return 1.0 if close.iloc[-1] > past else 0.0

    @classmethod
    def param_grid(cls) -> list[dict]:
        return [{"lookback": lb} for lb in [63, 126, 252]]


# ---------------------------------------------------------------------------

STRATEGIES: dict[str, type[Strategy]] = {
    "sma_cross": SmaCross,
    "rsi_reversion": RsiReversion,
    "momentum": Momentum,
}


def make_strategy(name: str, **params) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError:
        raise ValueError(
            f"Unknown strategy {name!r}. Available: {', '.join(STRATEGIES)}"
        ) from None
    return cls(**params)
