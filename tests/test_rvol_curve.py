"""Per-symbol RVOL curve: offline tests for the pure math."""

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

from bot.scanner import curve_from_bars, expected_volume_fraction

NY = ZoneInfo("America/New_York")


def _synthetic_bars(days: int, front_loaded: bool) -> pd.DataFrame:
    """5-min RTH bars; front_loaded puts half the day's volume in the first
    half hour (the penny-mover profile), else flat."""
    rows = []
    for d in range(days):
        day = dt.date(2026, 7, 1) + dt.timedelta(days=d)
        if day.weekday() >= 5:
            continue
        t = dt.datetime.combine(day, dt.time(9, 30), tzinfo=NY)
        for i in range(78):  # 78 five-minute bars = full RTH session
            vol = 1000
            if front_loaded and i < 6:
                vol = 6500  # first 30 min carries ~35% of the day
            rows.append({"date": t, "open": 1, "high": 1, "low": 1,
                         "close": 1, "volume": vol})
            t += dt.timedelta(minutes=5)
    return pd.DataFrame(rows)


def test_flat_profile_is_linear():
    curve = curve_from_bars(_synthetic_bars(30, front_loaded=False))
    assert curve is not None and len(curve) == 13
    assert abs(curve[0] - 1 / 13) < 0.01  # each half-hour = equal share
    assert curve[-1] == 1.0
    assert all(b >= a for a, b in zip(curve, curve[1:]))  # monotone


def test_front_loaded_profile_detected():
    curve = curve_from_bars(_synthetic_bars(30, front_loaded=True))
    generic_first_slot = 0.13
    assert curve[0] > 2 * generic_first_slot  # measured ≫ generic U-shape


def test_too_few_days_returns_none():
    assert curve_from_bars(_synthetic_bars(5, front_loaded=False)) is None


def test_expected_fraction_uses_custom_curve():
    curve = [0.5] + [0.5 + 0.5 * i / 12 for i in range(1, 13)]
    at_10am = dt.datetime(2026, 8, 10, 10, 0, tzinfo=NY)
    custom = expected_volume_fraction(at_10am, curve)
    generic = expected_volume_fraction(at_10am)
    assert custom > 0.45 and generic < 0.25  # curve actually changes the answer


def test_bad_curve_falls_back_to_generic():
    at_10am = dt.datetime(2026, 8, 10, 10, 0, tzinfo=NY)
    assert expected_volume_fraction(at_10am, [0.5, 1.0]) == \
        expected_volume_fraction(at_10am)  # wrong length -> generic
