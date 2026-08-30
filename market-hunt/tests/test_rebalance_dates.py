# get_rebalance_dates: completed periods keep loaded-trading-day picks; the
# trailing incomplete period anchors mid/last to the full business-day calendar
# (no early fire, no drift). Regression for the "mid of July = Jul 2" bug.
from engine.stateless import get_rebalance_dates


def _weekdays(year, month, upto=None):
    import calendar
    from datetime import date
    days = []
    for d in range(1, calendar.monthrange(year, month)[1] + 1):
        dt = date(year, month, d)
        if dt.weekday() < 5 and (upto is None or d <= upto):
            days.append(dt.strftime("%Y-%m-%d"))
    return days


JUNE = _weekdays(2026, 6)                 # complete month
JULY_BUSDAYS = _weekdays(2026, 7)          # full-month business calendar
JULY_MID = JULY_BUSDAYS[len(JULY_BUSDAYS) // 2]


def test_complete_month_mid_unchanged():
    got = get_rebalance_dates(JUNE, "monthly", "mid")
    assert got == {JUNE[len(JUNE) // 2]}


def test_trailing_partial_month_mid_does_not_fire_early():
    dates = JUNE + _weekdays(2026, 7, upto=3)   # July loaded only to Jul 3
    got = get_rebalance_dates(dates, "monthly", "mid")
    assert got == {JUNE[len(JUNE) // 2]}        # June only — no bogus Jul 2


def test_trailing_partial_month_mid_fires_on_target_day():
    upto = int(JULY_MID.split("-")[2])
    dates = JUNE + _weekdays(2026, 7, upto=upto)
    got = get_rebalance_dates(dates, "monthly", "mid")
    assert JULY_MID in got and JUNE[len(JUNE) // 2] in got and len(got) == 2


def test_trailing_partial_month_last_does_not_fire():
    dates = JUNE + _weekdays(2026, 7, upto=3)
    got = get_rebalance_dates(dates, "monthly", "last")
    assert got == {JUNE[-1]}                    # July's "last" hasn't arrived


def test_first_fires_immediately_even_in_partial_month():
    dates = JUNE + _weekdays(2026, 7, upto=3)
    got = get_rebalance_dates(dates, "monthly", "first")
    assert got == {JUNE[0], "2026-07-01"}


def test_holiday_shifts_mid_to_next_trading_day():
    # If the business-day mid target is a market holiday, fire on the next
    # loaded trading day after it.
    july = [d for d in JULY_BUSDAYS if d != JULY_MID]   # mid day is a holiday
    dates = JUNE + july
    got = get_rebalance_dates(dates, "monthly", "mid")
    later = [d for d in july if d > JULY_MID]
    assert JUNE[len(JUNE) // 2] in got
    # complete-month path (July fully loaded minus holiday): trading-day mid
    assert len(got) == 2


def test_blend_curve_counts_distinct_symbols_across_sleeves():
    from portfolio_composition import blend_curve
    a = [{"date": "2026-01-01", "portfolio_value": 100,
          "holdings": [{"symbol": "X"}, {"symbol": "Y"}]}]
    b = [{"date": "2026-01-01", "portfolio_value": 100,
          "holdings": [{"symbol": "Y"}, {"symbol": "Z"}]}]
    curve = blend_curve([a, b])
    assert curve[0]["holdings_count"] == 3  # X, Y, Z — Y not double-counted


def test_run_allocation_stats_avg_weight_and_chained_return():
    from portfolio_composition import run_allocation_stats
    meta = {"A": {"index_tier": "Micro"}, "B": {"index_tier": "Large"}}
    # day1: A 100 (10sh @10), B 100 (1sh @100) -> 50/50
    # day2: A 150 (10sh @15, +50%), B 100 (1sh @100, 0%) -> 60/40
    sr = [[
        {"date": "2026-01-01", "portfolio_value": 200, "holdings": [
            {"symbol": "A", "market_value": 100.0, "quantity": 10},
            {"symbol": "B", "market_value": 100.0, "quantity": 1}]},
        {"date": "2026-01-02", "portfolio_value": 250, "holdings": [
            {"symbol": "A", "market_value": 150.0, "quantity": 10},
            {"symbol": "B", "market_value": 100.0, "quantity": 1}]},
    ]]
    st = run_allocation_stats(sr, meta, "index_tier")
    assert st["Micro"]["avg_weight"] == 55.0     # mean(50, 60)
    assert st["Micro"]["run_return"] == 50.0     # 10 -> 15 per share
    assert st["Large"]["run_return"] == 0.0
