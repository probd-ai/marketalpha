"""RS-trend books on point-in-time NIFTY 50, Jan 2008 -> Sep 2026, Rs 1 Cr, equal weight.

RS  = stock close / Nifty 50 close;  RS_MA = 222-session SMA of RS (the stock's own sessions).
Signal at the previous close, fills at the next open, monthly (first session), full costs.
  A  rs_trend      : hold EVERY member with RS > RS_MA, equal weight (cash if none qualify).
     (--rule price_above: close > 222-session SMA of close -> price_ma_nifty50/;
      --rule rsma_rising: 222-session SMA of RS higher than 22 sessions ago -> rsma_rising_nifty50/;
      "A_rs_trend"/"B_..." then refer to that filter)
  B  loser_rsfilter: the 22d-loser book (N=20, buffer 30) but only RS > RS_MA names; a holding that
                     drops to RS <= RS_MA is sold (filter applies to exits too).
Controls: equal-weight PIT NIFTY50 and Nifty 50 buy & hold; plain loser book; B's random twin.
Membership from NSE month-end files (crash_test.build_intervals); delisted prices from crash_prices/.
RS and RS_MA are saved locally to rs_nifty50/ (nothing is written to Mongo).

  .venv/Scripts/python.exe scripts/research_grid/rs_trend.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from crash_test import build_intervals, load_prices, read_mcwb, window_dd  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index  # noqa: E402
from membership import members_as_of  # noqa: E402
from rotation_roc import run, stats  # noqa: E402

START, END = date(2008, 1, 1), date(2026, 9, 25)
CAP, SLIP, SEEDS, MA = 10_000_000, 0.0005, 20, 222
WINDOWS = {"full 2008-2026": ("2008-01-01", "2026-09-25"), "2008-2013": ("2008-01-01", "2013-12-31"),
           "2014-2020": ("2014-01-01", "2020-09-30"), "2020-2026": ("2020-10-01", "2026-09-25")}
CRASHES = {"2008 GFC": ("2008-01-01", "2009-03-31"), "2010-11": ("2010-11-01", "2011-12-31"),
           "2015-16": ("2015-03-01", "2016-02-29"), "2020 COVID": ("2020-01-01", "2020-03-31"),
           "2024-25": ("2024-09-01", "2025-03-31")}


def sub_cagr(s: pd.Series, a: str, b: str) -> float:
    w = s.loc[a:b]
    return (w.iloc[-1] / w.iloc[0]) ** (365.25 / (w.index[-1] - w.index[0]).days) - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", choices=["rs_above", "price_above", "rsma_rising"], default="rs_above",
                    help="rs_above: RS > 222d SMA of RS;  price_above: close > 222d SMA of close;  "
                         "rsma_rising: 222d SMA of RS higher than 22 sessions ago")
    args = ap.parse_args()
    ivs = build_intervals(read_mcwb(), START, END)
    syms = sorted({iv.symbol for iv in ivs})
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    opens, closes = load_prices(db, syms)
    opens, closes = opens.loc[: pd.Timestamp(END)], closes.loc[: pd.Timestamp(END)]
    stocks = [c for c in closes.columns if c != BENCH]

    # RS on each stock's own sessions (a gap day never counts as a bar)
    rs = closes[stocks].div(closes[BENCH], axis=0)
    rs_ma = rs.apply(lambda c: c.dropna().rolling(MA, min_periods=MA).mean().reindex(c.index))
    out = HERE / {"rs_above": "rs_nifty50", "price_above": "price_ma_nifty50",
                  "rsma_rising": "rsma_rising_nifty50"}[args.rule]
    out.mkdir(exist_ok=True)
    if args.rule == "rs_above":
        elig = (rs > rs_ma).fillna(False)
        rs.to_csv(out / "rs.csv")
        rs_ma.to_csv(out / "rs_ma222.csv")
    elif args.rule == "rsma_rising":
        # slope on the stock's own sessions: SMA now vs SMA 22 own-sessions earlier
        elig = (rs_ma > rs_ma.apply(lambda c: c.dropna().shift(22).reindex(c.index))).fillna(False)
    else:
        px_ma = closes[stocks].apply(lambda c: c.dropna().rolling(MA, min_periods=MA).mean().reindex(c.index))
        elig = (closes[stocks] > px_ma).fillna(False)
        px_ma.to_csv(out / "price_ma222.csv")

    roc22 = closes[stocks].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    ew = ew_index(closes, ivs)
    br, sr = charge_rates()
    books = {
        "A_rs_trend": run(opens, closes, roc22, ivs, START, END, 50, "alpha", "monthly", CAP, SLIP, br, sr,
                          eligible=elig),
        "B_loser_rsfilter": run(opens, closes, roc22, ivs, START, END, 20, "low", "monthly", CAP, SLIP, br, sr,
                                buffer=30, eligible=elig, elig_exit=True),
        "loser_plain": run(opens, closes, roc22, ivs, START, END, 20, "low", "monthly", CAP, SLIP, br, sr, buffer=30),
    }
    idx = books["A_rs_trend"][0].index
    curves = {k: v[0]["equity"] for k, v in books.items()}
    curves["EW50"] = CAP * ew.reindex(idx).ffill() / ew.reindex(idx).ffill().iloc[0]
    curves["Nifty50"] = CAP * closes[BENCH].reindex(idx).ffill() / closes[BENCH].reindex(idx).ffill().iloc[0]
    rnd = [run(opens, closes, roc22, ivs, START, END, 20, "random", "monthly", CAP, SLIP, br, sr,
               rng=np.random.default_rng(k), buffer=30, eligible=elig, elig_exit=True)[0]["equity"]
           for k in range(SEEDS)]

    rep = {"windows": {}, "crashes": {}, "books": {}}
    for w, (a, b) in WINDOWS.items():
        rep["windows"][w] = {k: sub_cagr(c, a, b) for k, c in curves.items()}
        rc = [sub_cagr(r, a, b) for r in rnd]
        rep["windows"][w]["B_random_mean"] = float(np.mean(rc))
        rep["windows"][w]["B_random_p05"] = float(np.percentile(rc, 5))
        rep["windows"][w]["B_random_p95"] = float(np.percentile(rc, 95))
    for n, (a, b) in CRASHES.items():
        rep["crashes"][n] = {k: window_dd(c, a, b) for k, c in curves.items()}
    yrs = (idx[-1] - idx[0]).days / 365.25
    for k, (eq, traded) in books.items():
        inv = eq["held"] / eq["equity"]
        rep["books"][k] = {**stats(eq["equity"]), "turnover_pa": traded / eq["equity"].mean() / yrs,
                           "avg_names": float(eq["n_held"].mean()), "min_names": int(eq["n_held"].min()),
                           "max_names": int(eq["n_held"].max()), "avg_invested": float(inv.mean()),
                           "months_fully_cash": int((eq["n_held"].resample("ME").last() == 0).sum())}
    for k in ["EW50", "Nifty50"]:
        rep["books"][k] = stats(curves[k])
    firsts = pd.Series(idx, index=idx).groupby(idx.to_period("M")).first()
    cnt = [sum(bool(elig.at[d, s]) for s in members_as_of(ivs, d.date()) if s in elig.columns) for d in firsts]
    rep["eligible_per_month"] = {"mean": float(np.mean(cnt)), "min": int(min(cnt)), "max": int(max(cnt))}

    pd.DataFrame(curves).to_csv(out / "equity.csv")
    yr = pd.DataFrame(curves).resample("YE").last()
    yr = pd.concat([pd.DataFrame(curves).iloc[[0]], yr]).pct_change().dropna()
    yr.index = yr.index.year
    yr.to_csv(out / "yearly.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
