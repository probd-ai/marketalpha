"""Monthly value book (NIFTY50 22d losers, N=20, buffer 30) rebalanced on EVERY trading day of the month
(session k = 0..20 after the month's first session; k=0 = signal at the previous month's last close).
Oct 2020 -> Sep 2026 (Mongo PIT) and Jan 2008 -> Sep 2026 (NSE month-end membership incl. delisted).
Per-day CAGR / max drawdown, sub-period CAGRs, rank correlation between the two histories (is a "good day" persistent?).

  .venv/Scripts/python.exe scripts/research_grid/value_alldays.py
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
from combine_all import stats  # noqa: E402
from crash_test import build_intervals, load_prices, read_mcwb  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402

CAP, SLIP, K = 1_260_000, 0.0005, 21


def per_day(opens, closes, iv, start, end, windows):
    stocks = [c for c in closes.columns if c != BENCH]
    roc = closes[stocks].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    br, sr = charge_rates()
    rows = []
    for k in range(K):
        eq, _ = run(opens, closes, roc, iv, start, end, 20, "low", f"monthly+{k}", CAP, SLIP, br, sr, buffer=30)
        e = eq["equity"]
        row = {"k": k}
        for w, (a, b) in windows.items():
            s = stats(e.loc[a:b])
            row[f"{w} cagr"] = s["cagr"] * 100
            row[f"{w} dd"] = s["max_dd"] * 100
        rows.append(row)
        print(f"  k={k} done", flush=True)
    return pd.DataFrame(rows).set_index("k")


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    oa, ca, _ = load_data(db, ever)
    wa = {"2020-26": ("2020-10-01", "2026-09-25"), "2020-23": ("2020-10-01", "2023-06-30"), "2023-26": ("2023-07-01", "2026-09-25")}
    A = per_day(oa, ca, iv, date(2020, 10, 1), date(2026, 9, 25), wa)
    ew_a = stats(ew_index(ca, iv).loc["2020-10-01":"2026-09-25"])["cagr"] * 100
    ivb = build_intervals(read_mcwb(), date(2008, 1, 1), date(2026, 9, 25))
    ob, cb = load_prices(db, sorted({x.symbol for x in ivb}))
    ob, cb = ob.loc[:"2026-09-25"], cb.loc[:"2026-09-25"]
    wb = {"2008-26": ("2008-01-01", "2026-09-25"), "2008-13": ("2008-01-01", "2013-12-31"), "2014-20": ("2014-01-01", "2020-09-30")}
    B = per_day(ob, cb, ivb, date(2008, 1, 1), date(2026, 9, 25), wb)
    ew_b = stats(ew_index(cb, ivb).loc["2008-01-01":"2026-09-25"])["cagr"] * 100
    T = A.join(B)
    T.to_csv(HERE / "value_alldays.csv")
    rep = {"ew50_2020_26": ew_a, "ew50_2008_26": ew_b,
           "rank_corr_2020_26_vs_2008_20": float(T["2020-26 cagr"].rank().corr(T["2014-20 cagr"].rank())),
           "rank_corr_2008_13_vs_2014_20": float(T["2008-13 cagr"].rank().corr(T["2014-20 cagr"].rank())),
           "summary": {c: {"min": float(T[c].min()), "median": float(T[c].median()), "max": float(T[c].max())}
                       for c in T.columns}}
    (HERE / "value_alldays.json").write_text(json.dumps(rep, indent=2, default=float))
    pd.set_option("display.width", 200)
    print(T.round(1).to_string())
    print(json.dumps({k: v for k, v in rep.items() if k != "summary"}, indent=1, default=float))
    for c in ["2020-26 cagr", "2008-26 cagr", "2020-26 dd", "2008-26 dd"]:
        s = rep["summary"][c]
        print(f"{c:14s} min {s['min']:6.1f}  median {s['median']:6.1f}  max {s['max']:6.1f}")


if __name__ == "__main__":
    main()
