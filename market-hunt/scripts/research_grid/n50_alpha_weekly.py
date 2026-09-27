"""Alpha list on point-in-time NIFTY50, rebalanced WEEKLY (full equal-weight rebalance, no tranches).

Each week (session k of the week, signal previous close, fills at the open): Alpha-eligible members ranked by
20_day_return, hold the top N equal-weight (an under-filled book splits equity across whatever qualifies).
N = 5, 7 and "all eligible" (N=50); weekday luck k=0..4. Oct 2020 -> Sep 2026 (TrueVX starts 2018).
Compared with the monthly value book (value_luck_2020.csv) and equal-weight NIFTY50.

  .venv/Scripts/python.exe scripts/research_grid/n50_alpha_weekly.py
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
from combine_all import WINDOWS as WW  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402
from value_luck import rng  # noqa: E402

CAP, SLIP = 1_260_000, 0.0005
NS = {"Alpha weekly top-5": 5, "Alpha weekly top-7": 7, "Alpha weekly all eligible": 50}


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, score = load_data(db, ever)
    fields = {"truevx_score": score, **load_means(db, ever)}
    cols = [c for c in closes.columns if c != BENCH]
    elig = pd.DataFrame(True, index=closes.index, columns=cols)
    for f, thr in RULES.items():
        elig &= (fields[f].reindex(index=closes.index, columns=cols) > thr).fillna(False)
    ret20 = closes[cols].apply(lambda c: c / c.dropna().shift(19).reindex(c.index) - 1)
    br, sr = charge_rates()

    sets, info = {}, {}
    for name, n in NS.items():
        cs, turns, names = [], [], []
        for k in range(5):
            eq, traded = run(opens, closes, ret20, iv, date(2020, 10, 1), date(2026, 9, 25), n, "alpha",
                             f"weekly+{k}", CAP, SLIP, br, sr, eligible=elig)
            cs.append(eq["equity"] / eq["equity"].iloc[0])
            turns.append(traded / eq["equity"].mean() / ((eq.index[-1] - eq.index[0]).days / 365.25))
            names.append(eq["n_held"].mean())
        idx = cs[0].index
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)})
        info[name] = {"turnover_pa": float(np.mean(turns)), "names_avg": float(np.mean(names))}
        print(f"{name} done", flush=True)
    idx = sets[list(NS)[0]].index
    ew = ew_index(closes, iv).reindex(idx).ffill()
    rep = {"info": info,
           "Monthly value book": rng(pd.read_csv(HERE / "value_luck_2020.csv", index_col=0, parse_dates=True).reindex(idx), WW, ew)}
    for name, df in sets.items():
        rep[name] = rng(df, WW, ew)
    (HERE / "n50_alpha_weekly.json").write_text(json.dumps(rep, indent=2, default=float))
    for k, v in info.items():
        print(f"{k:28s} names {v['names_avg']:.1f}  turnover {v['turnover_pa']:.1f}x")
    for part in ["Monthly value book", *NS]:
        print(f"== {part}")
        for w in WW:
            r = rep[part][w]
            print(f"   {w:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}  spread {r['spread']:4.1f}"
                  f"  DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}  EW50 {r['ew50']:5.1f}")


if __name__ == "__main__":
    main()
