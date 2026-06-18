from datetime import date
import numpy as np
import pandas as pd
from app.strategies.signals import rs_line, slope_r2, trailing_slope_r2, rank_blend, roc


def test_rs_line_divides_aligned():
    idx = pd.bdate_range("2024-01-01", periods=3)
    close = pd.Series([10.0, 20.0, 30.0], index=idx)
    nifty = pd.Series([10.0, 10.0, 10.0], index=idx)
    assert rs_line(close, nifty).tolist() == [1.0, 2.0, 3.0]


def test_slope_r2_perfect_line():
    slope, r2 = slope_r2(np.array([1.0, 2.0, 3.0, 4.0]))
    assert round(slope, 6) == 1.0 and round(r2, 6) == 1.0


def test_slope_r2_insufficient():
    s, r = slope_r2(np.array([5.0]))
    assert np.isnan(s) and np.isnan(r)


def test_trailing_slope_r2_uses_last_lookback(linear_series):
    as_of = linear_series.index[40].date()
    slope, r2 = trailing_slope_r2(linear_series, as_of, lookback=22)
    assert round(slope, 6) == 1.0 and round(r2, 6) == 1.0


def test_rank_blend_orders_by_average_rank():
    slopes = pd.Series({"A": 0.1, "B": 0.2, "C": 0.3})
    r2s = pd.Series({"A": 0.5, "B": 0.6, "C": 0.7})
    blended = rank_blend(slopes, r2s)
    # A lowest on both -> rank 1+1 avg; C highest on both -> rank 3+3 avg
    assert blended["C"] > blended["B"] > blended["A"]


def test_roc_22_bars(linear_series):
    as_of = linear_series.index[30].date()
    expected = linear_series.iloc[30] / linear_series.iloc[8] - 1
    assert round(roc(linear_series, as_of, 22), 9) == round(expected, 9)
