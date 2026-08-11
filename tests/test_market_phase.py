"""Regression tests for the fleet's oldest bug: engines start at 9:28 and
pre-open must mean WAIT, never 'closed' (which made every scheduled start
exit two minutes before the bell - expressed 2026-08-11, the first morning
every other failure was fixed)."""

import datetime as dt
from zoneinfo import ZoneInfo

from run_intraday import market_phase

NY = ZoneInfo("America/New_York")


def _at(h, m, day=11):  # Aug 11 2026 = a Tuesday
    return dt.datetime(2026, 8, day, h, m, tzinfo=NY)


def test_engine_start_time_is_premarket_not_closed():
    # THE bug: 9:28 scheduled start must wait for the bell, not exit
    assert market_phase(_at(9, 28)) == "premarket"


def test_early_morning_is_premarket():
    assert market_phase(_at(7, 0)) == "premarket"


def test_open_at_the_bell():
    assert market_phase(_at(9, 30)) == "open"
    assert market_phase(_at(12, 0)) == "open"


def test_flatten_window():
    assert market_phase(_at(15, 55)) == "flatten"


def test_after_close_is_closed():
    assert market_phase(_at(16, 0)) == "closed"
    assert market_phase(_at(20, 0)) == "closed"


def test_weekend_is_closed_even_at_market_hours():
    assert market_phase(_at(10, 0, day=15)) == "closed"  # Saturday
