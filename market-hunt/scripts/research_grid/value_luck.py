"""Timing luck of the VALUE book (NIFTY50 22d losers, N=20, buffer 30, monthly) and its effect on the whole book.

  A) Oct 2020 -> Sep 2026, point-in-time NIFTY50 from Mongo (the window the whole book uses): rebalance on session
     k = 0..9 of the month ("monthly+k"; k=1 = signal at the first session's close, fill next open).
  B) Jan 2008 -> Sep 2026, NSE month-end membership incl. delisted names (crash_test loaders): same k = 0..9.
  C) Whole book, AGGRESSIVE 40/35/25 (value 28 / gold 8 / NDX 4 / growth 35 / satellite 25), every value variant x
     growth variant x satellite variant (10 x 5 x 5 = 250), monthly rebalance.

  .venv/Scripts/python.exe scripts/research_grid/value_luck.py
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
from combine_all import WINDOWS as WW, stats  # noqa: E402
from crash_test import build_intervals, load_prices, read_mcwb  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from hedge_compare import rd, to_inr  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from multi_blend import blend_multi  # noqa: E402
from rotation_roc import run  # noqa: E402

CAP, SLIP = 1_260_000, 0.0005
AGG = (0.28, 0.08, 0.04, 0.35, 0.25)
LONG_WINDOWS = {"2008-2026": ("2008-01-01", "2026-09-25"), "2008-2013": ("2008-01-01", "2013-12-31"),
                "2014-2020": ("2014-01-01", "2020-09-30"), "2020-2026": ("2020-10-01", "2026-09-25")}


def value_variants(opens, closes, iv, start, end=None):
    stocks = [c for c in closes.columns if c != BENCH]
    roc = closes[stocks].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    br, sr = charge_rates()
    out = []
    for k in range(10):
        eq, _ = run(opens, closes, roc, iv, start, end, 20, "low", f"monthly+{k}", CAP, SLIP, br, sr, buffer=30)
        out.append(eq["equity"] / eq["equity"].iloc[0])
    idx = out[0].index
    return pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(out)})


def rng(df, windows, extra=None):
    res = {}
    for w, (a, b) in windows.items():
        sub = df.loc[a:b]
        ss = [stats(sub[c]) for c in sub.columns]
        cg = np.array([x["cagr"] for x in ss]) * 100
        dd = np.array([x["max_dd"] for x in ss]) * 100
        res[w] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                  "spread": cg.max() - cg.min(), "dd_worst": dd.min(), "dd_best": dd.max()}
        if extra is not None:
            res[w]["ew50"] = stats(extra.loc[a:b])["cagr"] * 100
    return res


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    # A) 2020-2026, Mongo PIT
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, _ = load_data(db, ever)
    va = value_variants(opens, closes, iv, date(2020, 10, 1))
    va.to_csv(HERE / "value_luck_2020.csv")
    ew_a = ew_index(closes, iv).reindex(va.index).ffill()
    rep = {"A_2020_2026": rng(va, WW, ew_a)}
    print("A done", flush=True)
    # B) 2008-2026, NSE month-end membership incl. delisted
    ivb = build_intervals(read_mcwb(), date(2008, 1, 1), date(2026, 9, 25))
    ob, cb = load_prices(db, sorted({x.symbol for x in ivb}))
    ob, cb = ob.loc[:"2026-09-25"], cb.loc[:"2026-09-25"]
    vb = value_variants(ob, cb, ivb, date(2008, 1, 1), date(2026, 9, 25))
    vb.to_csv(HERE / "value_luck_2008.csv")
    ew_b = ew_index(cb, ivb).reindex(vb.index).ffill()
    rep["B_2008_2026"] = rng(vb, LONG_WINDOWS, ew_b)
    print("B done", flush=True)
    # C) whole book, aggressive, all combinations
    gro = pd.read_csv(HERE / "n200_8wcap10_deploy.csv", index_col=0, parse_dates=True)
    sat = pd.read_csv(HERE / "n750_cap_weekly8w_cap10deploy.csv", index_col=0, parse_dates=True)
    idx = va.index.intersection(gro.index).intersection(sat.index)
    idx = idx[(idx >= "2020-10-01") & (idx <= "2026-09-25")]
    fx = rd("FX_IDC_USDINR.csv")
    gold, ndx = to_inr(rd("TVC_GOLD.csv"), fx, idx), to_inr(rd("NASDAQ_NDX.csv"), fx, idx)
    runs = []
    for vi, gi, si in product(va.columns, gro.columns, sat.columns):
        px = pd.DataFrame({"Value": va[vi].reindex(idx), "Gold": gold, "NDX": ndx,
                           "Growth": gro[gi].reindex(idx), "Satellite": sat[si].reindex(idx)}).ffill()
        runs.append(blend_multi(px, AGG))
    whole = pd.DataFrame({i: r for i, r in enumerate(runs)})
    whole.to_csv(HERE / "whole_book_aggressive_250.csv")
    rep["C_whole_book_aggressive"] = rng(whole, WW)
    (HERE / "value_luck.json").write_text(json.dumps(rep, indent=2, default=float))

    for part, windows in [("A_2020_2026", WW), ("B_2008_2026", LONG_WINDOWS), ("C_whole_book_aggressive", WW)]:
        print(f"== {part}")
        for w in windows:
            r = rep[part][w]
            ew = f"  EW50 {r['ew50']:5.1f}" if "ew50" in r else ""
            print(f"   {w:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}  spread {r['spread']:4.1f}"
                  f"  DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}{ew}")


if __name__ == "__main__":
    main()
