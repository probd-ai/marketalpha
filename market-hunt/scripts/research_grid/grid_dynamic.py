"""Dynamic mean_long-grid book on point-in-time NIFTY50 (no schedule, no fixed stock count).

State per stock = direction of mean_long's most recent 5-point grid crossing (value_grid_filter.grid_state).
Hold every member in state +1; sell a holding when its state turns -1. Checked every session (or every week),
signal at the previous close, fills at the open. Capital always invested, equal weight = equity / names held:
new entries are funded from exit cash up to an equal share (retained names are not trimmed each time); leftover cash
is spread across holdings. Oct 2020 -> Sep 2026 (mean_long starts 2018). Compared with the value book and EW50.

  .venv/Scripts/python.exe scripts/research_grid/grid_dynamic.py
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
from combine_all import WINDOWS as WW, stats  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from longhold import hold_stats  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402
from value_grid_filter import grid_state  # noqa: E402
from value_luck import rng  # noqa: E402

CAP, SLIP = 1_260_000, 0.0005


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, ml = load_data(db, ever, field="mean_long")
    cols = [c for c in closes.columns if c != BENCH]
    state = grid_state(ml.reindex(index=closes.index, columns=cols))
    roc = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)   # unused ranking input
    br, sr = charge_rates()

    def pick(prev, pool, held):
        return sorted(s for s in pool if state.at[prev, s] == 1)

    sets, info = {}, {}
    for name, freqs in {"Grid dynamic, daily check": ["daily"],
                        "Grid dynamic, weekly check": [f"weekly+{k}" for k in range(5)]}.items():
        cs, turns = [], []
        for j, fq in enumerate(freqs):
            log = [] if j == 0 else None
            eq, traded = run(opens, closes, roc, iv, date(2020, 10, 1), date(2026, 9, 25), 50, "low", fq, CAP, SLIP,
                             br, sr, pick_fn=pick, held_log=log)
            cs.append(eq["equity"] / eq["equity"].iloc[0])
            turns.append(traded / eq["equity"].mean() / ((eq.index[-1] - eq.index[0]).days / 365.25))
            if log is not None:
                inv = (eq["held"] / eq["equity"])
                info[name] = {**hold_stats(log), "turnover_pa": float(np.mean(turns)),
                              "avg_invested": float(inv.mean())}
        idx = cs[0].index
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)})
        sets[name].to_csv(HERE / f"grid_dynamic_{'daily' if 'daily' in name else 'weekly'}.csv")
        print(f"{name} done", flush=True)
    idx = sets["Grid dynamic, daily check"].index
    ew = ew_index(closes, iv).reindex(idx).ffill()
    rep = {"info": info,
           "Value book (monthly losers)": rng(pd.read_csv(HERE / "value_luck_2020.csv", index_col=0, parse_dates=True).reindex(idx), WW, ew)}
    for name, df in sets.items():
        rep[name] = rng(df, WW, ew)
    # crash windows inside 2020-26
    d = sets["Grid dynamic, daily check"].iloc[:, 0]
    v = pd.read_csv(HERE / "value_luck_2020.csv", index_col=0, parse_dates=True).reindex(idx).iloc[:, 0]
    rep["falls"] = {w: {"grid_daily": float((d.loc[a:b] / d.loc[a:b].cummax() - 1).min()),
                        "value_book": float((v.loc[a:b] / v.loc[a:b].cummax() - 1).min()),
                        "ew50": float((ew.loc[a:b] / ew.loc[a:b].cummax() - 1).min())}
                    for w, (a, b) in {"2022": ("2021-10-01", "2022-12-31"), "2024-25": ("2024-09-01", "2025-03-31"),
                                      "2026": ("2026-01-01", "2026-09-25")}.items()}
    (HERE / "grid_dynamic.json").write_text(json.dumps(rep, indent=2, default=float))
    for k, x in info.items():
        print(f"{k:28s} names {x['names_avg']:.1f} [{x['names_min']}-{x['names_max']}]  hold median {x['hold_median_days']:.0f}d "
              f"p75 {x['hold_p75_days']:.0f}d  turnover {x['turnover_pa']:.1f}x  invested {x['avg_invested'] * 100:.0f}%")
    for part in ["Value book (monthly losers)", "Grid dynamic, daily check", "Grid dynamic, weekly check"]:
        print(f"== {part}")
        for w in WW:
            r = rep[part][w]
            print(f"   {w:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}  spread {r['spread']:4.1f}"
                  f"  DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}  EW50 {r['ew50']:5.1f}")
    print("falls:", {w: {k: round(val * 100, 1) for k, val in x.items()} for w, x in rep["falls"].items()})


if __name__ == "__main__":
    main()
