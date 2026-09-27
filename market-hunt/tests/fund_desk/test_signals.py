import numpy as np
import pandas as pd

from fund.engine.signals import ALPHA_RULES, alpha_eligible, grid_state, roc

IDX = pd.date_range("2021-01-01", periods=30, freq="B")


def test_roc_shifts_over_valid_bars_only():
    c = pd.Series(np.arange(1.0, 31.0), index=IDX)
    c.iloc[5] = np.nan                                   # a halted day
    r = roc(pd.DataFrame({"A": c}), n=3)["A"]
    valid = c.dropna()
    # on the 4th valid bar after the gap the base is 3 valid bars back, skipping the NaN
    d = valid.index[7]
    assert r[d] == valid.iloc[7] / valid.iloc[4] - 1
    assert np.isnan(r.iloc[5])


def test_grid_state_holds_last_band_change():
    ml = pd.DataFrame({"A": [48, 52, 51, 47, 49]}, index=IDX[:5], dtype=float)
    assert grid_state(ml)["A"].tolist()[1:] == [1, 1, -1, -1]
    assert np.isnan(grid_state(ml)["A"].iloc[0])


def test_grid_state_skips_missing_indicator_days():
    ml = pd.DataFrame({"A": [48, 52, np.nan, 47]}, index=IDX[:4], dtype=float)
    assert grid_state(ml)["A"].tolist()[1:] == [1, 1, -1]


def test_alpha_needs_all_four_strictly_above():
    f = {k: pd.DataFrame({"A": [v + 1, v], "B": [np.nan, v + 5]}, index=IDX[:2]) for k, v in ALPHA_RULES.items()}
    e = alpha_eligible(f, IDX[:2], ["A", "B"])
    assert e["A"].tolist() == [True, False]      # equal to threshold is not enough
    assert e["B"].tolist() == [False, True]      # NaN -> not eligible
