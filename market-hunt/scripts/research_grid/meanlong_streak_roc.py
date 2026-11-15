"""Streak filter, then 22-day momentum: each month hold the top 10 eligible NIFTY 50 stocks by 22-day return.

Eligible = point-in-time NIFTY 50 member whose mean_long is above its own 222-session SMA (the streak filter).
Monthly rebalance (signal at the previous close, fills at the open): equal weight across the 10 picks; names that
fall out of the picks — or out of eligibility — are sold (rotation_roc.run, eligible=..., elig_exit=True).
  high  top 10 by 22-day return (momentum)
  low   bottom 10 by 22-day return (1-month reversal, the companion check)
Rebalance-day luck: monthly+k, k = 0..4 sessions after the month's first session; median and range reported.
Reference: all eligible EW monthly (meanlong_streak_all B), the daily top-10 streak book, EW NIFTY 50, Nifty 50.
Jan 2008 -> latest, statutory charges + 5 bp, mean_long recomputed from prices.

  .venv/Scripts/python.exe scripts/research_grid/meanlong_streak_roc.py
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
from rotation_roc import run  # noqa: E402

N, LB, SLIP, CAP = 10, 22, 0.0005, 10_000_000.0


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
    eligible = streaks(ml, ma).notna()
    stocks = [c for c in closes.columns if c != BENCH]
    roc = closes[stocks].apply(lambda c: c / c.dropna().shift(LB).reindex(c.index) - 1)
    br, sr = charge_rates()

    def era(eq):
        out = {"full": stats(eq)}
        out.update({n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()})
        return out

    rep, curves = {}, {}
    for mode in ("high", "low"):
        res = {}
        for k in range(5):
            freq = "monthly" if k == 0 else f"monthly+{k}"
            eq, traded = run(opens, closes, roc, ivs, START, None, N, mode, freq, CAP, SLIP, br, sr,
                             eligible=eligible, elig_exit=True)
            e = eq["equity"]
            yrs = (e.index[-1] - e.index[0]).days / 365.25
            res[k] = {"era": era(e), "turnover": traded / float(e.mean()) / yrs, "names": float(eq["n_held"].mean()),
                      "crashes": {c: window_dd(e, a, b) for c, (a, b) in CRASHES.items()}}
            curves[f"{mode}_k{k}"] = e
            print(mode, k, f"{stats(e)['cagr']:.4f}", flush=True)
        rep[mode] = {w: {f: {"median": float(np.median([res[k]["era"][w][f] for k in res])),
                             "min": min(res[k]["era"][w][f] for k in res), "max": max(res[k]["era"][w][f] for k in res)}
                         for f in ("cagr", "max_dd", "sharpe")} for w in res[0]["era"]}
        rep[mode]["turnover_median"] = float(np.median([res[k]["turnover"] for k in res]))
        rep[mode]["names_median"] = float(np.median([res[k]["names"] for k in res]))
        rep[mode]["crashes_median"] = {c: float(np.median([res[k]["crashes"][c] for k in res])) for c in CRASHES}
    out = HERE / "meanlong_streak_roc"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
