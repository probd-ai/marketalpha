"""Longer holds for the weekly top-5 book (Alpha on NIFTY200 point-in-time, 22d ROC, honest harness).

  A12  rolling: every week top 5 get a tranche of equity/60, each held 12 weeks   (weekday luck k=0..4)
  A8   rolling: every week top 5 get a tranche of equity/40, each held 8 weeks    (weekday luck k=0..4)
  B12  buy-then-wait: tranche equity/20, held 12 weeks -> cash runs out after ~4 weeks, buying pauses
       until expiries free cash (buy ~1 month, wait ~2)                          (cycle-phase luck: start 0..11 weeks later)
Reference: weekly 4-week book (range_weekly.csv) and two books (range_two.csv). Reports CAGR min|median|max per
window, drawdown range, average names held and average holding period.

  .venv/Scripts/python.exe scripts/research_grid/longhold.py
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
from membership import load_membership, union_over  # noqa: E402
from range_compare import START, WINDOWS  # noqa: E402
from weekly_tranche import run_weekly  # noqa: E402

VARIANTS = {"A12 rolling 12w (1/60)": dict(weeks=12, slot_div=60, luck="weekday"),
            "A8 rolling 8w (1/40)": dict(weeks=8, slot_div=40, luck="weekday"),
            "B12 buy-then-wait 12w (1/20)": dict(weeks=12, slot_div=20, luck="phase")}


def cagr(s):
    s = s.dropna()
    return (s.iloc[-1] / s.iloc[0]) ** (365.25 / (s.index[-1] - s.index[0]).days) - 1


def hold_stats(log):
    runs, cur = [], {}
    for _, held in log:
        for s in list(cur):
            if s not in held:
                runs.append(cur.pop(s))
        for s in held:
            cur[s] = cur.get(s, 0) + 1
    runs = np.array(runs + list(cur.values()))
    names = np.array([len(h) for _, h in log])[60:]
    return {"names_avg": float(names.mean()), "names_min": int(names.min()), "names_max": int(names.max()),
            "hold_avg_days": float(runs.mean()), "hold_median_days": float(np.median(runs)),
            "hold_p75_days": float(np.percentile(runs, 75))}


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
    cal = closes.index[closes.index >= pd.Timestamp(START)]

    sets, info = {}, {}
    for name, v in VARIANTS.items():
        curves = []
        offsets = range(5) if v["luck"] == "weekday" else range(12)
        for k in offsets:
            log = [] if k == 0 else None
            if v["luck"] == "weekday":
                eq, traded = run_weekly(opens, closes, roc22, iv, elig, START, None, br, sr, k=k,
                                        weeks=v["weeks"], slot_div=v["slot_div"], held_log=log)
            else:
                start_k = str(cal[5 * k].date())                  # begin the cycle k weeks later
                eq, traded = run_weekly(opens, closes, roc22, iv, elig, start_k, None, br, sr,
                                        weeks=v["weeks"], slot_div=v["slot_div"], held_log=log)
            curves.append(eq["equity"] / eq["equity"].iloc[0])
            if log is not None:
                yrs = (eq.index[-1] - eq.index[0]).days / 365.25
                info[name] = {**hold_stats(log), "turnover_pa": traded / eq["equity"].mean() / yrs,
                              "avg_invested": None}
        idx = curves[0].index
        for c in curves[1:]:
            idx = idx.union(c.index)
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(curves)})
        sets[name].to_csv(HERE / f"range_{name.split()[0].lower()}.csv")
    ref_idx = sets["A12 rolling 12w (1/60)"].index
    sets["Weekly 4w (current best)"] = pd.read_csv(HERE / "range_weekly.csv", index_col=0, parse_dates=True).reindex(ref_idx)
    sets["Two books (7)"] = pd.read_csv(HERE / "range_two.csv", index_col=0, parse_dates=True).reindex(ref_idx)

    rep = {"info": info}
    for w, (a, b) in WINDOWS.items():
        rep[w] = {}
        for k, df in sets.items():
            sub = df.loc[a:b]
            cols_ok = [c for c in sub.columns if sub[c].notna().sum() > 20]
            cg = np.array([cagr(sub[c]) for c in cols_ok]) * 100
            dd = np.array([((sub[c].dropna() / sub[c].dropna().cummax()) - 1).min() for c in cols_ok]) * 100
            rep[w][k] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                         "dd_worst": dd.min(), "dd_best": dd.max()}
    (HERE / "longhold_report.json").write_text(json.dumps(rep, indent=2, default=float))
    for k, v in info.items():
        print(f"{k:30s} names {v['names_avg']:.1f} [{v['names_min']}-{v['names_max']}]  hold avg {v['hold_avg_days']:.0f}d "
              f"median {v['hold_median_days']:.0f}d p75 {v['hold_p75_days']:.0f}d  turnover {v['turnover_pa']:.1f}x")
    for w in WINDOWS:
        print(f"== {w}")
        for k, r in rep[w].items():
            print(f"   {k:30s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}   DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}")


if __name__ == "__main__":
    main()
