import numpy as np
import pandas as pd

from fund.engine.calendar import period_start_shifted, prev_session, trading_days, week_session

# Mon 2021-01-04 .. ; drop Wed 01-13 and Tue-Fri of week 3 to make short weeks
DAYS = pd.bdate_range("2021-01-04", "2021-02-26")
DAYS = DAYS.drop([pd.Timestamp("2021-01-13"), pd.Timestamp("2021-01-19"), pd.Timestamp("2021-01-20"),
                  pd.Timestamp("2021-01-21"), pd.Timestamp("2021-01-22"), pd.Timestamp("2021-02-01")])


def test_trading_days_follow_bench_closes():
    bench = pd.Series(1.0, index=DAYS)
    bench.iloc[2] = np.nan
    cal = trading_days(bench, "2021-01-05", "2021-01-12")
    assert pd.Timestamp("2021-01-06") not in cal and cal[0] == pd.Timestamp("2021-01-05")


def test_week_session_clamps_to_last_session_of_short_week():
    s = week_session(DAYS, 4)
    assert pd.Timestamp("2021-01-18") in s            # week 3 has only Monday
    assert pd.Timestamp("2021-01-15") in s            # week 2 (4 sessions): 5th -> last


def test_period_start_shifted_moves_k_sessions_forward():
    s = period_start_shifted(DAYS, "weekly", 1)
    assert pd.Timestamp("2021-01-05") in s            # Mon 04 + 1 session
    assert pd.Timestamp("2021-01-25") in s            # week 3 starts Mon 18, +1 session = Mon 25 (next week)
    m = period_start_shifted(DAYS, "monthly", 0)
    assert pd.Timestamp("2021-02-02") in m            # Feb 1 missing -> first session Feb 2


def test_prev_session_day0_uses_price_dates_before_start():
    cal = DAYS[5:]
    all_dates = DAYS
    assert prev_session(cal, all_dates, 0) == DAYS[4]
    assert prev_session(cal, all_dates, 3) == cal[2]
