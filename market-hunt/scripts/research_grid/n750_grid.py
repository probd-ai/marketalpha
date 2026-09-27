"""Complete the cadence x holding-period grid on NIFTY500MICRO750 (top 5, Alpha filter, 22d ROC, honest harness).

Grid: weekly / bi-weekly  x  hold 4 / 8 / 12 weeks. Cells already run by n750_study.py are reused from their CSVs
(weekly 4w, weekly 8w = A8, bi-weekly 8w = BW5); this script runs the other three:
  weekly 12w     tranche 1/60, weeks=12                (weekday luck 0..4)
  bi-weekly 4w   tranche 1/10, every=2, weeks=2        (weekday x phase luck, 10)
  bi-weekly 12w  tranche 1/30, every=2, weeks=6        (weekday x phase luck, 10)
Reports CAGR min|median|max per window, drawdown range, names held, holding period and turnover for all six,
plus the current quarterly config for reference.

  .venv/Scripts/python.exe scripts/research_grid/n750_grid.py
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
from grid_meanrev import BENCH, charge_rates, load_data  # noqa: E402
from longhold import cagr, hold_stats  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from n750_study import CAP, INDEX  # noqa: E402
from range_compare import START, WINDOWS  # noqa: E402
from weekly_tranche import run_weekly  # noqa: E402

NEW = {"Weekly 12w": [dict(k=k, weeks=12, slot_div=60) for k in range(5)],
       "Bi-weekly 4w": [dict(k=k, every=2, phase=p, weeks=2, slot_div=10) for k in range(5) for p in range(2)],
       "Bi-weekly 12w": [dict(k=k, every=2, phase=p, weeks=6, slot_div=30) for k in range(5) for p in range(2)]}
REUSED = {"Weekly 4w": "n750_weekly.csv", "Weekly 8w": "n750_a8.csv", "Bi-weekly 8w": "n750_bw5.csv",
          "Current quarterly (20)": "n750_current.csv"}
ORDER = ["Weekly 4w", "Weekly 8w", "Weekly 12w", "Bi-weekly 4w", "Bi-weekly 8w", "Bi-weekly 12w", "Current quarterly (20)"]


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
    for name, vs in NEW.items():
        cs = []
        for j, v in enumerate(vs):
            log = [] if j == 0 else None
            eq, traded = run_weekly(opens, closes, roc22, iv, elig, START, None, br, sr, capital=CAP, held_log=log, **v)
            cs.append(eq["equity"] / eq["equity"].iloc[0])
            if log is not None:
                yrs = (eq.index[-1] - eq.index[0]).days / 365.25
                info[name] = {**hold_stats(log), "turnover_pa": traded / eq["equity"].mean() / yrs}
        idx = cs[0].index
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)})
        sets[name].to_csv(HERE / f"n750_{name.lower().replace(' ', '').replace('-', '')}.csv")
        print(f"{name} done", flush=True)
    idx = sets["Weekly 12w"].index
    for name, f in REUSED.items():
        sets[name] = pd.read_csv(HERE / f, index_col=0, parse_dates=True).reindex(idx)
    old = json.loads((HERE / "n750_report.json").read_text())["info"]
    info.update({"Weekly 4w": old["Weekly top-5, 4wk"], "Weekly 8w": old["A8 weekly top-5, 8wk"],
                 "Bi-weekly 8w": old["BW5 bi-weekly top-5, 8wk"]})

    rep = {"info": info}
    for w, (a, b) in WINDOWS.items():
        rep[w] = {}
        for k in ORDER:
            sub = sets[k].loc[a:b]
            cg = np.array([cagr(sub[c]) for c in sub.columns]) * 100
            dd = np.array([((sub[c].dropna() / sub[c].dropna().cummax()) - 1).min() for c in sub.columns]) * 100
            rep[w][k] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                         "dd_worst": dd.min(), "dd_best": dd.max()}
    (HERE / "n750_grid_report.json").write_text(json.dumps(rep, indent=2, default=float))
    for k in ORDER[:-1]:
        v = info[k]
        print(f"{k:14s} names {v['names_avg']:.1f} [{v['names_min']}-{v['names_max']}]  hold median {v['hold_median_days']:.0f}d  "
              f"p75 {v['hold_p75_days']:.0f}d  turnover {v['turnover_pa']:.1f}x")
    for w in WINDOWS:
        print(f"== {w}")
        for k in ORDER:
            r = rep[w][k]
            print(f"   {k:24s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}   DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}")


if __name__ == "__main__":
    main()
