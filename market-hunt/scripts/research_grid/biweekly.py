"""Bi-weekly 8-week-hold books (Alpha on NIFTY200 point-in-time, 22d ROC, honest harness).

  BW5  : every 2 weeks buy the top 5, tranche equity/20, each held 8 weeks (4 rebalances)
  BW10 : every 2 weeks buy the top 10, tranche equity/40, each held 8 weeks
Timing luck: 10 variants = weekday k 0..4 x which-of-two-weeks phase 0..1.
Compared with weekly A8 (range_a8.csv), weekly 4w and two books. Also names held and holding period.

  .venv/Scripts/python.exe scripts/research_grid/biweekly.py
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
from alpha_test import RULES, load_means  # noqa: E402
from daily_tranche import INDEX  # noqa: E402
from grid_meanrev import BENCH, charge_rates, load_data  # noqa: E402
from longhold import cagr, hold_stats  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from range_compare import START, WINDOWS  # noqa: E402
from weekly_tranche import run_weekly  # noqa: E402

VARIANTS = {"BW5 bi-weekly top-5, 8wk (5%)": dict(top=5, weeks=4, slot_div=20),
            "BW10 bi-weekly top-10, 8wk (2.5%)": dict(top=10, weeks=4, slot_div=40)}


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, INDEX, date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, score = load_data(db, ever)
    fields = {"truevx_score": score, **load_means(db, ever)}
    cols = [c for c in closes.columns if c != BENCH]
    elig = pd.DataFrame(True, index=closes.index, columns=cols)
    for f, thr in RULES.items():
        elig &= (fields[f].reindex(index=closes.index, columns=cols) > thr).fillna(False)
    roc22 = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    br, sr = charge_rates()

    sets, info = {}, {}
    for name, v in VARIANTS.items():
        curves = []
        for k in range(5):
            for ph in range(2):
                log = [] if (k, ph) == (0, 0) else None
                eq, traded = run_weekly(opens, closes, roc22, iv, elig, START, None, br, sr, k=k, every=2, phase=ph,
                                        held_log=log, **v)
                curves.append(eq["equity"] / eq["equity"].iloc[0])
                if log is not None:
                    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
                    info[name] = {**hold_stats(log), "turnover_pa": traded / eq["equity"].mean() / yrs}
        idx = curves[0].index
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(curves)})
        sets[name].to_csv(HERE / f"range_{name.split()[0].lower()}.csv")
    idx = sets[list(VARIANTS)[0]].index
    for lab, f in [("Weekly top-5, 8wk (A8)", "range_a8.csv"), ("Weekly top-5, 4wk", "range_weekly.csv"),
                   ("Two books (7)", "range_two.csv")]:
        sets[lab] = pd.read_csv(HERE / f, index_col=0, parse_dates=True).reindex(idx)

    rep = {"info": info}
    for w, (a, b) in WINDOWS.items():
        rep[w] = {}
        for k, df in sets.items():
            sub = df.loc[a:b]
            cg = np.array([cagr(sub[c]) for c in sub.columns]) * 100
            dd = np.array([((sub[c].dropna() / sub[c].dropna().cummax()) - 1).min() for c in sub.columns]) * 100
            rep[w][k] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                         "dd_worst": dd.min(), "dd_best": dd.max()}
    (HERE / "biweekly_report.json").write_text(json.dumps(rep, indent=2, default=float))
    for k, v in info.items():
        print(f"{k:36s} names {v['names_avg']:.1f} [{v['names_min']}-{v['names_max']}]  hold median {v['hold_median_days']:.0f}d "
              f"p75 {v['hold_p75_days']:.0f}d  turnover {v['turnover_pa']:.1f}x")
    for w in WINDOWS:
        print(f"== {w}")
        for k, r in rep[w].items():
            print(f"   {k:36s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}   DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}")


if __name__ == "__main__":
    main()
