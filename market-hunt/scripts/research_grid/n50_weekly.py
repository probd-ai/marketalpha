"""Alpha + weekly top-5 tranches (hold 8 weeks) on point-in-time NIFTY50, Oct 2020 -> Sep 2026, honest harness.

Variants (weekday luck k=0..4 each): uncapped; 10% cap + trim + cash deploy. Compared with the monthly value book
(value_luck_2020.csv, 10 timing variants) and equal-weight NIFTY50. Also the whole book AGGRESSIVE 40/35/25 with this
book in the value slot (5 x 5 x 5 = 125 combinations) vs the monthly value book (value_luck.json).
TrueVX indicators start 2018 and delisted names have none, so no 2008 history here.

  .venv/Scripts/python.exe scripts/research_grid/n50_weekly.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from alpha_test import RULES, load_means  # noqa: E402
from combine_all import WINDOWS as WW  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from hedge_compare import rd, to_inr  # noqa: E402
from longhold import hold_stats  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402
from multi_blend import blend_multi  # noqa: E402
from value_luck import AGG, rng  # noqa: E402
from weekly_tranche import run_weekly  # noqa: E402

CAP = 1_260_000
CONFIGS = {"N50 weekly 8w no cap": dict(weeks=8, slot_div=40),
           "N50 weekly 8w cap 10% +deploy": dict(weeks=8, slot_div=40, max_w=0.10, deploy_cash=True)}


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
    roc22 = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    br, sr = charge_rates()
    me = pd.Series(closes.index, index=closes.index).loc["2020-10-01":].groupby(closes.loc["2020-10-01":].index.to_period("M")).last()
    breadth = [int(sum(bool(elig.at[d, s]) for s in members_as_of(iv, d.date()) if s in elig.columns)) for d in me]

    sets, info = {}, {}
    for name, kw in CONFIGS.items():
        cs = []
        for k in range(5):
            log, book = ([], []) if k == 0 else (None, None)
            eq, traded = run_weekly(opens, closes, roc22, iv, elig, "2020-10-01", "2026-09-25", br, sr, k=k,
                                    capital=CAP, held_log=log, book_log=book, **kw)
            cs.append(eq["equity"] / eq["equity"].iloc[0])
            if k == 0:
                yrs = (eq.index[-1] - eq.index[0]).days / 365.25
                cash = [c / (c + sum(b.values())) for _, c, b in book][40:]
                big = [max((v / (c + sum(b.values())) for v in b.values()), default=0) for _, c, b in book]
                info[name] = {**hold_stats(log), "turnover_pa": traded / eq["equity"].mean() / yrs,
                              "avg_cash": float(np.mean(cash)), "peak_weight": float(max(big))}
        idx = cs[0].index
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)})
        sets[name].to_csv(HERE / f"n50_{name.split('N50 ')[1].replace(' ', '').replace('%', '').replace('+', '_')}.csv")
        print(f"{name} done", flush=True)
    idx = sets[list(CONFIGS)[0]].index
    ew = ew_index(closes, iv).reindex(idx).ffill()
    monthly = pd.read_csv(HERE / "value_luck_2020.csv", index_col=0, parse_dates=True).reindex(idx)
    rep = {"breadth": {"eligible_avg": float(np.mean(breadth)), "eligible_min": int(min(breadth)),
                       "months_below_5": int(sum(b < 5 for b in breadth)), "months": len(breadth)},
           "info": info, "Monthly value book": rng(monthly, WW, ew)}
    for name, df in sets.items():
        rep[name] = rng(df, WW, ew)

    gro = pd.read_csv(HERE / "n200_8wcap10_deploy.csv", index_col=0, parse_dates=True)
    sat = pd.read_csv(HERE / "n750_cap_weekly8w_cap10deploy.csv", index_col=0, parse_dates=True)
    v = sets["N50 weekly 8w cap 10% +deploy"]
    ix = v.index.intersection(gro.index).intersection(sat.index)
    fx = rd("FX_IDC_USDINR.csv")
    gold, ndx = to_inr(rd("TVC_GOLD.csv"), fx, ix), to_inr(rd("NASDAQ_NDX.csv"), fx, ix)
    runs = []
    for vi, gi, si in product(v.columns, gro.columns, sat.columns):
        px = pd.DataFrame({"Value": v[vi].reindex(ix), "Gold": gold, "NDX": ndx,
                           "Growth": gro[gi].reindex(ix), "Satellite": sat[si].reindex(ix)}).ffill()
        runs.append(blend_multi(px, AGG))
    rep["Whole book with N50 weekly (cap 10%) as value"] = rng(pd.DataFrame({i: r for i, r in enumerate(runs)}), WW)
    rep["Whole book with monthly value book"] = json.loads((HERE / "value_luck.json").read_text())["C_whole_book_aggressive"]
    (HERE / "n50_weekly.json").write_text(json.dumps(rep, indent=2, default=float))

    print("breadth:", rep["breadth"])
    for k, x in info.items():
        print(f"{k:32s} names {x['names_avg']:.1f} [{x['names_min']}-{x['names_max']}]  hold median {x['hold_median_days']:.0f}d  "
              f"turnover {x['turnover_pa']:.1f}x  avg cash {x['avg_cash'] * 100:.1f}%  peak weight {x['peak_weight'] * 100:.1f}%")
    for part in ["Monthly value book", *CONFIGS, "Whole book with N50 weekly (cap 10%) as value", "Whole book with monthly value book"]:
        print(f"== {part}")
        for w in WW:
            r = rep[part][w]
            ew_ = f"  EW50 {r['ew50']:5.1f}" if "ew50" in r else ""
            print(f"   {w:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}  spread {r['spread']:4.1f}"
                  f"  DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}{ew_}")


if __name__ == "__main__":
    main()
