"""Simple ratio-YTD rule on point-in-time NIFTY 200 (user, 2026-10-18) — ratio_ytd.py moved to a wider universe.

Rule: ratio = stock close / Nifty 50 close, anchored at the last close of the previous year; January all members held;
Feb..Dec keep members with ratio above the anchor. Main variant = no re-entry until January (the one the user
carried forward); re-entry variant alongside. Book / costs / fills identical to ratio_ytd.py.

Window: NIFTY 200 reconstitution events start 2020-06-26 — before that the "membership" is the current list
back-filled (survivorship-biased), so the honest test starts Jan 2021 (first full calendar year). NIFTY 50 is
re-run on the same window for a like-for-like comparison.

Controls: random books of the same size each month (20 seeds, holdings kept first), all-members engine book,
cost-free equal-weight universe, Nifty 50 index.

  .venv/Scripts/python.exe scripts/research_grid/ratio_ytd_n200.py
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
sys.path.insert(0, str(HERE.parent.parent))
from crash_test import build_intervals, read_mcwb  # noqa: E402
from grid_meanrev import BENCH, charge_rates  # noqa: E402
from meanlong_slope import load_ohlc, stats  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402
from ratio_ytd import filter_picker, random_picker, yearly, ytd_change  # noqa: E402
from rotation_roc import run  # noqa: E402

START = date(2021, 1, 1)
SLIP, CAP, SEEDS, NMAX = 0.0005, 10_000_000.0, 20, 250


def study(db, name, ivs):
    syms = sorted(union_over(ivs, START, date.today()))
    ohlc = load_ohlc(db, syms)
    missing = [s for s in syms if s not in ohlc]
    closes = pd.DataFrame({s: g.set_index("date")["close_price"] for s, g in ohlc.items()}).sort_index()
    opens = pd.DataFrame({s: g.set_index("date")["open_price"] for s, g in ohlc.items()}).sort_index()
    closes = closes[closes[BENCH].notna()]
    opens = opens.reindex(closes.index)
    cols = [c for c in closes.columns if c != BENCH]
    ytd = ytd_change(closes[cols].div(closes[BENCH], axis=0))
    br, sr = charge_rates()
    cal = closes.index[closes.index >= pd.Timestamp(START)]

    rets = closes[cols].pct_change(fill_method=None)
    ew_r = pd.Series([rets.loc[d, [s for s in members_as_of(ivs, d.date()) if s in rets.columns]].mean()
                      for d in cal], index=cal).fillna(0)
    ew = (1 + ew_r).cumprod() * CAP
    nifty = closes[BENCH].reindex(cal) / closes[BENCH].reindex(cal).iloc[0] * CAP

    def go(pick_fn):
        eq, traded = run(opens, closes, closes, ivs, START, None, NMAX, "high", "monthly", CAP, SLIP, br, sr,
                         pick_fn=pick_fn)
        e = eq["equity"]
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        return eq, {"full": stats(e), "yearly": yearly(e), "turnover_per_year": traded / float(e.mean()) / yrs,
                    "avg_held": float(eq["n_held"].mean())}

    rep = {"universe": name, "period": f"{cal[0].date()} -> {cal[-1].date()}", "ever_members": len(syms),
           "missing_prices": missing,
           "ew_costfree": {"full": stats(ew), "yearly": yearly(ew)},
           "nifty50": {"full": stats(nifty), "yearly": yearly(nifty)}}
    curves = {"ew": ew, "nifty50": nifty}
    all_eq, rep["all_members_engine"] = go(lambda prev, pool, held: sorted(pool))
    curves["all_members"] = all_eq["equity"]
    sizes = {}
    for v, re_ in (("ytd_no_reentry", False), ("ytd_reentry", True)):
        counts: dict = {}
        eq, rep[v] = go(filter_picker(ytd, re_, True, counts))
        rnd = [go(random_picker(counts, np.random.default_rng(k)))[1]["full"] for k in range(SEEDS)]
        rc = [r["cagr"] for r in rnd]
        rep[v]["random_same_size"] = {"cagr_p05": float(np.percentile(rc, 5)), "cagr_median": float(np.median(rc)),
                                      "cagr_p95": float(np.percentile(rc, 95)),
                                      "maxdd_median": float(np.median([r["max_dd"] for r in rnd])),
                                      "sharpe_median": float(np.median([r["sharpe"] for r in rnd]))}
        curves[v], sizes[v] = eq["equity"], pd.Series(counts)
        print(name, v, rep[v]["full"], f"held {rep[v]['avg_held']:.1f}", rep[v]["random_same_size"], flush=True)
    return rep, pd.DataFrame(curves), pd.DataFrame(sizes)


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    out = HERE / "ratio_ytd_n200"
    out.mkdir(exist_ok=True)
    rep = {}
    for name, ivs in (("NIFTY200", load_membership(db, "NIFTY200", date.today())),
                      ("NIFTY50", build_intervals(read_mcwb(), date(2008, 1, 1), date.today()))):
        rep[name], curves, sizes = study(db, name, ivs)
        curves.to_csv(out / f"equity_{name}.csv")
        sizes.to_csv(out / f"book_size_{name}.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
