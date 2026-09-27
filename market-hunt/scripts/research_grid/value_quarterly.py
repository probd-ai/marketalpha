"""QUARTERLY value book on NIFTY50: lowest ROC, N=20, buffer 30, rebalanced on session k (0..9) of each quarter.
Lookbacks 22 and 66 sessions. Oct 2020 -> Sep 2026 (Mongo PIT) and Jan 2008 -> Sep 2026 (NSE month-end membership incl.
delisted). Compared with the monthly value book (value_luck_2020.csv / value_luck_2008.csv) and equal-weight NIFTY50.

  .venv/Scripts/python.exe scripts/research_grid/value_quarterly.py
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
from crash_test import build_intervals, load_prices, read_mcwb  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402
from value_luck import LONG_WINDOWS, rng  # noqa: E402

CAP, SLIP = 1_260_000, 0.0005


def variants(opens, closes, iv, start, end, lb):
    stocks = [c for c in closes.columns if c != BENCH]
    roc = closes[stocks].apply(lambda c: c / c.dropna().shift(lb).reindex(c.index) - 1)
    br, sr = charge_rates()
    out, turns = [], []
    for k in range(10):
        eq, traded = run(opens, closes, roc, iv, start, end, 20, "low", f"quarterly+{k}", CAP, SLIP, br, sr, buffer=30)
        out.append(eq["equity"] / eq["equity"].iloc[0])
        turns.append(traded / eq["equity"].mean() / ((eq.index[-1] - eq.index[0]).days / 365.25))
    idx = out[0].index
    return pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(out)}), float(np.mean(turns))


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    oa, ca, _ = load_data(db, ever)
    ivb = build_intervals(read_mcwb(), date(2008, 1, 1), date(2026, 9, 25))
    ob, cb = load_prices(db, sorted({x.symbol for x in ivb}))
    ob, cb = ob.loc[:"2026-09-25"], cb.loc[:"2026-09-25"]
    rep, turn = {}, {}
    for lb in (22, 66):
        qa, turn[f"lb{lb}_2020"] = variants(oa, ca, iv, date(2020, 10, 1), date(2026, 9, 25), lb)
        qb, turn[f"lb{lb}_2008"] = variants(ob, cb, ivb, date(2008, 1, 1), date(2026, 9, 25), lb)
        qa.to_csv(HERE / f"value_quarterly_lb{lb}_2020.csv")
        qb.to_csv(HERE / f"value_quarterly_lb{lb}_2008.csv")
        ew_a = ew_index(ca, iv).reindex(qa.index).ffill()
        ew_b = ew_index(cb, ivb).reindex(qb.index).ffill()
        rep[f"Quarterly lb{lb} 2020-26"] = rng(qa, WW, ew_a)
        rep[f"Quarterly lb{lb} 2008-26"] = rng(qb, LONG_WINDOWS, ew_b)
        print(f"lb{lb} done", flush=True)
    ma = pd.read_csv(HERE / "value_luck_2020.csv", index_col=0, parse_dates=True)
    mb = pd.read_csv(HERE / "value_luck_2008.csv", index_col=0, parse_dates=True)
    rep["Monthly lb22 2020-26"] = rng(ma, WW)
    rep["Monthly lb22 2008-26"] = rng(mb, LONG_WINDOWS)
    rep["turnover"] = turn
    (HERE / "value_quarterly.json").write_text(json.dumps(rep, indent=2, default=float))
    print("turnover:", {k: round(v, 1) for k, v in turn.items()})
    for part, windows in [("Monthly lb22 2020-26", WW), ("Quarterly lb22 2020-26", WW), ("Quarterly lb66 2020-26", WW),
                          ("Monthly lb22 2008-26", LONG_WINDOWS), ("Quarterly lb22 2008-26", LONG_WINDOWS),
                          ("Quarterly lb66 2008-26", LONG_WINDOWS)]:
        print(f"== {part}")
        for w in windows:
            r = rep[part][w]
            ew = f"  EW50 {r['ew50']:5.1f}" if "ew50" in r else ""
            print(f"   {w:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}  spread {r['spread']:4.1f}"
                  f"  DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}{ew}")


if __name__ == "__main__":
    main()
