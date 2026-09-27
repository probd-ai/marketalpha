"""Day-by-day replay of one sleeve, with checkpoints (spec §5).

replay_sleeve walks the trading calendar, asks the sleeve to act on each session (index exits every day, rules on
schedule days) and values the book at the close (H9). Passing `resume=(last_done_date, state)` continues from a
saved checkpoint and gives exactly the result of a full replay.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

import pandas as pd

from .calendar import prev_session, trading_days
from .costs import Costs
from .market import Market
from .types import Order, SleeveState


@dataclass
class ReplayResult:
    equity: pd.Series                       # end-of-day value per session
    state: SleeveState                      # state after the last session
    orders: dict                            # session -> list[Order]
    last_date: pd.Timestamp


def replay_sleeve(sleeve, m: Market, costs: Costs, start, end, schedule: set, capital: float,
                  resume: tuple | None = None) -> ReplayResult:
    cal = trading_days(m.closes[m.bench], start, end)
    if resume is None:
        st, first = SleeveState(cash=capital, params={}), 0
    else:
        last_done, saved = resume
        st = copy.deepcopy(saved)
        first = int(cal.searchsorted(pd.Timestamp(last_done), side="right"))
    eq, orders = {}, {}
    for i in range(first, len(cal)):
        d = cal[i]
        prev = prev_session(cal, m.all_dates, i)
        o = sleeve.on_day(st, m, costs, cal, i, prev, d in schedule)
        if o:
            orders[d] = o
        held = sum(st.qty(s) * m.cclose.at[d, s] for s in st.lots)
        eq[d] = st.cash + held
    return ReplayResult(pd.Series(eq, dtype=float), st, orders, cal[-1] if len(cal) else None)
