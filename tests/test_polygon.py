"""Offline tests for bot/polygon.py parsers - no network, no key needed."""

import datetime as dt

from bot.polygon import (df_from_aggs, movers_from_snapshot, news_items,
                         overview_fields, parse_last_nbbo, parse_last_trade,
                         reverse_splits_from, spread_pct_from_quote)

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


def _snap_ticker(sym, pct, price, vol, vw):
    return {"ticker": sym, "todaysChangePerc": pct,
            "day": {"c": price, "v": vol, "vw": vw},
            "lastTrade": {"p": price}}


def test_movers_from_snapshot_filters_and_sorts():
    js = {"tickers": [
        _snap_ticker("ABC", 25.0, 1.50, 3_000_000, 1.40),   # keep (dvol 4.2M)
        _snap_ticker("BIGG", 50.0, 12.0, 9_000_000, 11.0),  # price above band
        _snap_ticker("TINY", 40.0, 0.90, 400_000, 0.90),    # dvol 0.36M < 1M
        _snap_ticker("MEH", 4.0, 2.00, 5_000_000, 2.00),    # +4% < min_pct
        _snap_ticker("XYZ", 90.0, 0.55, 8_000_000, 0.50),   # keep (dvol 4M)
        _snap_ticker("HYMCW", 80.0, 1.10, 6_000_000, 1.0),  # 5-letter warrant
        _snap_ticker("AACIU", 70.0, 2.10, 6_000_000, 2.0),  # 5-letter unit
        _snap_ticker("ABCDR", 60.0, 1.10, 6_000_000, 1.0),  # 5-letter rights
        {"ticker": "UNIT.W", "todaysChangePerc": 99.0,       # non-alpha symbol
         "day": {"c": 1.0, "v": 9e6, "vw": 1.0}, "lastTrade": {"p": 1.0}},
    ]}
    got = movers_from_snapshot(js, min_pct=10.0, lo=0.20, hi=7.0, min_dvol_m=1.0)
    assert [m["symbol"] for m in got] == ["XYZ", "ABC"]  # hottest first
    assert got[0]["pct"] == 90.0 and got[0]["dollar_vol_m"] == 4.0


def test_movers_premarket_volume_fallback():
    # Before the open day.v is 0; min.av (accumulated incl premarket) must
    # keep a heavy premarket gapper visible to the 8:20 capture.
    js = {"tickers": [{
        "ticker": "GAP", "todaysChangePerc": 45.0,
        "day": {"c": 0, "v": 0, "vw": 0},
        "min": {"av": 4_000_000},
        "lastTrade": {"p": 1.25},
    }]}
    got = movers_from_snapshot(js, min_pct=10.0, lo=0.20, hi=7.0, min_dvol_m=1.0)
    assert len(got) == 1 and got[0]["symbol"] == "GAP"
    assert got[0]["dollar_vol_m"] == 5.0  # 4M shares * $1.25 lastTrade


def test_movers_from_snapshot_empty():
    assert movers_from_snapshot(None, 10, 0.2, 7, 1) == []
    assert movers_from_snapshot({}, 10, 0.2, 7, 1) == []


def test_overview_fields():
    js = {"results": {"weighted_shares_outstanding": 12_400_000,
                      "market_cap": 45_600_000, "list_date": "2024-11-02",
                      "type": "CS"}}
    got = overview_fields(js)
    assert got == {"shares_out_m": 12.4, "mcap_m": 45.6,
                   "list_date": "2024-11-02", "type": "CS"}
    assert overview_fields(None) == {}
    assert overview_fields({"results": {}}) == {}


def test_reverse_splits_from():
    js = {"results": [
        {"ticker": "RVSE", "split_from": 10, "split_to": 1},   # reverse
        {"ticker": "FWRD", "split_from": 1, "split_to": 4},    # forward
        {"ticker": "BAD"},                                     # malformed
    ]}
    assert reverse_splits_from(js) == {"RVSE"}
    assert reverse_splits_from(None) == set()


def test_news_items_sentiment_matching():
    js = {"results": [{
        "title": "ACME announces reverse split",
        "published_utc": "2026-08-07T12:00:00Z",
        "insights": [{"ticker": "OTHER", "sentiment": "positive"},
                     {"ticker": "ACME", "sentiment": "negative"}],
    }]}
    got = news_items(js, "ACME")
    assert len(got) == 1
    assert got[0]["sentiment"] == "negative"  # OUR ticker's insight, not OTHER's
    assert news_items(None, "ACME") == []
