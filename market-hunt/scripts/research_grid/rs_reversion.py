"""RS long-term laggard book on point-in-time NIFTY 50, Jan 2008 -> Sep 2026, Rs 1 Cr, equal weight.

Monthly (signal at previous close, next-open fills, full costs):
  BUY  a member that is in the BOTTOM 20 by 22-session ROC AND whose RS (close / Nifty 50) is BELOW
       its 222-session SMA  (--entry all_below: every RS<SMA member, ignoring ROC — the first run).
  SELL a holding only when RS is back ABOVE its 222-session SMA AND its 22-session ROC ranks outside
       the lowest 30 (i.e. in the top-20 winners) — otherwise keep holding.
Book size floats; equal weight = equity / names, retained names never trimmed.
Benchmarks: equal-weight NIFTY 50 paying the same monthly costs (tilt k=0), cost-free EW, Nifty 50,
the reversal tilt k=1 and the plain 22d-loser book.

  .venv/Scripts/python.exe scripts/research_grid/rs_reversion.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from crash_test import build_intervals, load_prices, read_mcwb, window_dd  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index  # noqa: E402
from rotation_roc import run, stats  # noqa: E402
from rs_trend import CAP, CRASHES, END, MA, SLIP, START, WINDOWS, sub_cagr  # noqa: E402
from tilt import run_tilt  # noqa: E402

BUFFER, ENTRY_N = 30, 20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", choices=["bottom20_and_below", "all_below"], default="bottom20_and_below")
    args = ap.parse_args()
    ivs = build_intervals(read_mcwb(), START, END)
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    opens, closes = load_prices(db, sorted({iv.symbol for iv in ivs}))
    opens, closes = opens.loc[: pd.Timestamp(END)], closes.loc[: pd.Timestamp(END)]
    stocks = [c for c in closes.columns if c != BENCH]
    rs = closes[stocks].div(closes[BENCH], axis=0)
    rs_ma = rs.apply(lambda c: c.dropna().rolling(MA, min_periods=MA).mean().reindex(c.index))
    roc = closes[stocks].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    br, sr = charge_rates()

    def pick(prev, pool, held):
        pool = [s for s in pool if not pd.isna(rs_ma.at[prev, s])]
        below = {s for s in pool if rs.at[prev, s] < rs_ma.at[prev, s]}
        ranked = list(roc.loc[prev, pool].sort_values().index)          # biggest loser first
        winners = set(ranked[BUFFER:])                                   # outside the lowest 30
        entries = below & set(ranked[:ENTRY_N]) if args.entry == "bottom20_and_below" else below
        exits = {s for s in held if s in pool and s not in below and s in winners}
        return sorted((held - exits) | entries)

    eq, traded = run(opens, closes, roc, ivs, START, END, 20, "low", "monthly", CAP, SLIP, br, sr, pick_fn=pick)
    k0, _ = run_tilt(opens, closes, roc, ivs, 0.0, 0.005, br, sr)
    k1, _ = run_tilt(opens, closes, roc, ivs, 1.0, 0.005, br, sr)
    loser, _ = run(opens, closes, roc, ivs, START, END, 20, "low", "monthly", CAP, SLIP, br, sr, buffer=30)
    idx = eq.index
    ew = ew_index(closes, ivs).reindex(idx).ffill()
    curves = {"rs_laggard": eq["equity"], "EW50 w/ costs (k=0)": k0["equity"], "tilt k=1": k1["equity"],
              "loser20 buf30": loser["equity"], "EW50 cost-free": CAP * ew / ew.iloc[0],
              "Nifty50": CAP * closes[BENCH].reindex(idx).ffill() / closes[BENCH].reindex(idx).ffill().iloc[0]}
    yrs = (idx[-1] - idx[0]).days / 365.25
    rep = {"books": {k: stats(c) for k, c in curves.items()}, "windows": {}, "crashes": {}}
    rep["books"]["rs_laggard"].update({"turnover_pa": traded / eq["equity"].mean() / yrs,
                                       "avg_names": float(eq["n_held"].mean()),
                                       "min_names": int(eq["n_held"].min()), "max_names": int(eq["n_held"].max()),
                                       "avg_invested": float((eq["held"] / eq["equity"]).mean())})
    for w, (a, b) in WINDOWS.items():
        rep["windows"][w] = {k: sub_cagr(c, a, b) for k, c in curves.items()}
    for n, (a, b) in CRASHES.items():
        rep["crashes"][n] = {k: window_dd(c, a, b) for k, c in curves.items()}
    yr = pd.DataFrame(curves).resample("YE").last()
    yr = pd.concat([pd.DataFrame(curves).iloc[[0]], yr]).pct_change().dropna()
    yr.index = yr.index.year
    out = HERE / "rs_reversion" / args.entry
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    yr.to_csv(out / "yearly.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
