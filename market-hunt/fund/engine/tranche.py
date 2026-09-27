"""Weekly-tranche momentum sleeve (growth / satellite; spec §4.2) — mirrors weekly_tranche.run_weekly exactly (H8).

Daily step:
  1. sell holdings that are not index members today (at the open, or the last close once trading has stopped);
  2. on a rebalance day, from Alpha-eligible members rank by 22-day return, pick the top `top`;
       a) trim any holding above `max_w` of equity back to it (every lot shrinks pro rata);
       b) per symbol: lots younger than `weeks` rebalances stay, older lots expire; a pick adds a tranche of
          equity/slot_div, capped so the holding stays within max_w; expiry and new tranche net into one order;
       c) net sells, then buys scaled to the cash available;
       d) leftover cash tops up holdings in rank order up to the cap, as fresh tranches;
       e) every lot ages by one rebalance.
Float operations follow the harness order so the golden master matches to rounding. Symbols are always visited in
sorted order (the harness used set order, which changes per process — see tests/fund_desk/oracle.py).
"""
from __future__ import annotations

import math

import pandas as pd

from .costs import Costs
from .market import Market
from .types import Lot, Order, Reason, SleeveState


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x)


class Tranche:
    kind = "tranche"

    def __init__(self, top: int = 5, weeks: int = 8, slot_div: int | None = 40, max_w: float | None = 0.10,
                 deploy_cash: bool = True):
        self.top, self.weeks, self.slot_div, self.max_w, self.deploy_cash = top, weeks, slot_div, max_w, deploy_cash

    def on_day(self, st: SleeveState, m: Market, costs: Costs, cal: pd.DatetimeIndex, i: int, prev,
               rebalance: bool) -> list[Order]:
        d = cal[i]
        orders: list[Order] = []
        slip, br, sr = costs.slip, costs.buy_rate, costs.sell_rate
        tr = st.lots

        def qty(s):
            return sum(t.qty for t in tr.get(s, []))

        # 1) left the index
        members = sorted(m.members(d.date()))                   # canonical order: deterministic across runs
        for s in [s for s in tr if s not in members]:
            o = m.opens.at[d, s]
            if _nan(o):
                lb = m.last_bar.get(s)
                if lb is None or lb >= d:
                    continue
                o = m.closes.at[lb, s]
            q = qty(s)
            v = q * o * (1 - slip) * (1 - sr)
            st.cash += v
            tr.pop(s)
            orders.append(Order(s, "SELL", q, float(o), v, Reason.INDEX_EXIT))

        if not rebalance:
            return orders

        # 2) rebalance
        ok = m.elig.loc[prev] if prev in m.elig.index else pd.Series(dtype=bool)
        pool = [s for s in members if s in m.roc.columns and bool(ok.get(s, False))
                and not _nan(m.roc.at[prev, s]) and not _nan(m.opens.at[d, s])]
        ranked = list(m.roc.loc[prev, pool].sort_values(ascending=False).index)
        picks = ranked[: self.top]
        px = lambda s: m.opens.at[d, s] if not _nan(m.opens.at[d, s]) else m.cclose.at[prev, s]
        equity = st.cash + sum(qty(s) * px(s) for s in tr)
        slot = equity / (self.slot_div or self.top * self.weeks)
        if self.max_w:                                              # a) trim winners back to the cap
            for s in list(tr):
                if _nan(m.opens.at[d, s]):
                    continue
                v = qty(s) * m.opens.at[d, s]
                if v > self.max_w * equity:
                    f = self.max_w * equity / v
                    sold = qty(s) * (1 - f)
                    proceeds = sold * m.opens.at[d, s] * (1 - slip) * (1 - sr)
                    st.cash += proceeds
                    tr[s] = [Lot(t.symbol, t.qty * f, t.opened, t.age) for t in tr[s]]
                    orders.append(Order(s, "SELL", sold, float(m.opens.at[d, s]), proceeds, Reason.TRIM_ABOVE_CAP))
        plan = {}                                                    # b) expiry vs new tranche per symbol
        for s in sorted(set(tr) | set(picks)):
            keep = [t for t in tr.get(s, []) if t.age < self.weeks]
            expiring = qty(s) - sum(t.qty for t in keep)
            new_q = slot / px(s) if s in picks else 0.0
            if self.max_w and new_q > 0:
                room = self.max_w * equity - sum(t.qty for t in keep) * px(s)
                new_q = max(0.0, min(slot, room)) / px(s)
            plan[s] = (keep, expiring, new_q)
        for s, (keep, expiring, new_q) in plan.items():             # c) net sells first
            net = new_q - expiring
            o = m.opens.at[d, s]
            if net <= 0 and not _nan(o):
                if net < 0:
                    proceeds = -net * o * (1 - slip) * (1 - sr)
                    st.cash += proceeds
                    orders.append(Order(s, "SELL", -net, float(o), proceeds, Reason.TRANCHE_EXPIRED))
                tr[s] = keep + ([Lot(s, new_q, d.date(), 0)] if new_q > 0 else [])
                if not tr[s]:
                    tr.pop(s)
            elif net <= 0 and expiring > 0:                         # halted: the expiring tranche waits
                tr[s] = keep + [Lot(s, expiring, d.date(), self.weeks)]
        buys = {s: v for s, v in plan.items() if v[2] - v[1] > 0}
        need = sum((new_q - expiring) * m.opens.at[d, s] for s, (_, expiring, new_q) in buys.items())
        scale = min(1.0, st.cash / (need * (1 + slip) * (1 + br))) if need > 0 else 0.0
        for s, (keep, expiring, new_q) in buys.items():
            net = (new_q - expiring) * scale
            o = m.opens.at[d, s]
            cost = net * o * (1 + slip) * (1 + br)
            st.cash -= cost
            tr[s] = keep + [Lot(s, expiring + net, d.date(), 0)]
            orders.append(Order(s, "BUY", net, float(o), cost,
                                Reason.TRANCHE_ROLL if expiring > 0 else Reason.TOP5_NEW_TRANCHE))
        if self.deploy_cash and st.cash > 0.005 * equity:            # d) leftover cash, best-ranked first
            limit = (self.max_w or 1.0 / self.top) * equity
            held_ranked = [s for s in ranked if s in tr] + [s for s in tr if s not in ranked]
            for s in held_ranked:
                if st.cash <= 0.005 * equity:
                    break
                o = m.opens.at[d, s]
                if _nan(o):
                    continue
                room = limit - qty(s) * o
                v = min(st.cash / (1 + slip) / (1 + br), room)
                if v > 1e-6:
                    q_add = v / o
                    cost = v * (1 + slip) * (1 + br)
                    st.cash -= cost
                    tr[s].append(Lot(s, q_add, d.date(), 0))
                    orders.append(Order(s, "BUY", q_add, float(o), cost, Reason.CASH_DEPLOY))
        for s in tr:                                                 # e) age every lot one rebalance
            tr[s] = [Lot(t.symbol, t.qty, t.opened, t.age + 1) for t in tr[s]]
        return orders
