"""Satellite weekly top-5 books with a per-stock cap (10% / 15%): buy up to the cap, trim winners back to it each
week, and deploy leftover cash into the best-ranked holdings still under the cap (no idle-cash drag).

NIFTY500MICRO750, Alpha filter, 22d ROC, honest harness. Weekly hold 4w (tranche 1/20) and hold 8w (tranche 1/40),
weekday timing luck k=0..4, compared with the uncapped runs saved by n750_study.py (n750_weekly.csv, n750_a8.csv).
Reports CAGR min|median|max per window, drawdown, trims per year, cash drag, peak weight actually reached.

  .venv/Scripts/python.exe scripts/research_grid/n750_cap.py
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

NEW = {"Weekly 4w, cap 10% +deploy": dict(weeks=4, slot_div=20, max_w=0.10, deploy_cash=True),
       "Weekly 4w, cap 15% +deploy": dict(weeks=4, slot_div=20, max_w=0.15, deploy_cash=True),
       "Weekly 8w, cap 10% +deploy": dict(weeks=8, slot_div=40, max_w=0.10, deploy_cash=True),
       "Weekly 8w, cap 15% +deploy": dict(weeks=8, slot_div=40, max_w=0.15, deploy_cash=True)}
OLD = {"Weekly 4w, no cap": "n750_weekly.csv", "Weekly 8w, no cap": "n750_a8.csv"}


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
    for name, kw in NEW.items():
        cs = []
        for k in range(5):
            book, trims, hlog = [], [], []
            eq, traded = run_weekly(opens, closes, roc22, iv, elig, START, None, br, sr, k=k, capital=CAP,
                                    trim_log=trims, book_log=book if k == 0 else None,
                                    held_log=hlog if k == 0 else None, **kw)
            cs.append(eq["equity"] / eq["equity"].iloc[0])
            if k == 0:
                yrs = (eq.index[-1] - eq.index[0]).days / 365.25
                w = [max((v / (c + sum(bk.values())) for v in bk.values()), default=0) for _, c, bk in book]
                cash = [c / (c + sum(bk.values())) for _, c, bk in book]
                info[name] = {**hold_stats(hlog), "turnover_pa": traded / eq["equity"].mean() / yrs,
                              "trims_pa": len(trims) / yrs, "peak_weight_daily": float(max(w)),
                              "avg_largest_weight": float(np.mean(w[40:])), "avg_cash": float(np.mean(cash[40:]))}
        idx = cs[0].index
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)})
        sets[name].to_csv(HERE / f"n750_cap_{name.lower().replace(' ', '').replace(',', '_').replace('%', '').replace('+', '')}.csv")
        print(f"{name} done", flush=True)
    idx = sets[list(NEW)[0]].index
    for name, f in OLD.items():
        sets[name] = pd.read_csv(HERE / f, index_col=0, parse_dates=True).reindex(idx)

    order = ["Weekly 4w, no cap", "Weekly 4w, cap 10% +deploy", "Weekly 4w, cap 15% +deploy",
             "Weekly 8w, no cap", "Weekly 8w, cap 10% +deploy", "Weekly 8w, cap 15% +deploy"]
    rep = {"info": info}
    for w, (a, b) in WINDOWS.items():
        rep[w] = {}
        for k in order:
            sub = sets[k].loc[a:b]
            cg = np.array([cagr(sub[c]) for c in sub.columns]) * 100
            dd = np.array([((sub[c].dropna() / sub[c].dropna().cummax()) - 1).min() for c in sub.columns]) * 100
            rep[w][k] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                         "dd_worst": dd.min(), "dd_best": dd.max()}
    (HERE / "n750_cap_report.json").write_text(json.dumps(rep, indent=2, default=float))
    for k, v in info.items():
        print(f"{k:28s} names {v['names_avg']:.1f} [{v['names_min']}-{v['names_max']}]  hold median {v['hold_median_days']:.0f}d  "
              f"turnover {v['turnover_pa']:.1f}x  trims/yr {v['trims_pa']:.0f}  peak weight {v['peak_weight_daily'] * 100:.1f}%  "
              f"avg largest {v['avg_largest_weight'] * 100:.1f}%  avg cash {v['avg_cash'] * 100:.1f}%")
    for w in WINDOWS:
        print(f"== {w}")
        for k in order:
            r = rep[w][k]
            print(f"   {k:28s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}   DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}")


if __name__ == "__main__":
    main()
