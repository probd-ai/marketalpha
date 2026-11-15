"""Grid-10 value core + the streak exit: also sell a holding once mean_long has dropped below its own 222-session SMA,
and never buy a stock that is below it — the user's "save the falling stocks that don't bounce" idea.

Implemented by narrowing the member list Grid-10 sees (production engine, no rule rewrite): a stock counts as a member
only while its mean_long was above its SMA at the previous close. Grid-10 sells a holding that is not a member at the
next open (the index-exit path) and only picks from members, so:
  GS_daily   filter applied every session  -> sold the session after it drops below, never bought while below
  GS_weekly  filter applied only on the weekly rebalance session -> same, checked once a week
Baseline G = plain Grid-10 (grid10_2008.py, identical inputs). weekly+k, k = 0..4.
Point-in-time NIFTY 50, Jan 2008 -> latest, statutory charges + 5 bp, mean_long recomputed from prices.

  .venv/Scripts/python.exe scripts/research_grid/grid10_streak_exit.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from crash_test import CRASHES, build_intervals, read_mcwb, window_dd  # noqa: E402
from fund.data import charge_rates  # noqa: E402
from fund.engine.calendar import period_start_shifted, trading_days  # noqa: E402
from fund.engine.costs import Costs  # noqa: E402
from fund.engine.grid10 import Grid10  # noqa: E402
from fund.engine.market import Market  # noqa: E402
from fund.engine.replay import replay_sleeve  # noqa: E402
from fund.engine.signals import grid_state, roc  # noqa: E402
from fund.engine.types import Reason  # noqa: E402
from meanlong_slope import ERAS, MA, START, load_ohlc, mean_long_series, stats  # noqa: E402
from membership import members_as_of  # noqa: E402

BENCH = "Nifty 50"
N, SLIP, CAP = 10, 0.0005, 10_000_000.0


def trailing(e: pd.Series) -> dict:
    end, out = e.index[-1], {}
    for y in (10, 5, 3, 1):
        seg = e[e.index >= end - pd.DateOffset(years=y)]
        out[f"{y}y"] = (seg.iloc[-1] / seg.iloc[0]) ** (365.25 / (seg.index[-1] - seg.index[0]).days) - 1
    return out


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    ivs = build_intervals(read_mcwb(), START, date.today())
    syms = sorted({iv.symbol for iv in ivs})
    ohlc = load_ohlc(db, syms)
    ml = mean_long_series(ohlc, syms)
    closes = pd.DataFrame({s: g.set_index("date")["close_price"] for s, g in ohlc.items()}).sort_index()
    opens = pd.DataFrame({s: g.set_index("date")["open_price"] for s, g in ohlc.items()}).sort_index()
    cols = [c for c in closes.columns if c != BENCH]
    mlr = ml.reindex(index=closes.index, columns=cols)
    ma = mlr.apply(lambda c: c.dropna().rolling(MA).mean().reindex(c.index))
    above_prev = (mlr > ma).shift(1).fillna(False).astype(bool)          # state at the previous close
    ok_on = {d.date(): set(above_prev.columns[above_prev.loc[d].to_numpy()]) for d in above_prev.index}
    grid = grid_state(mlr)
    r = roc(closes[cols])
    costs = Costs(*charge_rates(), SLIP)
    cal = trading_days(closes[BENCH], START)
    end = cal[-1]

    def market(member_fn):
        return Market(opens.reindex(columns=closes.columns), closes, BENCH, member_fn, r, grid=grid)

    def era(eq):
        out = {"full": stats(eq)}
        out.update({n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()})
        return out

    gd = pd.read_csv(HERE / "grid10_2008" / "equity.csv", index_col=0, parse_dates=True)
    runs = {"G": {k: gd[f"grid10_k{k}"].dropna() for k in range(5)}}
    extra = {"G": {}}
    for name in ("GS_daily", "GS_weekly"):
        runs[name], extra[name] = {}, {}
        for k in range(5):
            sched = period_start_shifted(cal, "weekly", k)
            sched_days = {d.date() for d in sched}
            if name == "GS_daily":
                mem = lambda d: set(members_as_of(ivs, d)) & ok_on.get(d, set())
            else:
                mem = lambda d, sd=sched_days: (set(members_as_of(ivs, d)) & ok_on.get(d, set())) if d in sd \
                    else set(members_as_of(ivs, d))
            res = replay_sleeve(Grid10(N), market(mem), costs, START, end, sched, CAP)
            runs[name][k] = res.equity
            orders = [o for os in res.orders.values() for o in os]
            yrs = (res.equity.index[-1] - res.equity.index[0]).days / 365.25
            extra[name][k] = {"streak_exits_per_year": sum(o.reason == Reason.INDEX_EXIT for o in orders) / yrs,
                              "turnover": sum(abs(o.value) for o in orders) / float(res.equity.mean()) / yrs}
            print(name, k, f"{stats(res.equity)['cagr']:.4f} dd {stats(res.equity)['max_dd']:.3f}", flush=True)

    rep = {}
    for name, rs in runs.items():
        eras = {k: era(e) for k, e in rs.items()}
        tr = {k: trailing(e) for k, e in rs.items()}
        rep[name] = {
            w: {f: {"median": float(np.median([eras[k][w][f] for k in rs])), "min": min(eras[k][w][f] for k in rs),
                    "max": max(eras[k][w][f] for k in rs)} for f in ("cagr", "max_dd", "sharpe")} for w in eras[0]}
        rep[name]["trailing"] = {t: float(np.median([tr[k][t] for k in rs])) for t in tr[0]}
        rep[name]["crashes"] = {c: float(np.median([window_dd(e, a, b) for e in rs.values()])) for c, (a, b) in CRASHES.items()}
        if extra[name]:
            rep[name]["streak_exits_per_year"] = float(np.median([v["streak_exits_per_year"] for v in extra[name].values()]))
            rep[name]["turnover"] = float(np.median([v["turnover"] for v in extra[name].values()]))
    out = HERE / "grid10_streak_exit"
    out.mkdir(exist_ok=True)
    pd.DataFrame({f"{n}_k{k}": e for n, rs in runs.items() for k, e in rs.items()}).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
