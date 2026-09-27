"""Daily VALUE book: every session buy the biggest 22d loser in point-in-time NIFTY50 not already held, hold 21
sessions (roll if still the day's pick), tranche equity/21 -> ~21 names. No filter (all members eligible).

  A) Oct 2020 -> Sep 2026, Mongo PIT NIFTY50; start-day luck k=0..9; vs monthly loser book (value_luck_2020.csv) and EW50
  B) Jan 2008 -> Sep 2026, NSE month-end membership incl. delisted; k=0..9; vs monthly loser book (value_luck_2008.csv)
  C) Whole book AGGRESSIVE 40/35/25 with the daily value book (10 x 5 x 5 = 250 combinations)

  .venv/Scripts/python.exe scripts/research_grid/value_daily.py
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
from combine_all import WINDOWS as WW  # noqa: E402
from crash_test import build_intervals, load_prices, read_mcwb  # noqa: E402
from daily_tranche import run_daily  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from hedge_compare import rd, to_inr  # noqa: E402
from longhold import hold_stats  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from multi_blend import blend_multi  # noqa: E402
from value_luck import AGG, LONG_WINDOWS, rng  # noqa: E402

CAP = 1_260_000


def daily_variants(opens, closes, iv, start, end):
    stocks = [c for c in closes.columns if c != BENCH]
    roc = closes[stocks].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    elig = pd.DataFrame(True, index=closes.index, columns=stocks)
    br, sr = charge_rates()
    out, info = [], None
    for k in range(10):
        log = [] if k == 0 else None
        eq, traded = run_daily(opens, closes, roc, iv, elig, start, end, br, sr, capital=CAP, skip=k,
                               held_log=log, ascending=True)
        out.append(eq["equity"] / eq["equity"].iloc[0])
        if log is not None:
            yrs = (eq.index[-1] - eq.index[0]).days / 365.25
            info = {**hold_stats(log), "turnover_pa": traded / eq["equity"].mean() / yrs}
    idx = out[0].index
    return pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(out)}), info


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, _ = load_data(db, ever)
    da, info_a = daily_variants(opens, closes, iv, "2020-10-01", "2026-09-25")
    da.to_csv(HERE / "value_daily_2020.csv")
    ew_a = ew_index(closes, iv).reindex(da.index).ffill()
    monthly_a = pd.read_csv(HERE / "value_luck_2020.csv", index_col=0, parse_dates=True).reindex(da.index)
    rep = {"info_2020": info_a, "A_daily": rng(da, WW, ew_a), "A_monthly": rng(monthly_a, WW)}
    print("A done", flush=True)

    ivb = build_intervals(read_mcwb(), date(2008, 1, 1), date(2026, 9, 25))
    ob, cb = load_prices(db, sorted({x.symbol for x in ivb}))
    ob, cb = ob.loc[:"2026-09-25"], cb.loc[:"2026-09-25"]
    db_, _ = daily_variants(ob, cb, ivb, "2008-01-01", "2026-09-25")
    db_.to_csv(HERE / "value_daily_2008.csv")
    ew_b = ew_index(cb, ivb).reindex(db_.index).ffill()
    monthly_b = pd.read_csv(HERE / "value_luck_2008.csv", index_col=0, parse_dates=True).reindex(db_.index)
    rep["B_daily"] = rng(db_, LONG_WINDOWS, ew_b)
    rep["B_monthly"] = rng(monthly_b, LONG_WINDOWS)
    print("B done", flush=True)

    gro = pd.read_csv(HERE / "n200_8wcap10_deploy.csv", index_col=0, parse_dates=True)
    sat = pd.read_csv(HERE / "n750_cap_weekly8w_cap10deploy.csv", index_col=0, parse_dates=True)
    idx = da.index.intersection(gro.index).intersection(sat.index)
    fx = rd("FX_IDC_USDINR.csv")
    gold, ndx = to_inr(rd("TVC_GOLD.csv"), fx, idx), to_inr(rd("NASDAQ_NDX.csv"), fx, idx)
    runs = []
    for vi, gi, si in product(da.columns, gro.columns, sat.columns):
        px = pd.DataFrame({"Value": da[vi].reindex(idx), "Gold": gold, "NDX": ndx,
                           "Growth": gro[gi].reindex(idx), "Satellite": sat[si].reindex(idx)}).ffill()
        runs.append(blend_multi(px, AGG))
    whole = pd.DataFrame({i: r for i, r in enumerate(runs)})
    whole.to_csv(HERE / "whole_book_aggressive_dailyvalue.csv")
    rep["C_whole_daily_value"] = rng(whole, WW)
    rep["C_whole_monthly_value"] = json.loads((HERE / "value_luck.json").read_text())["C_whole_book_aggressive"]
    (HERE / "value_daily.json").write_text(json.dumps(rep, indent=2, default=float))

    print("daily value book (2020 run):", {k: round(v, 1) if isinstance(v, float) else v for k, v in info_a.items()})
    for part, windows in [("A_daily", WW), ("A_monthly", WW), ("B_daily", LONG_WINDOWS), ("B_monthly", LONG_WINDOWS),
                          ("C_whole_daily_value", WW), ("C_whole_monthly_value", WW)]:
        print(f"== {part}")
        for w in windows:
            r = rep[part][w]
            ew = f"  EW50 {r['ew50']:5.1f}" if "ew50" in r else ""
            print(f"   {w:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}  spread {r['spread']:4.1f}"
                  f"  DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}{ew}")


if __name__ == "__main__":
    main()
