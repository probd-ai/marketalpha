"""Project the next session so a run can produce orders BEFORE that session's prices exist.

A run is computed after the close of the signal day S. Orders fill at the next session's open F, which is not in the
data yet. The projection appends one synthetic session F whose open and close equal S's close (the reference price).
Signals for F are S's signals (unchanged rows), and membership on F is membership on S. The real open replaces the
projection automatically on the next run, once F's prices are loaded.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .market import Market


def next_session(s: pd.Timestamp, holidays: tuple = ()) -> pd.Timestamp:
    return pd.Timestamp(np.busday_offset(s.date(), 1, roll="forward", holidays=list(holidays)))


def project(m: Market, s: pd.Timestamp, f: pd.Timestamp) -> Market:
    ref = m.cclose.loc[s]
    opens = pd.concat([m.opens.loc[:s], ref.to_frame(f).T])
    closes = pd.concat([m.closes.loc[:s], ref.to_frame(f).T])
    members = lambda d, _m=m.members, _s=s.date(), _f=f.date(): _m(_s) if d >= _f else _m(d)
    return Market(opens, closes, m.bench, members, m.roc, grid=m.grid, elig=m.elig,
                  traded_value=m.traded_value, indicator_last=m.indicator_last)
