"""Research: does a "sell the loser, back the winner" overlay improve the weekly-tranche books (Growth / Satellite)?

Rule tested (user proposal, 2026-11-15):
  exit  — sell a whole holding once its close is more than X% below its average cost;
  reinvest — the proceeds go either
      'rank': to the engine's existing cash step (top up holdings in 22-day-return rank order, 10% cap), or
      'best': first into the holding with the largest unrealised gain (up to the 10% cap), the rest by rank.
  check — on the weekly rebalance only ('weekly'), or every session ('daily'; proceeds wait in cash to the rebalance).

Everything else is the production engine (fund.engine.tranche.Tranche) on the production loaders, costs and
schedule. Baseline = the live rule. Each variant runs on all five rebalance weekdays (weekday luck on a 5-name book is
large) and is split into two halves. Research only — nothing here changes the live books.

    .venv/Scripts/python.exe scripts/research_grid/tranche_stop_reinvest.py [--sleeve growth|satellite] [--start 2020-10-01]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pymongo import MongoClient  # noqa: E402

from fund import service  # noqa: E402
from fund.data import BENCH, charge_rates  # noqa: E402
from fund.engine.calendar import trading_days  # noqa: E402
from fund.engine.costs import Costs  # noqa: E402
from fund.engine.factory import base_schedule  # noqa: E402
from fund.engine.replay import replay_sleeve  # noqa: E402
from fund.engine.tranche import Tranche, _nan  # noqa: E402
from fund.engine.types import Lot, Order, Reason  # noqa: E402
from fund.store import FundStore  # noqa: E402

OUT = Path(__file__).with_suffix(".json")


class StopTranche(Tranche):
    """Production Tranche plus a stop-and-reinvest overlay. Cost basis per symbol is tracked from this book's own
    fills (order value includes costs, so the stop is measured net of what was paid)."""

    def __init__(self, stop: float, reinvest: str, check: str, mode: str = "pct", min_losers: int = 10, **kw):
        """mode 'pct': stop when the close is `stop` (fraction) below average cost.
        mode 'pts': stop when the position's P&L (proceeds taken + value − invested, as on the Performance page) is
                    worse than −`stop` percent of the book's current value.
        mode 'avg': as 'pts', but the threshold is the running average loss (in % of book at the time) of positions
                    already closed at a loss — point-in-time; inactive until `min_losers` losers exist."""
        super().__init__(**kw)
        self.stop, self.reinvest, self.check, self.mode, self.min_losers = stop, reinvest, check, mode, min_losers
        self.basis: dict[str, list[float]] = {}          # symbol -> [qty, cost]   (average-cost basis)
        self.pos: dict[str, list[float]] = {}            # symbol -> [qty, invested, proceeds]  (position P&L)
        self.losers: list[float] = []                    # closed losing positions, P&L in % of book at close
        self.stops = 0
        self._equity = 1.0

    def _threshold(self) -> float | None:
        """Loss (in % of book) beyond which a holding is sold; None = rule inactive."""
        if self.mode == "pts":
            return self.stop
        if len(self.losers) < self.min_losers:
            return None
        return -float(np.mean(self.losers))

    def _book(self, orders):
        for o in orders:                                 # position P&L, same closing rule as the Performance page
            if o.side == "BUY":
                q, inv, proc, peak = self.pos.get(o.symbol, [0.0, 0.0, 0.0, 0.0])
                q, inv = q + o.qty, inv + o.value
                self.pos[o.symbol] = [q, inv, proc, max(peak, q)]
                continue
            if o.symbol not in self.pos:                 # remnant dust of an already-closed position
                continue
            q, inv, proc, peak = self.pos[o.symbol]
            q, proc = q - o.qty, proc + o.value
            if q <= 1e-3 * peak:
                if proc - inv < 0:
                    self.losers.append(100 * (proc - inv) / self._equity)
                del self.pos[o.symbol]
            else:
                self.pos[o.symbol] = [q, inv, proc, peak]
        for o in orders:
            q, c = self.basis.get(o.symbol, [0.0, 0.0])
            if o.side == "BUY":
                q, c = q + o.qty, c + o.value
            else:
                c = c * max(q - o.qty, 0.0) / q if q > 0 else 0.0
                q = max(q - o.qty, 0.0)
            if q <= 1e-15:
                self.basis.pop(o.symbol, None)
            else:
                self.basis[o.symbol] = [q, c]

    def on_day(self, st, m, costs, cal, i, prev, rebalance):
        d = cal[i]
        pre: list[Order] = []
        if prev is not None:
            self._equity = st.cash + sum(sum(t.qty for t in ls) * m.cclose.at[prev, s] for s, ls in st.lots.items()
                                         if not _nan(m.cclose.at[prev, s]))
        thr = self._threshold() if self.mode != "pct" else None
        if (self.check == "daily" or rebalance) and (self.mode == "pct" or thr is not None):
            slip, sr, br = costs.slip, costs.sell_rate, costs.buy_rate
            for s in sorted(st.lots):
                q, c = self.basis.get(s, [0.0, 0.0])
                held = sum(t.qty for t in st.lots[s])
                px_prev = m.cclose.at[prev, s] if prev is not None else float("nan")
                o = m.opens.at[d, s]
                if q <= 0 or _nan(px_prev) or _nan(o) or held <= 0:
                    continue
                if self.mode == "pct":
                    hit = px_prev * q / c - 1 < -self.stop
                else:
                    if s not in self.pos:
                        continue
                    _, inv, proc, _ = self.pos[s]
                    hit = 100 * (proc + held * px_prev - inv) / self._equity < -thr
                if hit:
                    proceeds = held * o * (1 - slip) * (1 - sr)
                    st.cash += proceeds
                    st.lots.pop(s)
                    pre.append(Order(s, "SELL", held, float(o), proceeds, Reason.TRIM_TO_FUND))
                    self.stops += 1
            if rebalance and pre and self.reinvest == "best" and st.lots:
                freed = sum(x.value for x in pre)
                gains = {s: m.cclose.at[prev, s] * self.basis[s][0] / self.basis[s][1] - 1
                         for s in st.lots if s in self.basis and not _nan(m.cclose.at[prev, s])
                         and not _nan(m.opens.at[d, s])}
                if gains:
                    s = max(gains, key=gains.get)
                    o = m.opens.at[d, s]
                    equity = st.cash + sum(sum(t.qty for t in st.lots[x]) * (m.opens.at[d, x] if not _nan(m.opens.at[d, x])
                                                                              else m.cclose.at[prev, x]) for x in st.lots)
                    room = (self.max_w or 1.0) * equity - sum(t.qty for t in st.lots[s]) * o
                    v = min(freed / (1 + slip) / (1 + br), room, st.cash / (1 + slip) / (1 + br))
                    if v > 1e-9:
                        cost = v * (1 + slip) * (1 + br)
                        st.cash -= cost
                        st.lots[s].append(Lot(s, v / o, d.date(), 0))
                        pre.append(Order(s, "BUY", v / o, float(o), cost, Reason.CASH_DEPLOY))
        self._book(pre)
        out = super().on_day(st, m, costs, cal, i, prev, rebalance)
        self._book(out)
        return pre + out


class ExcludeTranche(Tranche):
    """User rule (2026-11-15, second form): the live rule keeps running; on top, a point-in-time "worst losers" filter.

    Each calendar year starts fresh. Once `min_losers` positions have closed at a loss this year, the line is their
    average loss in points of the book's value at the start of the year (updated as more close). At every weekly
    rebalance a holding whose P&L (proceeds + value − invested, page definition) is worse than the line is removed
    from the pickable list until that P&L is back above the line. `sell`: also sell it at the open (cash goes to the
    live rule's picks); otherwise it is held and its tranches age out as normal."""

    def __init__(self, sell: bool, min_losers: int = 10, **kw):
        super().__init__(**kw)
        self.sell, self.min_losers = sell, min_losers
        self.pos: dict[str, list[float]] = {}            # symbol -> [qty, invested, proceeds, peak]
        self.gone: dict[str, list[float]] = {}           # sold-and-excluded: symbol -> [qty, invested, proceeds]
        self.losers: list[float] = []
        self.excluded: set[str] = set()
        self.year, self.v0 = None, 1.0
        self.stops = 0                                   # exclusions made

    def _book(self, orders):
        for o in orders:
            if o.side == "BUY":
                q, inv, proc, peak = self.pos.get(o.symbol, [0.0, 0.0, 0.0, 0.0])
                self.pos[o.symbol] = [q + o.qty, inv + o.value, proc, max(peak, q + o.qty)]
                continue
            if o.symbol not in self.pos:
                continue
            q, inv, proc, peak = self.pos[o.symbol]
            q, proc = q - o.qty, proc + o.value
            if q <= 1e-3 * peak:
                if proc - inv < 0:
                    self.losers.append(100 * (proc - inv) / self.v0)
                del self.pos[o.symbol]
            else:
                self.pos[o.symbol] = [q, inv, proc, peak]

    def on_day(self, st, m, costs, cal, i, prev, rebalance):
        d = cal[i]
        if prev is not None and d.year != self.year:      # a new year: fresh line, fresh exclusions
            self.year = d.year
            self.v0 = st.cash + sum(sum(t.qty for t in ls) * m.cclose.at[prev, s] for s, ls in st.lots.items()
                                    if not _nan(m.cclose.at[prev, s])) or 1.0
            self.losers, self.excluded, self.gone = [], set(), {}
        pre: list[Order] = []
        masked = []
        if rebalance and prev is not None and len(self.losers) >= self.min_losers:
            line = float(np.mean(self.losers))           # negative: average loser this year, points of the book
            pnl = lambda inv, proc, q, px: 100 * (proc + q * px - inv) / self.v0
            for s in list(self.excluded):                # back on the list once P&L recovers above the line
                px = m.cclose.at[prev, s]
                if _nan(px):
                    continue
                if s in self.gone:
                    q, inv, proc = self.gone[s]
                elif s in self.pos:
                    q, inv, proc, _ = self.pos[s]
                else:
                    self.excluded.discard(s)
                    continue
                if pnl(inv, proc, q, px) > line:
                    self.excluded.discard(s)
                    self.gone.pop(s, None)
            for s in sorted(st.lots):
                if s in self.excluded or s not in self.pos or _nan(m.cclose.at[prev, s]):
                    continue
                q, inv, proc, _ = self.pos[s]
                if pnl(inv, proc, q, m.cclose.at[prev, s]) < line:
                    self.excluded.add(s)
                    self.stops += 1
                    o = m.opens.at[d, s]
                    if self.sell and not _nan(o):
                        held = sum(t.qty for t in st.lots[s])
                        proceeds = held * o * (1 - costs.slip) * (1 - costs.sell_rate)
                        st.cash += proceeds
                        st.lots.pop(s)
                        self.gone[s] = [held, inv, proc]     # "as if still held": shares at today's price
                        pre.append(Order(s, "SELL", held, float(o), proceeds, Reason.TRIM_TO_FUND))
            self._book(pre)
            if self.excluded and prev in m.elig.index:   # out of the pickable list for this rebalance only
                masked = [s for s in self.excluded if s in m.elig.columns]
                saved = m.elig.loc[prev, masked].copy()
                m.elig.loc[prev, masked] = False
        try:
            out = super().on_day(st, m, costs, cal, i, prev, rebalance)
        finally:
            if masked:
                m.elig.loc[prev, masked] = saved
        self._book(out)
        return pre + out


def fib_market(m, level: float, n: int = 66):
    """The same market with Alpha eligibility narrowed to stocks whose close is above their rolling n-session
    Fibonacci level: low + level × (high − low) of the last n closes (closes only — the engine has no high/low).
    A stock with under n sessions of history is not eligible. Returns (market, mask)."""
    import copy
    px = m.cclose[[c for c in m.elig.columns if c in m.cclose.columns]]
    hi, lo = px.rolling(n, min_periods=n).max(), px.rolling(n, min_periods=n).min()
    mask = (px > lo + level * (hi - lo)).reindex(index=m.elig.index, columns=m.elig.columns).fillna(False)
    out = copy.copy(m)
    out.elig = m.elig & mask
    return out, mask


def bind_rate(m, mask, cal, wd: int, top: int = 5) -> float:
    """Share of the live rule's weekly top-`top` picks that the mask would have refused (how often the filter bites)."""
    hit = tot = 0
    for d in sorted(base_schedule("tranche", cal, wd)):
        i = m.closes.index.get_loc(d)
        if i == 0:
            continue
        prev = m.closes.index[i - 1]
        pool = [s for s in m.members(d.date()) if s in m.roc.columns and bool(m.elig.at[prev, s])
                and not pd.isna(m.roc.at[prev, s]) and not pd.isna(m.opens.at[d, s])]
        picks = m.roc.loc[prev, pool].sort_values(ascending=False).index[:top]
        tot += len(picks)
        hit += int(sum(not bool(mask.at[prev, s]) for s in picks))
    return 100 * hit / tot if tot else 0.0


def perf(eq: pd.Series) -> dict:
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    return {"cagr": 100 * ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1), "sharpe": float(r.mean() / r.std() * math.sqrt(252)),
            "max_dd": 100 * float((eq / eq.cummax() - 1).min())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleeve", default="growth")
    ap.add_argument("--start", default="2020-10-01")
    ap.add_argument("--load-from", default=None, help="load prices/membership from this date (extra history for long lookbacks); the backtest still starts at --start")
    ap.add_argument("--lookbacks", default="22,5,10,15", help="--set roc: ranking lookbacks in sessions (22 = control)")
    ap.add_argument("--set", choices=("pct", "pts", "excl", "fib", "roc"), default="pct",
                    help="pct: stop at X%% below cost · pts: stop when the loss exceeds the average loser / fixed points")
    a = ap.parse_args()
    db = MongoClient()["market_hunt"]
    cfg = FundStore(db).get_config()
    sl = next(s for s in cfg["sleeves"] if s["id"] == a.sleeve)
    p = sl.get("params") or {}
    kw = dict(top=int(p.get("top", 5)), weeks=int(p.get("weeks", 8)), slot_div=int(p.get("slot_div", 40)),
              max_w=float(p.get("max_w", 0.10)), deploy_cash=bool(p.get("deploy_cash", True)),
              trim_at=p.get("trim_at"), full_slot=bool(p.get("full_slot", False)))
    ms = date.fromisoformat(a.start)
    costs = Costs(*charge_rates(), float(cfg["slippage_bp"]) / 1e4)
    t0 = time.time()
    m = service._market(db, sl, date.fromisoformat(a.load_from) if a.load_from else ms, date.today())
    cal = trading_days(m.closes[BENCH], ms, m.closes[BENCH].dropna().index[-1])
    mid = cal[len(cal) // 2]
    print(f"{sl['label']} ({sl['universe']}) {cal[0].date()}..{cal[-1].date()} split {mid.date()}  loaded {time.time() - t0:.0f}s",
          flush=True)
    variants = [("baseline", None, None, None, None)]
    if a.set == "pct":
        variants += [(f"stop{int(x * 100)}-{r}-{c}", x, r, c, "pct")
                     for c in ("weekly",) for r in ("rank", "best") for x in (0.08, 0.10, 0.12, 0.15)]
        variants += [(f"stop{int(x * 100)}-rank-daily", x, "rank", "daily", "pct") for x in (0.10, 0.15)]
    elif a.set == "pts":
        variants += [(f"avgloser-{r}-{c}", 0.0, r, c, "avg") for c in ("weekly", "daily") for r in ("rank", "best")]
        variants += [(f"loss>{x:.1f}pts-rank-weekly", x, "rank", "weekly", "pts") for x in (0.4, 0.6, 0.8, 1.0)]
    elif a.set == "excl":
        variants += [(f"exclude-{'sell' if s else 'hold'}-after{n}", n, s, None, "excl")
                     for s in (False, True) for n in (10, 5, 20)]
    elif a.set == "fib":
        variants += [("fib66", 0.66, False, None, "fib"), ("fib66+exclude-hold10", 0.66, True, None, "fib"),
                     ("fib50", 0.50, False, None, "fib"), ("fib80", 0.80, False, None, "fib")]
    else:                                              # ranking lookback: the live rule ranks by 22-session return
        variants += [(f"roc{n}", n, None, None, "roc") for n in [int(x) for x in a.lookbacks.split(",")]]   # roc22 = control, must equal baseline
    markets = {}
    for _, x, _, _, mode in variants:
        if mode == "fib" and x not in markets:
            markets[x] = fib_market(m, x)
            print(f"  fib {x:.2f}: refuses {bind_rate(m, markets[x][1], cal, 0):.1f}% of the live rule's weekly top-5 picks"
                  " (Monday schedule)", flush=True)
        if mode == "roc" and x not in markets:
            import copy
            from fund.engine.signals import roc as roc_n
            mk = copy.copy(m)
            mk.roc = roc_n(m.closes[list(m.roc.columns)], x)     # same signal function and columns, n sessions
            markets[x] = (mk, None)
            same = (m.roc.columns == mk.roc.columns).all()
            first = int(mk.roc.loc[:cal[0]].iloc[-1].notna().sum())
            print(f"  roc{x}: ranking frame rebuilt over the same {len(mk.roc.columns)} stocks (columns match: {same});"
                  f" {first} have a {x}-session return on {cal[0].date()}", flush=True)
    rows = []
    for name, x, r, c, mode in variants:
        for wd in range(5):
            mk = m
            if x is None:
                book = Tranche(**kw)
            elif mode == "excl":
                book = ExcludeTranche(sell=r, min_losers=x, **kw)
            elif mode == "fib":
                mk = markets[x][0]
                book = ExcludeTranche(sell=False, min_losers=10, **kw) if r else Tranche(**kw)
            elif mode == "roc":
                mk = markets[x][0]
                book = Tranche(**kw)
            else:
                book = StopTranche(x, r, c, mode=mode, **kw)
            res = replay_sleeve(book, mk, costs, ms, cal[-1], base_schedule("tranche", cal, wd), 1.0)
            eq = res.equity
            row = {"variant": name, "weekday": wd, **perf(eq), "h1": perf(eq.loc[:mid])["cagr"],
                   "h2": perf(eq.loc[mid:])["cagr"], "stops": getattr(book, "stops", 0)}
            rows.append(row)
        sub = [r_ for r_ in rows if r_["variant"] == name]
        print(f"  {name:<24} CAGR {np.median([s['cagr'] for s in sub]):6.1f}% (wd {min(s['cagr'] for s in sub):5.1f}..{max(s['cagr'] for s in sub):5.1f})"
              f"  Sharpe {np.median([s['sharpe'] for s in sub]):4.2f}  DD {np.median([s['max_dd'] for s in sub]):6.1f}%"
              f"  H1 {np.median([s['h1'] for s in sub]):6.1f}%  H2 {np.median([s['h2'] for s in sub]):6.1f}%"
              f"  stops/run {np.median([s['stops'] for s in sub]):5.0f}   [{time.time() - t0:.0f}s]", flush=True)
    tag = "" if a.set == "pct" else "_" + a.set
    if a.set == "roc" and a.lookbacks != "22,5,10,15":
        tag += "_" + a.lookbacks.replace(",", "-")
    out = OUT.with_name(f"{OUT.stem}_{a.sleeve}{tag}.json")
    out.write_text(json.dumps({"sleeve": a.sleeve, "start": a.start, "split": str(mid.date()), "rows": rows}, indent=1))
    print("wrote", out)


if __name__ == "__main__":
    main()
