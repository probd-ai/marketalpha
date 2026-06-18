from datetime import date
import pandas as pd
from app.backtest.portfolio import simulate


def test_single_holding_tracks_price():
    idx = pd.bdate_range("2024-01-01", periods=4)
    panel = pd.DataFrame({"A": [100.0, 110.0, 121.0, 121.0]}, index=idx)
    res = simulate(panel, [(idx[0].date(), {"A": 1.0})], start_capital=1000.0, cost_bps=0.0)
    # fully invested in A from day 0; +10% then +10%
    assert round(res.equity.iloc[0], 6) == 1000.0
    assert round(res.equity.iloc[1], 6) == 1100.0
    assert round(res.equity.iloc[2], 6) == 1210.0


def test_costs_reduce_equity():
    idx = pd.bdate_range("2024-01-01", periods=2)
    panel = pd.DataFrame({"A": [100.0, 100.0]}, index=idx)
    no_cost = simulate(panel, [(idx[0].date(), {"A": 1.0})], 1000.0, 0.0)
    with_cost = simulate(panel, [(idx[0].date(), {"A": 1.0})], 1000.0, 10.0)  # 10 bps
    assert with_cost.equity.iloc[-1] < no_cost.equity.iloc[-1]
