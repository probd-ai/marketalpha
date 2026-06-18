from datetime import date
import numpy as np
import pandas as pd
from app.strategies.base import BacktestConfig
from app.strategies.rs_momentum import RSMomentumStrategy


def _panel():
    idx = pd.bdate_range("2024-01-01", periods=40)
    # 6 symbols with increasing steepness; NIFTY flat at 100
    data = {f"S{i}": 100.0 + i * np.arange(len(idx)) for i in range(6)}
    return pd.DataFrame(data, index=idx), pd.Series(100.0, index=idx)


def test_select_picks_equal_weighted_top_n():
    panel, nifty = _panel()
    cfg = BacktestConfig(start=date(2024, 1, 1), end=date(2024, 3, 1),
                         lookback=22, top_pct=0.5, top_n=2, nominal_size=6)
    strat = RSMomentumStrategy()
    weights = strat.select(panel, nifty, set(panel.columns), panel.index[39].date(), cfg)
    assert len(weights) == 2
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert all(abs(w - 0.5) < 1e-9 for w in weights.values())
    # steepest movers (highest slope & ROC) are S5, S4
    assert set(weights) == {"S5", "S4"}


def test_select_excludes_insufficient_history():
    panel, nifty = _panel()
    panel = panel.copy()
    panel.loc[panel.index[:38], "S5"] = np.nan   # only 2 valid points
    cfg = BacktestConfig(start=date(2024, 1, 1), end=date(2024, 3, 1),
                         lookback=22, top_pct=0.5, top_n=2, nominal_size=6)
    weights = RSMomentumStrategy().select(panel, nifty, set(panel.columns),
                                          panel.index[39].date(), cfg)
    assert "S5" not in weights
