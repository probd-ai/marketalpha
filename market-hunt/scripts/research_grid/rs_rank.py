"""Top-20 by 22-session RS change on point-in-time NIFTY 50, Jan 2008 -> Sep 2026, Rs 1 Cr, EW.

RS = close / Nifty 50 close; score = RS / RS 22 own-sessions earlier - 1, highest first. Every stock is
divided by the same Nifty move, so this ordering matches plain 22d price ROC (the "winners" book).
Monthly, signal at the previous close, next-open fills, full costs. Also reports the mirror (lowest
20 by RS change) and a 20-seed random-20 control with identical mechanics.

  .venv/Scripts/python.exe scripts/research_grid/rs_rank.py [--buffer 0]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from crash_test import build_intervals, load_prices, read_mcwb, window_dd  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index  # noqa: E402
from rotation_roc import run, stats  # noqa: E402
from rs_trend import CAP, CRASHES, END, SLIP, START, WINDOWS, sub_cagr  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buffer", type=int, default=0)
    ap.add_argument("--seeds", type=int, default=20)
    args = ap.parse_args()
    ivs = build_intervals(read_mcwb(), START, END)
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    opens, closes = load_prices(db, sorted({iv.symbol for iv in ivs}))
    opens, closes = opens.loc[: pd.Timestamp(END)], closes.loc[: pd.Timestamp(END)]
    stocks = [c for c in closes.columns if c != BENCH]
    rs = closes[stocks].div(closes[BENCH], axis=0)
    rs22 = rs.apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    ew = ew_index(closes, ivs)
    br, sr = charge_rates()

    kw = dict(buffer=args.buffer)
    books = {"top20_rs22": run(opens, closes, rs22, ivs, START, END, 20, "high", "monthly", CAP, SLIP, br, sr, **kw),
             "bottom20_rs22": run(opens, closes, rs22, ivs, START, END, 20, "low", "monthly", CAP, SLIP, br, sr, **kw)}
    idx = books["top20_rs22"][0].index
    curves = {k: v[0]["equity"] for k, v in books.items()}
    curves["EW50"] = CAP * ew.reindex(idx).ffill() / ew.reindex(idx).ffill().iloc[0]
    curves["Nifty50"] = CAP * closes[BENCH].reindex(idx).ffill() / closes[BENCH].reindex(idx).ffill().iloc[0]
    rnd = [run(opens, closes, rs22, ivs, START, END, 20, "random", "monthly", CAP, SLIP, br, sr,
               rng=np.random.default_rng(k), **kw)[0]["equity"] for k in range(args.seeds)]

    rep = {"buffer": args.buffer, "books": {}, "windows": {}, "crashes": {}}
    yrs = (idx[-1] - idx[0]).days / 365.25
    for k, (eq, traded) in books.items():
        rep["books"][k] = {**stats(eq["equity"]), "turnover_pa": traded / eq["equity"].mean() / yrs}
    for k in ["EW50", "Nifty50"]:
        rep["books"][k] = stats(curves[k])
    rs_ = [stats(r) for r in rnd]
    rep["books"]["random20"] = {"cagr": float(np.mean([r["cagr"] for r in rs_])),
                                "cagr_p05": float(np.percentile([r["cagr"] for r in rs_], 5)),
                                "cagr_p95": float(np.percentile([r["cagr"] for r in rs_], 95)),
                                "max_dd": float(np.mean([r["max_dd"] for r in rs_])),
                                "sharpe": float(np.mean([r["sharpe"] for r in rs_]))}
    for w, (a, b) in WINDOWS.items():
        rep["windows"][w] = {k: sub_cagr(c, a, b) for k, c in curves.items()}
        rc = [sub_cagr(r, a, b) for r in rnd]
        rep["windows"][w].update({"random_mean": float(np.mean(rc)), "random_p05": float(np.percentile(rc, 5)),
                                  "random_p95": float(np.percentile(rc, 95))})
    for n, (a, b) in CRASHES.items():
        rep["crashes"][n] = {k: window_dd(c, a, b) for k, c in curves.items()}
    out = HERE / "rs_rank"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / f"equity_buf{args.buffer}.csv")
    (out / f"report_buf{args.buffer}.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
