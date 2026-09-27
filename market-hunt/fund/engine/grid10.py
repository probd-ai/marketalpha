"""Grid-10 value-core sleeve (spec §4.2) — mirrors rotation_roc.run with the grid10.py pick exactly (H5–H7).

Daily step:
  1. sell holdings that are not index members today at today's open (or the last close once the stock has
     stopped trading; a halted exit waits);
  2. on a rebalance day: sell holdings whose grid turned DOWN while their 22-day return is POSITIVE, keep the rest,
     fill up to N with the lowest-22d names in grid-UP state; size new money into gaps to equity/N (pro-rata when
     short), spread any remainder equally; never trim;
Float operations follow the harness order so the golden master matches to rounding. Symbols are always visited in
sorted order (the harness used set order, which changes per process — see tests/fund_desk/oracle.py).
"""
from __future__ import annotations

import math
from dataclasses import replace

import pandas as pd

from .costs import Costs
from .market import Market
from .types import Lot, Order, Reason, SleeveState


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x)


class Grid10:
    kind = "grid10"

    def __init__(self, n: int = 10):
        self.n = n

    # -- selection ---------------------------------------------------------------------------------------------
    def pick(self, m: Market, prev, pool: list[str], held: list[str]) -> list[str]:
        sells = {s for s in held if s in pool and m.grid.at[prev, s] == -1
                 and not _nan(m.roc.at[prev, s]) and m.roc.at[prev, s] > 0}
        keep = [s for s in held if s not in sells]
        filtered = [s for s in pool if m.grid.at[prev, s] == 1 and not _nan(m.roc.at[prev, s]) and s not in keep]
        cands = list(m.roc.loc[prev, filtered].sort_values().index)
        return sorted(keep + cands[: max(0, self.n - len(keep))])

    # -- one session -------------------------------------------------------------------------------------------
    def on_day(self, st: SleeveState, m: Market, costs: Costs, cal: pd.DatetimeIndex, i: int, prev,
               rebalance: bool) -> list[Order]:
        d = cal[i]
        orders: list[Order] = []
        pend = st.params.setdefault("pending_exits", [])

        def sell(sym, px, reason):
            q = st.lots.pop(sym)[0].qty
            proceeds = costs.sell_proceeds(q, px)
            st.cash += proceeds
            orders.append(Order(sym, "SELL", q, float(px), proceeds, reason))

        def buy(sym, v, px, reason):
            q = costs.buy_qty(v, px)
            st.cash -= v
            old = st.lots.get(sym)
            st.lots[sym] = [Lot(sym, (old[0].qty if old else 0.0) + q, old[0].opened if old else d.date(), 0)]
            orders.append(Order(sym, "BUY", q, float(px), v, reason))

        # 1) index exits: holdings that are not members today (the harness queued these at yesterday's close using
        #    today's membership — same set, but queueing here keeps checkpoints exact), sold at today's open
        if i > 0:
            today_members = m.members(d.date())
            for s in st.lots:
                if s not in today_members and s not in pend:
                    pend.append(s)
        for sym in sorted(pend):                              # canonical order: deterministic across runs
            if sym not in st.lots:
                pend.remove(sym)
                continue
            o = m.opens.at[d, sym]
            if _nan(o):
                lb = m.last_bar.get(sym)
                if lb is None or lb >= d:
                    continue                                  # halted; retry tomorrow
                o = m.closes.at[lb, sym]
            sell(sym, o, Reason.INDEX_EXIT)
            pend.remove(sym)

        # 2) rebalance
        if rebalance or i == 0:
            members = sorted(m.members(d.date()))
            pool = [s for s in members if s in m.roc.columns and not _nan(m.roc.at[prev, s])
                    and not _nan(m.opens.at[d, s])]
            held_before = set(st.lots)
            pick = self.pick(m, prev, pool, sorted(held_before))
            px_open = {s: (m.opens.at[d, s] if not _nan(m.opens.at[d, s]) else m.cclose.at[prev, s]) for s in st.lots}
            equity = st.cash + sum(l[0].qty * px_open[s] for s, l in st.lots.items())
            target = equity / max(len(pick), 1)
            for s in [s for s in st.lots if s not in pick]:
                if not _nan(m.opens.at[d, s]):
                    sell(s, m.opens.at[d, s], Reason.GRID_DOWN_POSITIVE_MONTH)
            tradable = [s for s in pick if not _nan(m.opens.at[d, s])]
            gaps = {s: max(target - (st.lots[s][0].qty if s in st.lots else 0.0) * m.opens.at[d, s], 0.0)
                    for s in tradable}
            total_gap = sum(gaps.values())
            budget = st.cash
            alloc = {s: (budget * g / total_gap if total_gap > budget else g) for s, g in gaps.items()} \
                if total_gap > 0 else {s: 0.0 for s in tradable}
            rest = budget - sum(alloc.values())
            if rest > 1e-6 and tradable:
                for s in tradable:
                    alloc[s] += rest / len(tradable)
            for s, v in alloc.items():
                if v > 1e-6:
                    reason = (Reason.NEW_LOWEST_22D if s not in held_before
                              else Reason.TOPUP_TO_TARGET if gaps[s] > 0 else Reason.REMAINDER_SPREAD)
                    buy(s, v, m.opens.at[d, s], reason)

        return orders
