import asyncio
from datetime import datetime, timedelta
import engine.stateless as engine_stateless
from engine.stateless import (build_price_series, build_indicator_series,
                        calculate_stock_momentum, calculate_stock_momentum_fast)


def _price_hist():
    # date_str -> {symbol -> {close_price,...}}; intentionally out of order
    return {
        "2024-01-03": {"AAA": {"close_price": 12.0}, "BBB": {"close_price": 50.0}},
        "2024-01-01": {"AAA": {"close_price": 10.0}},
        "2024-01-02": {"AAA": {"close_price": 11.0}, "BBB": {"close_price": 49.0}},
    }


def test_build_price_series_sorts_and_splits_by_symbol():
    s = build_price_series(_price_hist())
    aaa_dates, aaa_closes = s["AAA"]
    assert aaa_dates == [datetime(2024, 1, 1), datetime(2024, 1, 2), datetime(2024, 1, 3)]
    assert aaa_closes == [10.0, 11.0, 12.0]
    bbb_dates, bbb_closes = s["BBB"]            # BBB missing on 01-01
    assert bbb_dates == [datetime(2024, 1, 2), datetime(2024, 1, 3)]
    assert bbb_closes == [49.0, 50.0]


def test_build_indicator_series_empty_and_basic():
    assert build_indicator_series(None) == {}
    assert build_indicator_series({}) == {}
    ind = {"2024-01-02": {"AAA": {"truevx_score": 5}},
           "2024-01-01": {"AAA": {"truevx_score": 4}}}
    s = build_indicator_series(ind)
    dates, data = s["AAA"]
    assert dates == [datetime(2024, 1, 1), datetime(2024, 1, 2)]
    assert data == [{"truevx_score": 4}, {"truevx_score": 5}]


def _ramp_price_hist(start="2023-06-01", n=300):
    # AAA rises 100->..., BBB falls; daily calendar dates (weekends included is fine,
    # the function filters by calendar-day window identically for old and new).
    base = datetime.strptime(start, "%Y-%m-%d")
    hist = {}
    for i in range(n):
        ds = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        hist[ds] = {"AAA": {"close_price": 100.0 + i},
                    "BBB": {"close_price": 200.0 - 0.3 * i}}
    return hist


def _ind_hist(start="2023-06-01", n=60):
    base = datetime.strptime(start, "%Y-%m-%d")
    hist = {}
    for i in range(n):
        ds = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        hist[ds] = {"AAA": {"truevx_score": 50 + i, "mean_short": 40 + i,
                            "mean_mid": 30 + i, "mean_long": 20 + i}}
    return hist


def _old(symbol, current, price_hist, method, ind_hist=None):
    return asyncio.run(engine_stateless.calculate_stock_momentum(
        symbol, current, price_hist, ind_hist, method))


def test_fast_matches_legacy_price_methods():
    price_hist = _ramp_price_hist()
    pseries = build_price_series(price_hist)
    current = "2024-01-15"
    cur_obj = datetime.strptime(current, "%Y-%m-%d")
    for method in ["20_day_return", "price_roc_66d", "price_roc_222d",
                   "risk_adjusted", "technical"]:
        for sym in ["AAA", "BBB"]:
            old = _old(sym, current, price_hist, method)
            new = calculate_stock_momentum_fast(pseries.get(sym), None, cur_obj, method)
            assert abs(old - new) < 1e-9, f"{method}/{sym}: old={old} new={new}"


def test_fast_matches_legacy_indicator_methods():
    price_hist = _ramp_price_hist(n=60)
    ind_hist = _ind_hist()
    iseries = build_indicator_series(ind_hist)
    current = "2023-07-30"
    cur_obj = datetime.strptime(current, "%Y-%m-%d")
    for method in ["truevx_roc", "short_mean_roc", "mid_mean_roc",
                   "long_mean_roc", "stock_score_roc"]:
        old = _old("AAA", current, price_hist, method, ind_hist)
        new = calculate_stock_momentum_fast(None, iseries.get("AAA"), cur_obj, method)
        assert abs(old - new) < 1e-9, f"{method}: old={old} new={new}"


def test_fast_edge_cases():
    cur = datetime(2024, 1, 15)
    assert calculate_stock_momentum_fast(None, None, cur, "20_day_return") == 0.0
    assert calculate_stock_momentum_fast(([cur], [10.0]), None, cur, "20_day_return") == 0.0  # <2
    # lookback price 0 -> 0.0
    dates = [cur - timedelta(days=2), cur - timedelta(days=1), cur]
    assert calculate_stock_momentum_fast((dates, [0.0, 5.0, 10.0]), None, cur, "20_day_return") == 0.0


def test_fast_matches_legacy_price_roc_22d():
    price_hist = _ramp_price_hist()
    pseries = build_price_series(price_hist)
    current = "2024-01-15"
    cur_obj = datetime.strptime(current, "%Y-%m-%d")
    for sym in ["AAA", "BBB"]:
        old = _old(sym, current, price_hist, "price_roc_22d")
        new = calculate_stock_momentum_fast(pseries.get(sym), None, cur_obj, "price_roc_22d")
        assert abs(old - new) < 1e-9, f"price_roc_22d/{sym}: old={old} new={new}"
    # rising AAA has positive ROC, falling BBB negative
    assert calculate_stock_momentum_fast(pseries["AAA"], None, cur_obj, "price_roc_22d") > 0
    assert calculate_stock_momentum_fast(pseries["BBB"], None, cur_obj, "price_roc_22d") < 0


def test_low_suffix_selects_lowest_roc():
    # AAA rises, BBB falls: "_low" ranking must pick BBB first, plain picks AAA.
    from engine.stateless import select_top_stocks_by_momentum
    price_hist = _ramp_price_hist()
    pseries = build_price_series(price_hist)
    current = "2024-01-15"
    cur_obj = datetime.strptime(current, "%Y-%m-%d")
    qualified = [{"symbol": "AAA", "truevx_score": 50},
                 {"symbol": "BBB", "truevx_score": 50}]

    def _select(method):
        sel, _, _ = asyncio.run(select_top_stocks_by_momentum(
            qualified_stocks=qualified, current_holdings={},
            price_data_history=price_hist, indicator_data_history={},
            current_date=current, max_holdings=1, momentum_method=method,
            price_series=pseries, indicator_series={}, current_date_obj=cur_obj))
        return [s["symbol"] for s in sel]

    assert _select("price_roc_22d") == ["AAA"]
    assert _select("price_roc_22d_low") == ["BBB"]
    assert _select("price_roc_222d_low") == ["BBB"]
    assert _select("20_day_return_low") == ["BBB"]
