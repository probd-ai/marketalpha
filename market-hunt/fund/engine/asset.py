"""Asset sleeve (GOLDBEES, MON100): invest everything in one instrument on the first session and hold.
Only the fund layer's monthly reset changes its size (spec §4.2)."""
from __future__ import annotations

import pandas as pd

from .costs import Costs
from .market import Market
from .types import Lot, Order, Reason, SleeveState


class Asset:
    kind = "asset"

    def __init__(self, symbol: str):
        self.symbol = symbol

    def on_day(self, st: SleeveState, m: Market, costs: Costs, cal: pd.DatetimeIndex, i: int, prev,
               rebalance: bool) -> list[Order]:
        if st.lots or st.cash <= 0:
            return []
        d = cal[i]
        px = m.opens.at[d, self.symbol]
        if pd.isna(px):
            return []                                   # no open today: stay in cash until it trades
        v = st.cash
        q = costs.buy_qty(v, px)
        st.cash = 0.0
        st.lots[self.symbol] = [Lot(self.symbol, q, d.date(), 0)]
        return [Order(self.symbol, "BUY", q, float(px), v, Reason.MONTHLY_RESET)]
