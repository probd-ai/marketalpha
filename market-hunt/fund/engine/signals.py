"""Signals derived from prices and TrueVX fields. Each function reproduces the research harness exactly (H1, H2)."""
from __future__ import annotations

import numpy as np
import pandas as pd

ALPHA_RULES = {"truevx_score": 90, "mean_short": 80, "mean_mid": 60, "mean_long": 50}


def roc(closes: pd.DataFrame, n: int = 22) -> pd.DataFrame:
    """H1: n-bar return, shifted over each stock's own valid bars (a halted day is skipped, not counted)."""
    return closes.apply(lambda c: c / c.dropna().shift(n).reindex(c.index) - 1)


def grid_state(mean_long: pd.DataFrame, step: float = 5) -> pd.DataFrame:
    """H2: +1 after moving into a higher `step` band, -1 after a lower one, held until the next band change.
    Computed on the indicator's own non-NaN days, then forward-filled onto the frame's index."""
    def one(c: pd.Series) -> pd.Series:
        z = np.floor(c.dropna() / step)
        move = np.sign(z.diff()).replace(0, np.nan)
        return move.ffill().reindex(c.index).ffill()
    return mean_long.apply(one)


def alpha_eligible(fields: dict[str, pd.DataFrame], index, columns, rules: dict = ALPHA_RULES) -> pd.DataFrame:
    """Every rule strictly above its threshold; a missing value is not eligible."""
    elig = pd.DataFrame(True, index=index, columns=columns)
    for f, thr in rules.items():
        elig &= (fields[f].reindex(index=index, columns=columns) > thr).fillna(False)
    return elig
