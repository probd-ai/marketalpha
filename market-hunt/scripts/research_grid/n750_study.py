"""Satellite study on NIFTY500MICRO750 (live Alpha-Market portfolio_1783106608552), honest harness.

Designs (Alpha filter, point-in-time membership, signal previous close, next-open fills, full costs, 5 bp):
  Current config : quarterly two books — first session + middle session of each quarter — top 20 each by
                   20_day_return, 50/50; timing luck = both shifted k=0..9 sessions (k=1 = engine day + next open)
  A8             : weekly top 5 by 22d ROC, tranche 1/40, held 8 weeks       (weekday luck 0..4)
  BW5            : every 2 weeks top 5, tranche 1/20, held 8 weeks           (weekday x phase luck, 10)
  Weekly 4w      : weekly top 5, tranche 1/20, held 4 weeks                  (weekday luck 0..4)
Benchmarks: equal-weight PIT universe, Nifty 50. Coverage of ever-members with prices is reported.

  .venv/Scripts/python.exe scripts/research_grid/n750_study.py
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
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from longhold import cagr, hold_stats  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from range_compare import START, WINDOWS  # noqa: E402
from rotation_roc import run  # noqa: E402
from weekly_tranche import run_weekly  # noqa: E402

INDEX, CAP, SLIP = "NIFTY500MICRO750", 1_600_000, 0.0005


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
    ret20 = closes[cols].apply(lambda c: c / c.dropna().shift(19).reindex(c.index) - 1)
    roc22 = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    ew = ew_index(closes, iv)
    br, sr = charge_rates()
    print(f"ever-members {len(ever)}, priced {len([s for s in ever if s in closes.columns])}", flush=True)

    sets, info = {}, {}
    curves = []
    for k in range(10):
        tot = None
        for fq in [f"quarterly+{k}", f"quarterly_mid+{k}"]:
            eq, _ = run(opens, closes, ret20, iv, date.fromisoformat(START), None, 20, "alpha", fq, CAP / 2, SLIP,
                        br, sr, eligible=elig)
            tot = eq["equity"] if tot is None else tot + eq["equity"]
        curves.append(tot / tot.iloc[0])
    idx = curves[0].index
    sets["Current: quarterly 2 books (20)"] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(curves)})
    print("current config done", flush=True)

    specs = {"A8 weekly top-5, 8wk": [dict(k=k, weeks=8, slot_div=40) for k in range(5)],
             "BW5 bi-weekly top-5, 8wk": [dict(k=k, every=2, phase=p, weeks=4, slot_div=20) for k in range(5) for p in range(2)],
             "Weekly top-5, 4wk": [dict(k=k, weeks=4, slot_div=20) for k in range(5)]}
    for name, vs in specs.items():
        cs = []
        for j, v in enumerate(vs):
            log = [] if j == 0 else None
            eq, traded = run_weekly(opens, closes, roc22, iv, elig, START, None, br, sr, capital=CAP, held_log=log, **v)
            cs.append(eq["equity"] / eq["equity"].iloc[0])
            if log is not None:
                yrs = (eq.index[-1] - eq.index[0]).days / 365.25
                info[name] = {**hold_stats(log), "turnover_pa": traded / eq["equity"].mean() / yrs}
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)})
        print(f"{name} done", flush=True)
    for name, df in sets.items():
        df.to_csv(HERE / f"n750_{name.split()[0].lower().strip(':')}.csv")

    e = ew.reindex(idx).ffill()
    n = closes[BENCH].reindex(idx).ffill()
    rep = {"info": info, "coverage": {"ever": len(ever), "priced": len([s for s in ever if s in closes.columns])}}
    for w, (a, b) in WINDOWS.items():
        rep[w] = {"EW universe": cagr(e.loc[a:b]) * 100, "Nifty 50": cagr(n.loc[a:b]) * 100}
        for k, df in sets.items():
            sub = df.loc[a:b]
            cg = np.array([cagr(sub[c]) for c in sub.columns]) * 100
            dd = np.array([((sub[c].dropna() / sub[c].dropna().cummax()) - 1).min() for c in sub.columns]) * 100
            end = np.array([sub[c].dropna().iloc[-1] / sub[c].dropna().iloc[0] for c in sub.columns]) * CAP / 1e5
            rep[w][k] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                         "dd_worst": dd.min(), "dd_best": dd.max(), "end_min_L": end.min(), "end_max_L": end.max()}
    (HERE / "n750_report.json").write_text(json.dumps(rep, indent=2, default=float))
    print(rep["coverage"])
    for k, v in info.items():
        print(f"{k:32s} names {v['names_avg']:.1f} [{v['names_min']}-{v['names_max']}]  hold median {v['hold_median_days']:.0f}d  turnover {v['turnover_pa']:.1f}x")
    for w in WINDOWS:
        x = rep[w]
        print(f"== {w}   (EW universe {x['EW universe']:.1f}%, Nifty {x['Nifty 50']:.1f}%)")
        for k in sets:
            r = x[k]
            print(f"   {k:32s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}   DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}   ₹{r['end_min_L']:.0f}–{r['end_max_L']:.0f}L")


if __name__ == "__main__":
    main()
