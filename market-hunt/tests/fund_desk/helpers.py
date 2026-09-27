"""Tiny synthetic markets for engine unit tests."""
import numpy as np
import pandas as pd

from fund.engine.market import Market

BENCH = "NIFTY"


def make_market(symbols, days=30, seed=0, members=None, grid=None, roc=None, elig=None, opens=None, closes=None):
    idx = pd.bdate_range("2021-01-04", periods=days)
    rng = np.random.default_rng(seed)
    if closes is None:
        closes = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (days, len(symbols))), axis=0)),
                              index=idx, columns=symbols)
    if opens is None:
        opens = closes.shift(1).fillna(closes)
    closes = closes.copy()
    closes[BENCH] = 1.0
    opens = opens.copy()
    opens[BENCH] = 1.0
    mem = members or (lambda d: set(symbols))
    roc = roc if roc is not None else pd.DataFrame(0.0, index=idx, columns=symbols)
    return Market(opens, closes, BENCH, mem, roc, grid=grid, elig=elig)


def frame(idx, **cols):
    return pd.DataFrame({k: (v if hasattr(v, "__len__") else [v] * len(idx)) for k, v in cols.items()}, index=idx,
                        dtype=float)
