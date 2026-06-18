import numpy as np
import pandas as pd
from app.backtest.metrics import compute_metrics


def test_total_return_and_drawdown():
    idx = pd.bdate_range("2024-01-01", periods=5)
    equity = pd.Series([100, 110, 105, 120, 130], index=idx, dtype=float)
    bench = pd.Series([100, 100, 100, 100, 110], index=idx, dtype=float)
    m = compute_metrics(equity, bench, turnover=1.0)
    assert round(m["total_return"], 6) == 0.30
    assert round(m["benchmark_total_return"], 6) == 0.10
    assert round(m["alpha"], 6) == 0.20
    # max drawdown: 110 -> 105 = -4.5454...%
    assert round(m["max_drawdown"], 4) == -0.0455


def test_degenerate_single_point():
    idx = pd.bdate_range("2024-01-01", periods=1)
    s = pd.Series([100.0], index=idx)
    m = compute_metrics(s, s, turnover=0.0)
    assert m["total_return"] == 0.0 and m["sharpe"] == 0.0
