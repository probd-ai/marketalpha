"""Market inputs for a replay: prices, membership and pre-computed signals. Built by fund.data, consumed by sleeves.

Frames are indexed by session date, one column per symbol; `closes` also carries the bench column that defines the
trading calendar. Nothing here reads a database — the caller passes everything in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import pandas as pd


@dataclass
class Market:
    opens: pd.DataFrame
    closes: pd.DataFrame
    bench: str
    members: Callable[[date], set]
    roc: pd.DataFrame                              # 22-day return (signals.roc)
    grid: Optional[pd.DataFrame] = None            # Grid-10 state (signals.grid_state)
    elig: Optional[pd.DataFrame] = None            # Alpha eligibility (signals.alpha_eligible)
    traded_value: Optional[pd.DataFrame] = None    # rupees traded per session (liquidity check)
    indicator_last: Optional[pd.Series] = None     # last date with TrueVX data per symbol (coverage check)
    cclose: pd.DataFrame = field(init=False, repr=False)
    last_bar: dict = field(init=False, repr=False)

    def __post_init__(self):
        self.cclose = self.closes.ffill()
        self.last_bar = {s: self.closes[s].last_valid_index() for s in self.closes.columns}

    @property
    def all_dates(self) -> pd.DatetimeIndex:
        return self.closes.index
