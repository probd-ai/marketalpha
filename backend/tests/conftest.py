import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def trading_days():
    # 60 business days starting 2024-01-01
    return pd.bdate_range("2024-01-01", periods=60)


@pytest.fixture
def linear_series(trading_days):
    # deterministic upward line, slope 1.0/day, perfect fit (R^2 == 1)
    return pd.Series(np.arange(len(trading_days), dtype=float) + 100.0, index=trading_days)
