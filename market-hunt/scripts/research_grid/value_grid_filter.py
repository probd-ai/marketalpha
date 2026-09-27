"""Value book + mean_long 5-point grid "falling-knife" filter (NIFTY50 22d losers, N=20, buffer 30, monthly).

Grid state per stock: the direction of mean_long's most recent crossing of a 5-point grid line (0,5,...,100).
  cross up   = floor(ml/5) rose since the previous session   -> state +1 (long trend rising: a dip, buyable)
  cross down = floor(ml/5) fell                              -> state -1 (long trend turning: falling knife)
  no crossing -> keep the previous state.
Variants (timing luck: rebalance session k=0..9 of the month):
  filter buys + sells : only state +1 names can be bought; a holding in state -1 is sold at the rebalance
  filter buys only    : only state +1 names can be bought; holdings follow the normal buffer rule
Baseline: the unfiltered monthly value book (value_luck_2020.csv). mean_long exists from 2018 -> Oct 2020 onward only.

  .venv/Scripts/python.exe scripts/research_grid/value_grid_filter.py
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
from combine_all import WINDOWS as WW  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402
from value_luck import rng  # noqa: E402

CAP, SLIP, STEP = 1_260_000, 0.0005, 5


def grid_state(ml: pd.DataFrame) -> pd.DataFrame:
    """+1 after an upward grid crossing, -1 after a downward one, held until the next crossing (per stock)."""
    def one(c: pd.Series) -> pd.Series:
        z = np.floor(c.dropna() / STEP)
        move = np.sign(z.diff()).replace(0, np.nan)
        return move.ffill().reindex(c.index).ffill()
    return ml.apply(one)


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, ml = load_data(db, ever, field="mean_long")
    cols = [c for c in closes.columns if c != BENCH]
    roc = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    state = grid_state(ml.reindex(index=closes.index, columns=cols))
    elig = (state == 1).fillna(False)
    br, sr = charge_rates()

    configs = {"Filter buys + sells": dict(eligible=elig, elig_exit=True),
               "Filter buys only": dict(eligible=elig, elig_exit=False)}
    sets, info = {}, {}
    for name, kw in configs.items():
        cs, turns, names = [], [], []
        for k in range(10):
            eq, traded = run(opens, closes, roc, iv, date(2020, 10, 1), date(2026, 9, 25), 20, "low", f"monthly+{k}",
                             CAP, SLIP, br, sr, buffer=30, **kw)
            cs.append(eq["equity"] / eq["equity"].iloc[0])
            turns.append(traded / eq["equity"].mean() / ((eq.index[-1] - eq.index[0]).days / 365.25))
            names.append(eq["n_held"].mean())
        idx = cs[0].index
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)})
        info[name] = {"turnover_pa": float(np.mean(turns)), "names_avg": float(np.mean(names))}
        print(f"{name} done", flush=True)
    idx = sets["Filter buys + sells"].index
    ew = ew_index(closes, iv).reindex(idx).ffill()
    me = pd.Series(idx, index=idx).groupby(idx.to_period("M")).first()
    from membership import members_as_of
    share_up = [np.mean([bool(elig.at[d, s]) for s in members_as_of(iv, d.date()) if s in elig.columns]) for d in me]
    rep = {"info": info, "share_of_members_in_up_state": float(np.mean(share_up)),
           "Current value book (no filter)": rng(pd.read_csv(HERE / "value_luck_2020.csv", index_col=0, parse_dates=True).reindex(idx), WW, ew)}
    for name, df in sets.items():
        rep[name] = rng(df, WW, ew)
        df.to_csv(HERE / f"value_gridfilter_{name.split()[2].replace('+', 'and')}.csv")
    (HERE / "value_grid_filter.json").write_text(json.dumps(rep, indent=2, default=float))
    print("members in 'up' state on average: %.0f%%" % (rep["share_of_members_in_up_state"] * 100))
    for k, v in info.items():
        print(f"{k:22s} names {v['names_avg']:.1f}  turnover {v['turnover_pa']:.1f}x")
    for part in ["Current value book (no filter)", *configs]:
        print(f"== {part}")
        for w in WW:
            r = rep[part][w]
            print(f"   {w:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}  spread {r['spread']:4.1f}"
                  f"  DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}  EW50 {r['ew50']:5.1f}")


if __name__ == "__main__":
    main()
