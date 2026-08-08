"""Offline tests for bot/polygon.py parsers - no network, no key needed."""

import datetime as dt

from bot.polygon import (df_from_aggs, parse_last_nbbo, parse_last_trade,
                         spread_pct_from_quote)

NOW_NS = 1_800_000_000_000_000_000


def test_parse_last_trade_good():
    js = {"results": {"p": 3.21, "t": NOW_NS - 5_000_000_000}}
    price, age = parse_last_trade(js, NOW_NS)
    assert price == 3.21
    assert 4.9 < age < 5.1


def test_parse_last_trade_future_stamp_rejected():
    js = {"results": {"p": 3.21, "t": NOW_NS + 120_000_000_000}}
    assert parse_last_trade(js, NOW_NS) == (None, None)


def test_parse_last_trade_slight_skew_clamped_to_zero():
    js = {"results": {"p": 3.21, "t": NOW_NS + 2_000_000_000}}
    price, age = parse_last_trade(js, NOW_NS)
    assert price == 3.21 and age == 0.0


def test_parse_last_trade_malformed():
    assert parse_last_trade(None, NOW_NS) == (None, None)
    assert parse_last_trade({}, NOW_NS) == (None, None)
    assert parse_last_trade({"results": {"p": 0, "t": NOW_NS}}, NOW_NS) == (None, None)


def test_spread_pct_normal():
    # bid 1.00 / ask 1.02 -> 2 cents on a 1.01 mid = 1.980%
    assert spread_pct_from_quote({"bid_price": 1.00, "ask_price": 1.02}) == 1.980


def test_spread_pct_rejects_crossed_onesided_zero():
    assert spread_pct_from_quote({"bid_price": 1.02, "ask_price": 1.00}) is None
    assert spread_pct_from_quote({"bid_price": 1.00, "ask_price": 1.00}) is None
    assert spread_pct_from_quote({"bid_price": 0, "ask_price": 1.02}) is None
    assert spread_pct_from_quote({}) is None
    assert spread_pct_from_quote(None) is None


def test_parse_last_nbbo_good():
    # bid 'p'=1.00 / ask 'P'=1.02, quoted 3s ago
    js = {"results": {"p": 1.00, "P": 1.02, "t": NOW_NS - 3_000_000_000}}
    s, age = parse_last_nbbo(js, NOW_NS)
    assert s == 1.980
    assert 2.9 < age < 3.1


def test_parse_last_nbbo_crossed_or_malformed():
    assert parse_last_nbbo({"results": {"p": 1.02, "P": 1.00, "t": NOW_NS}},
                           NOW_NS) == (None, None)
    assert parse_last_nbbo({}, NOW_NS) == (None, None)
    assert parse_last_nbbo(None, NOW_NS) == (None, None)


def test_df_from_aggs_roundtrip():
    js = {"results": [
        {"t": 1_754_500_000_000, "o": 1.0, "h": 1.2, "l": 0.9, "c": 1.1, "v": 5000},
        {"t": 1_754_500_300_000, "o": 1.1, "h": 1.3, "l": 1.0, "c": 1.2, "v": 7000},
    ]}
    df = df_from_aggs(js)
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df.iloc[1]["close"] == 1.2
    # timestamps land tz-aware Eastern (the convention every module uses)
    assert str(df.iloc[0]["date"].tzinfo) == "America/New_York"


def test_df_from_aggs_empty():
    assert df_from_aggs({"results": []}) is None
    assert df_from_aggs({}) is None
    assert df_from_aggs(None) is None
