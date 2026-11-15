"""Grid-10 value core (production engine, fund.engine.grid10) on point-in-time NIFTY 50, Jan 2008 -> latest —
its first full-history test, on EXACTLY the inputs of meanlong_streak.py so the two compare fairly.

Inputs shared with meanlong_streak.py: mean_long recomputed from prices (meanlong_slope; identical to the stored
series), OHLC from Mongo + crash_prices/ for delisted names, NSE month-end NIFTY 50 membership throughout
(crash_test.build_intervals), statutory charges + 5 bp slippage, eras and crash windows.
Grid-10: grid filter from mean_long (fund.engine.signals.grid_state), lowest 22-day return, sell on grid DOWN + positive
month, weekly schedule = production (period_start_shifted weekly+k), k = 0..4. Control: random 10 from the same
grid-UP pool with the same exits (value_knife.Grid10X(seed)), 20 seeds, k = 0.

  .venv/Scripts/python.exe scripts/research_grid/grid10_2008.py
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
from meanlong_slope import ERAS, START, load_ohlc, mean_long_series, stats  # noqa: E402
from membership import members_as_of  # noqa: E402
from value_knife import Grid10X  # noqa: E402

BENCH = "Nifty 50"
N, SLIP, CAP, SEEDS = 10, 0.0005, 10_000_000.0, 20
DESIGN = ("2020-10-01", "2026-09-25")          # the window of the earlier Grid-10 tests (sanity check)


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    ivs = build_intervals(read_mcwb(), START, date.today())
    syms = sorted({iv.symbol for iv in ivs})
    ohlc = load_ohlc(db, syms)
    ml = mean_long_series(ohlc, syms)
    closes = pd.DataFrame({s: g.set_index("date")["close_price"] for s, g in ohlc.items()}).sort_index()
    opens = pd.DataFrame({s: g.set_index("date")["open_price"] for s, g in ohlc.items()}).sort_index()
    cols = [c for c in closes.columns if c != BENCH]
    grid = grid_state(ml.reindex(index=closes.index, columns=cols))
    m = Market(opens.reindex(columns=closes.columns), closes, BENCH, lambda d: members_as_of(ivs, d),
               roc(closes[cols]), grid=grid)
    costs = Costs(*charge_rates(), SLIP)
    cal = trading_days(closes[BENCH], START)
    end = cal[-1]

    rets = closes.drop(columns=[BENCH]).pct_change(fill_method=None)
    ew_r = pd.Series([rets.loc[d, [s for s in members_as_of(ivs, d.date()) if s in rets.columns]].mean()
                      for d in cal], index=cal).fillna(0)
    ew = (1 + ew_r).cumprod() * CAP
    nifty = closes[BENCH].reindex(cal) / closes[BENCH].reindex(cal).iloc[0] * CAP

    def windows(eq):
        out = {"full": stats(eq)}
        out.update({n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()})
        out["design_oct20_sep26"] = stats(eq[(eq.index >= pd.Timestamp(DESIGN[0])) & (eq.index <= pd.Timestamp(DESIGN[1]))])
        return out

    runs = {}
    for k in range(5):
        res = replay_sleeve(Grid10(N), m, costs, START, end, period_start_shifted(cal, "weekly", k), CAP)
        runs[k] = res.equity
        print(f"k={k} {stats(res.equity)}", flush=True)
    per_k = {k: windows(e) for k, e in runs.items()}
    keys = list(per_k[0])
    rep = {"period": f"{cal[0].date()} -> {end.date()}",
           "grid10_weekly": {w: {f: {"min": min(per_k[k][w][f] for k in runs), "median": float(np.median([per_k[k][w][f] for k in runs])),
                                     "max": max(per_k[k][w][f] for k in runs)} for f in ("cagr", "max_dd", "sharpe")}
                             for w in keys},
           "crashes": {c: {"grid10_median": float(np.median([window_dd(e, a, b) for e in runs.values()])),
                           "ew50": window_dd(ew, a, b), "nifty50": window_dd(nifty, a, b)} for c, (a, b) in CRASHES.items()},
           "ew50": windows(ew), "nifty50": windows(nifty)}

    rnd = []
    for seed in range(SEEDS):
        res = replay_sleeve(Grid10X(N, seed=seed), m, costs, START, end, period_start_shifted(cal, "weekly", 0), CAP)
        rnd.append(windows(res.equity))
        print(f"random seed {seed} {rnd[-1]['full']['cagr']:.4f}", flush=True)
    rep["random_pool_control"] = {w: {"p5": float(np.percentile([r[w]["cagr"] for r in rnd], 5)),
                                      "median": float(np.median([r[w]["cagr"] for r in rnd])),
                                      "p95": float(np.percentile([r[w]["cagr"] for r in rnd], 95))} for w in keys}

    # side by side with the streak book ("just turned strong"), same inputs
    st = pd.read_csv(HERE / "meanlong_streak" / "equity.csv", index_col=0, parse_dates=True)
    if "daily_shortest" in st.columns:
        rep["streak_shortest_daily"] = windows(st["daily_shortest"].dropna())

    out = HERE / "grid10_2008"
    out.mkdir(exist_ok=True)
    pd.DataFrame({**{f"grid10_k{k}": e for k, e in runs.items()}, "ew50": ew, "nifty50": nifty}).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
