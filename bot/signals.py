"""Signal library: 5 classic technical strategies as vectorized functions.

These are baseline FEATURES for the ML trade filter, not standalone money
makers. Each is a well-known internet/textbook strategy implemented honestly.

Contract (identical for all five functions):
    Input:  pandas DataFrame with columns open, high, low, close, volume
            (daily bars, ascending DatetimeIndex).
    Output: pandas Series of int signals aligned to the input index:
            1 = Buy event, -1 = Sell event, 0 = Hold/no event.

NO-LOOKAHEAD GUARANTEE
----------------------
Every function ends with `.shift(1)`. The raw signal computed from bars up to
and including day t is therefore reported at row t+1. Read the output as:

    signal.loc[t] = "the action knowable BEFORE day t's open"

so acting at day t's open price uses only information from day t-1 and
earlier. This is enforced mechanically (the shift), not by convention, and
the pytest suite verifies it by perturbing individual rows.

All rolling/EWM windows are trailing (past-only); nothing is centered.
Lookback defaults are the standard institutional/textbook values, noted per
function. Vectorized throughout - no Python loops.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _finalize(raw: pd.Series) -> pd.Series:
    """Apply the execution lag and clean up dtype. (Shared plumbing only -
    the strategy math lives entirely inside each function.)"""
    return raw.shift(1).fillna(0).astype(int)


def macd_crossover_signal(df: pd.DataFrame) -> pd.Series:
    """MACD line / signal line crossover (Appel). Defaults 12-26-9 (standard).

    Math:
        MACD_t   = EMA_12(close)_t - EMA_26(close)_t     (momentum spread)
        Signal_t = EMA_9(MACD)_t                          (smoothed trigger)
    Buy  when MACD crosses ABOVE its signal line (momentum turning up).
    Sell when MACD crosses BELOW its signal line.
    EWMs use adjust=False (the recursive textbook form: y_t = a*x_t + (1-a)*y_{t-1}).
    """
    close = df["close"]
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=9, adjust=False).mean()

    above = macd > signal_line
    prev_above = above.shift(1)
    # fillna choices make day 0 a non-event by construction.
    cross_up = above & ~prev_above.fillna(True).astype(bool)
    cross_dn = ~above & prev_above.fillna(False).astype(bool)

    raw = pd.Series(np.where(cross_up, 1, np.where(cross_dn, -1, 0)), index=df.index)
    return _finalize(raw)


def rsi_mean_reversion_signal(df: pd.DataFrame) -> pd.Series:
    """RSI(14) mean reversion (Wilder). Defaults: 14 period, 30/70 bands (standard).

    Math (Wilder's smoothing = EWM with alpha = 1/n):
        gain_t = max(close_t - close_{t-1}, 0),  loss_t = max(-(delta), 0)
        RS_t   = WilderEMA(gain)_t / WilderEMA(loss)_t
        RSI_t  = 100 - 100 / (1 + RS_t)
    Buy  when RSI crosses UP through 30 (leaving oversold - the reversion bet).
    Sell when RSI crosses DOWN through 70 (leaving overbought).
    Signaling on the EXIT of the extreme zone (not entry) is the standard
    form: it waits for reversion to actually begin.
    """
    close = df["close"]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).fillna(50)  # undefined early RSI -> neutral 50

    prev = rsi.shift(1)
    cross_up_30 = (rsi > 30) & (prev <= 30)
    cross_dn_70 = (rsi < 70) & (prev >= 70)

    raw = pd.Series(np.where(cross_up_30, 1, np.where(cross_dn_70, -1, 0)), index=df.index)
    return _finalize(raw)


def bollinger_breakout_signal(df: pd.DataFrame) -> pd.Series:
    """Bollinger Band breakout. Defaults: 20-day SMA, 2 standard deviations (standard).

    Math:
        mid_t   = SMA_20(close)_t
        band    = mid_t +/- 2 * stdev_20(close)_t     (population ddof=0? -
                  pandas default ddof=1 is the common charting convention; kept)
    This is the BREAKOUT (volatility-expansion) interpretation:
    Buy  when close crosses ABOVE the upper band (unusual strength).
    Sell when close crosses BELOW the lower band (unusual weakness).
    (The inverse, mean-reversion interpretation fades these moves; both are
    common online - we implement breakout and let the ML filter learn when
    it works.)
    """
    close = df["close"]
    mid = close.rolling(20).mean()
    sd = close.rolling(20).std()
    upper = mid + 2 * sd
    lower = mid - 2 * sd

    prev_close = close.shift(1)
    cross_up = (close > upper) & (prev_close <= upper.shift(1))
    cross_dn = (close < lower) & (prev_close >= lower.shift(1))

    raw = pd.Series(np.where(cross_up, 1, np.where(cross_dn, -1, 0)), index=df.index)
    return _finalize(raw)


def golden_cross_signal(df: pd.DataFrame) -> pd.Series:
    """Golden/death cross. Defaults: SMA 50 vs SMA 200 (the standard pair).

    Math:
        fast_t = SMA_50(close)_t,  slow_t = SMA_200(close)_t
    Buy  when the 50d SMA crosses ABOVE the 200d ("golden cross").
    Sell when it crosses BELOW ("death cross").
    The slowest, most-lagging strategy here - fires a handful of times per
    decade per symbol. Included because it is the single most talked-about
    signal in retail finance media.
    """
    close = df["close"]
    fast = close.rolling(50).mean()
    slow = close.rolling(200).mean()

    above = fast > slow
    prev_above = above.shift(1)
    valid = slow.notna() & slow.shift(1).notna()  # no events during warmup
    cross_up = above & ~prev_above.fillna(True).astype(bool) & valid
    cross_dn = ~above & prev_above.fillna(False).astype(bool) & valid

    raw = pd.Series(np.where(cross_up, 1, np.where(cross_dn, -1, 0)), index=df.index)
    return _finalize(raw)


def donchian_breakout_signal(df: pd.DataFrame) -> pd.Series:
    """Donchian channel breakout (Turtle-style). Default: 20-day channel (classic).

    Math:
        upper_t = max(high_{t-20} .. high_{t-1})   <- note: PRIOR 20 days,
        lower_t = min(low_{t-20}  .. low_{t-1})       excluding today
    Buy  when today's close exceeds the prior 20-day high (fresh breakout).
    Sell when today's close breaks the prior 20-day low.
    Two distinct shifts, for two distinct reasons:
      1. The channel uses .shift(1) so today's own high/low can never be part
         of the barrier it is breaking (that comparison would be circular).
      2. The final _finalize shift is the execution lag shared by all signals.
    """
    close = df["close"]
    upper = df["high"].rolling(20).max().shift(1)
    lower = df["low"].rolling(20).min().shift(1)

    breakout_up = close > upper
    breakout_dn = close < lower
    # Signal only on the FIRST day of a breakout, not every day above the band.
    first_up = breakout_up & ~breakout_up.shift(1).fillna(True).astype(bool)
    first_dn = breakout_dn & ~breakout_dn.shift(1).fillna(True).astype(bool)

    raw = pd.Series(np.where(first_up, 1, np.where(first_dn, -1, 0)), index=df.index)
    return _finalize(raw)


ALL_SIGNALS = {
    "macd_cross": macd_crossover_signal,
    "rsi_reversion": rsi_mean_reversion_signal,
    "bollinger_breakout": bollinger_breakout_signal,
    "golden_cross": golden_cross_signal,
    "donchian_breakout": donchian_breakout_signal,
}
