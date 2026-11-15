"""Streak universe held WHOLE: every NIFTY 50 member whose mean_long is above its own 222-session SMA, equal weight.

  A all_daily     daily: sell a holding when mean_long drops below its SMA (or it leaves the index); freed cash goes to
                  newly eligible names up to equal weight; retained names are never trimmed; fully invested
  B all_monthly   reset to exact equal weight of the eligible set on the first session of each month (no exits between)
  C cap5_daily    each name a fixed 5% seat (up to 20), shortest streak first when more qualify; empty seats = cash
Reference: the top-10 streak book (meanlong_streak.py), EW NIFTY 50, Nifty 50.
Point-in-time NIFTY 50, Jan 2008 -> latest, statutory charges + 5 bp, mean_long recomputed from prices.

  .venv/Scripts/python.exe scripts/research_grid/meanlong_streak_all.py
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
from grid_meanrev import BENCH, charge_rates  # noqa: E402
from meanlong_slope import ERAS, MA, START, load_ohlc, mean_long_series, stats  # noqa: E402
from meanlong_streak import streaks  # noqa: E402
from membership import members_as_of  # noqa: E402
from rotation_roc import run  # noqa: E402

SLIP, CAP, ALL = 0.0005, 10_000_000.0, 60


def all_picker(score):
    """Every qualifying name: retained ones first, then the rest shortest streak first."""
    def pick(prev, pool, held):
        keep = sorted(s for s in held if s in pool)
        return keep + sorted((s for s in pool if s not in keep), key=lambda s: (score.at[prev, s], s))
    return pick


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    ivs = build_intervals(read_mcwb(), START, date.today())
    syms = sorted({iv.symbol for iv in ivs})
    ohlc = load_ohlc(db, syms)
    ml = mean_long_series(ohlc, syms)
    closes = pd.DataFrame({s: g.set_index("date")["close_price"] for s, g in ohlc.items()}).sort_index()
    opens = pd.DataFrame({s: g.set_index("date")["open_price"] for s, g in ohlc.items()}).sort_index()
    ml = ml.reindex(closes.index)
    ma = ml.apply(lambda c: c.dropna().rolling(MA).mean().reindex(c.index))
    streak = streaks(ml, ma)
    br, sr = charge_rates()
    cal = closes.index[(closes.index >= pd.Timestamp(START)) & closes[BENCH].notna().to_numpy()]

    def era(eq):
        out = {"full": stats(eq)}
        out.update({n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()})
        return out

    runs = {
        "A_all_daily": lambda: run(opens, closes, streak, ivs, START, None, ALL, "high", "daily", CAP, SLIP, br, sr,
                                   pick_fn=all_picker(streak)),
        # mode "alpha" sizes equity / len(qualifying) and resets every name to it each month (exact equal weight)
        "B_all_monthly": lambda: run(opens, closes, streak, ivs, START, None, ALL, "alpha", "monthly", CAP, SLIP, br, sr,
                                     eligible=streak.notna()),
        "C_cap5_daily": lambda: run(opens, closes, streak, ivs, START, None, 20, "high", "daily", CAP, SLIP, br, sr,
                                    pick_fn=all_picker(streak), fixed_seats=True),
    }
    rep, curves = {"period": f"{cal[0].date()} -> {cal[-1].date()}"}, {}
    for name, fn in runs.items():
        eq, traded = fn()
        e = eq["equity"]
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        rep[name] = {"book": era(e), "invested": float((eq["held"] / eq["equity"]).mean()),
                     "avg_names": float(eq["n_held"].mean()), "min_names": int(eq["n_held"].min()),
                     "max_names": int(eq["n_held"].max()), "turnover_per_year": traded / float(e.mean()) / yrs,
                     "crashes": {c: window_dd(e, a, b) for c, (a, b) in CRASHES.items()}}
        curves[name] = e
        print(name, f"{stats(e)['cagr']:.4f} dd {stats(e)['max_dd']:.3f}", flush=True)
    st = pd.read_csv(HERE / "meanlong_streak" / "equity.csv", index_col=0, parse_dates=True)
    for k, col in (("top10_streak", "daily_shortest"), ("ew50", "ew50"), ("nifty50", "nifty50")):
        e = st[col].dropna()
        rep[k] = {"book": era(e), "crashes": {c: window_dd(e, a, b) for c, (a, b) in CRASHES.items()}}
    out = HERE / "meanlong_streak_all"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
