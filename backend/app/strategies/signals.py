from datetime import date

import numpy as np
import pandas as pd
from scipy import stats


def rs_line(close: pd.Series, nifty: pd.Series) -> pd.Series:
    aligned_close, aligned_nifty = close.align(nifty, join="inner")
    return aligned_close / aligned_nifty


def slope_r2(values: np.ndarray) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return float("nan"), float("nan")
    if np.allclose(values, values[0]):   # zero variance: no trend, no fit
        return 0.0, 0.0
    x = np.arange(len(values), dtype=float)
    result = stats.linregress(x, values)
    return float(result.slope), float(result.rvalue ** 2)


def trailing_slope_r2(series: pd.Series, as_of: date, lookback: int) -> tuple[float, float]:
    window = series[series.index.date <= as_of].tail(lookback)
    if len(window) < lookback:
        return float("nan"), float("nan")
    return slope_r2(window.to_numpy())


def rank_blend(slopes: pd.Series, r2s: pd.Series) -> pd.Series:
    idx = slopes.index.intersection(r2s.index)
    s_rank = slopes.loc[idx].rank(method="average")
    r_rank = r2s.loc[idx].rank(method="average")
    return 0.5 * s_rank + 0.5 * r_rank


def roc(series: pd.Series, as_of: date, window: int) -> float:
    hist = series[series.index.date <= as_of]
    if len(hist) < window + 1:
        return float("nan")
    return float(hist.iloc[-1] / hist.iloc[-1 - window] - 1)
