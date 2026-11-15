"""Simplest ratio rule (user, 2026-10-18): ratio = stock close / Nifty 50 close. First session of every month, hold
every member whose ratio ROSE over the last calendar month (prior month-end close vs the month-end before it);
exit the ones whose ratio fell. No year anchor, no January all-in, names re-enter freely.
Also the mirror (hold last month's ratio FALLERS) — 1-month reversal check, same machinery.

Book / costs / fills / controls identical to ratio_ytd_n200.py. Windows: NIFTY 50 2008-01 -> latest (full PIT
history) and 2021-01 -> latest; NIFTY 200 2021-01 -> latest (its PIT reconstitution starts 2020-06).

  .venv/Scripts/python.exe scripts/research_grid/ratio_1m.py
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
from ratio_ytd import filter_picker, random_picker, yearly  # noqa: E402
from rotation_roc import run  # noqa: E402

SLIP, CAP, SEEDS, NMAX = 0.0005, 10_000_000.0, 20, 250


def month_change(ratio: pd.DataFrame) -> pd.DataFrame:
    """Row d: ratio at d vs the ratio at the last session of the month BEFORE d's month. At a month-end close this is
    exactly last month's ratio change."""
    per = ratio.index.to_period("M")
    me = ratio.ffill().groupby(per).last()
    base = me.shift(1).reindex(per)
    base.index = ratio.index
    return ratio / base - 1


def study(db, name, ivs, start: date):
    syms = sorted(union_over(ivs, start, date.today()))
    ohlc = load_ohlc(db, syms)
    closes = pd.DataFrame({s: g.set_index("date")["close_price"] for s, g in ohlc.items()}).sort_index()
    opens = pd.DataFrame({s: g.set_index("date")["open_price"] for s, g in ohlc.items()}).sort_index()
    closes = closes[closes[BENCH].notna()]
    opens = opens.reindex(closes.index)
    cols = [c for c in closes.columns if c != BENCH]
    chg = month_change(closes[cols].div(closes[BENCH], axis=0))
    br, sr = charge_rates()
    cal = closes.index[closes.index >= pd.Timestamp(start)]

    rets = closes[cols].pct_change(fill_method=None)
    ew_r = pd.Series([rets.loc[d, [s for s in members_as_of(ivs, d.date()) if s in rets.columns]].mean()
                      for d in cal], index=cal).fillna(0)
    ew = (1 + ew_r).cumprod() * CAP
    nifty = closes[BENCH].reindex(cal) / closes[BENCH].reindex(cal).iloc[0] * CAP

    def go(pick_fn):
        eq, traded = run(opens, closes, closes, ivs, start, None, NMAX, "high", "monthly", CAP, SLIP, br, sr,
                         pick_fn=pick_fn)
        e = eq["equity"]
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        return eq, {"full": stats(e), "yearly": yearly(e), "turnover_per_year": traded / float(e.mean()) / yrs,
                    "avg_held": float(eq["n_held"].mean())}

    rep = {"universe": name, "period": f"{cal[0].date()} -> {cal[-1].date()}",
           "missing_prices": [s for s in syms if s not in ohlc],
           "ew_costfree": {"full": stats(ew), "yearly": yearly(ew)},
           "nifty50": {"full": stats(nifty), "yearly": yearly(nifty)}}
    curves = {"ew": ew, "nifty50": nifty}
    all_eq, rep["all_members_engine"] = go(lambda prev, pool, held: sorted(pool))
    curves["all_members"] = all_eq["equity"]
    for v, frame in (("ratio_up_last_month", chg), ("ratio_down_last_month", -chg)):
        counts: dict = {}
        eq, rep[v] = go(filter_picker(frame, True, False, counts))
        rnd = [go(random_picker(counts, np.random.default_rng(k)))[1]["full"] for k in range(SEEDS)]
        rc = [r["cagr"] for r in rnd]
        rep[v]["random_same_size"] = {"cagr_p05": float(np.percentile(rc, 5)), "cagr_median": float(np.median(rc)),
                                      "cagr_p95": float(np.percentile(rc, 95)),
                                      "maxdd_median": float(np.median([r["max_dd"] for r in rnd])),
                                      "sharpe_median": float(np.median([r["sharpe"] for r in rnd]))}
        curves[v] = eq["equity"]
        print(name, start, v, rep[v]["full"], f"held {rep[v]['avg_held']:.1f}", rep[v]["random_same_size"],
              flush=True)
    return rep, pd.DataFrame(curves)


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    out = HERE / "ratio_1m"
    out.mkdir(exist_ok=True)
    n50 = build_intervals(read_mcwb(), date(2008, 1, 1), date.today())
    n200 = load_membership(db, "NIFTY200", date.today())
    rep = {}
    for key, name, ivs, start in (("NIFTY50_2008", "NIFTY50", n50, date(2008, 1, 1)),
                                  ("NIFTY50_2021", "NIFTY50", n50, date(2021, 1, 1)),
                                  ("NIFTY200_2021", "NIFTY200", n200, date(2021, 1, 1))):
        rep[key], curves = study(db, name, ivs, start)
        curves.to_csv(out / f"equity_{key}.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
