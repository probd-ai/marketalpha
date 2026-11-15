"""Price-ratio filter on point-in-time NIFTY 50 — no TrueVX, just ratio = stock close / Nifty 50 close.

Rule (user, 2026-10-18), variant YTD:
  * the ratio is re-anchored every calendar year at the last close of the previous year (= 0%);
  * January: every NIFTY 50 member is held (no filter);
  * Feb..Dec, first session of the month: hold the members whose ratio is ABOVE its year-start anchor
    (ratio % change from the anchor > 0), drop the ones below. Signal on the previous close, fill at the open.
  * reentry=True (default): a dropped name comes back the month its YTD ratio turns positive again;
    reentry=False: once dropped it stays out until next January.
  Variant ROLL222: same monthly filter, but ratio change over the trailing 222 sessions (no January reset).

Book: equal-weight seats among the names that pass; retained names are never trimmed (winners run), freed cash
is spread over the picks; nothing passes -> cash (0% yield). PIT membership (NSE mcwb), delisted prices from
crash_prices/, statutory charges + 5 bp slippage — all via rotation_roc.run, same harness as meanlong_streak.py.
A stock with no price on the anchor day (listed mid-year) is anchored at its first close of that year.

Controls: ALL-members book through the same engine (costs included), random books of the SAME size as the
filter's each month (20 seeds; held names kept first -> lowest turnover, conservative), cost-free EW50, Nifty 50.

  .venv/Scripts/python.exe scripts/research_grid/ratio_ytd.py
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
from crash_test import CRASHES, build_intervals, read_mcwb, window_dd  # noqa: E402
from grid_meanrev import BENCH, charge_rates  # noqa: E402
from meanlong_slope import ERAS, START, load_ohlc, stats  # noqa: E402
from membership import members_as_of  # noqa: E402
from rotation_roc import run  # noqa: E402

SLIP, CAP, SEEDS, ROLL = 0.0005, 10_000_000.0, 20, 222
NMAX = 60                                          # > index size: the book holds every name that passes


def ytd_change(ratio: pd.DataFrame) -> pd.DataFrame:
    """Ratio % change from its year-start anchor (last close of the prior year; first close of the year if the
    stock had no price then). Row d answers 'where is the ratio vs this year's anchor at d's close'."""
    yr = ratio.index.year
    prior_last = ratio.ffill().groupby(yr).last()                       # last known ratio of each year
    anchor = pd.DataFrame(index=ratio.index, columns=ratio.columns, dtype=float)
    for y in np.unique(yr):
        rows = yr == y
        a = prior_last.loc[y - 1] if (y - 1) in prior_last.index else pd.Series(np.nan, index=ratio.columns)
        first = ratio[rows].bfill().iloc[0]
        anchor.loc[rows] = np.tile(a.fillna(first).to_numpy(), (rows.sum(), 1))
    return ratio / anchor - 1


def filter_picker(chg: pd.DataFrame, reentry: bool, jan_all: bool, counts: dict):
    """pick_fn for rotation_roc.run: names with chg > 0 at the signal close; January -> everyone (jan_all)."""
    dropped: dict[int, set] = {}

    def pick(prev, pool, held):
        if jan_all and prev.month == 12:              # monthly schedule: signal close in Dec = January rebalance
            out = sorted(pool)
        else:
            row = chg.loc[prev] if prev in chg.index else pd.Series(dtype=float)
            out = sorted(s for s in pool if row.get(s, np.nan) > 0)
            if not reentry:
                gone = dropped.setdefault(prev.year, set())
                gone |= {s for s in pool if not row.get(s, np.nan) > 0}
                out = [s for s in out if s not in gone]
        counts[prev] = len(out)
        return out
    return pick


def random_picker(counts: dict, rng):
    """Same book size as the filter on each date; holdings kept first (minimum turnover), rest drawn at random."""
    def pick(prev, pool, held):
        k = counts.get(prev, len(pool))
        keep = [s for s in sorted(held) if s in pool]
        if len(keep) >= k:
            return list(rng.choice(keep, size=k, replace=False)) if k else []
        cand = [s for s in sorted(pool) if s not in keep]
        return keep + list(rng.choice(cand, size=min(k - len(keep), len(cand)), replace=False))
    return pick


def era_stats(eq: pd.Series) -> dict:
    return {n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()}


def yearly(eq: pd.Series) -> dict:
    y = eq.groupby(eq.index.year).last()
    first = eq.iloc[0]
    r = y.pct_change()
    r.iloc[0] = y.iloc[0] / first - 1
    return {int(k): float(v) for k, v in r.items()}


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    ivs = build_intervals(read_mcwb(), START, date.today())
    syms = sorted({iv.symbol for iv in ivs})
    ohlc = load_ohlc(db, syms)
    closes = pd.DataFrame({s: g.set_index("date")["close_price"] for s, g in ohlc.items()}).sort_index()
    opens = pd.DataFrame({s: g.set_index("date")["open_price"] for s, g in ohlc.items()}).sort_index()
    closes = closes[closes[BENCH].notna()]                               # index sessions only
    opens = opens.reindex(closes.index)
    cols = [c for c in closes.columns if c != BENCH]
    ratio = closes[cols].div(closes[BENCH], axis=0)
    ytd = ytd_change(ratio)
    roll = ratio.apply(lambda c: c / c.dropna().shift(ROLL).reindex(c.index) - 1)
    live = closes.copy()                                                 # pool = members with a close at signal
    br, sr = charge_rates()
    cal = closes.index[closes.index >= pd.Timestamp(START)]

    rets = closes[cols].pct_change(fill_method=None)
    ew_r = pd.Series([rets.loc[d, [s for s in members_as_of(ivs, d.date()) if s in rets.columns]].mean()
                      for d in cal], index=cal).fillna(0)
    ew = (1 + ew_r).cumprod() * CAP
    nifty = closes[BENCH].reindex(cal) / closes[BENCH].reindex(cal).iloc[0] * CAP

    def go(pick_fn):
        eq, traded = run(opens, closes, live, ivs, START, None, NMAX, "high", "monthly", CAP, SLIP, br, sr,
                         pick_fn=pick_fn)
        e = eq["equity"]
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        return eq, {"full": stats(e), "eras": era_stats(e), "yearly": yearly(e),
                    "turnover_per_year": traded / float(e.mean()) / yrs,
                    "avg_held": float(eq["n_held"].mean()),
                    "cash_share_avg": float(1 - (eq["held"] / eq["equity"]).mean()),
                    "crashes": {c: window_dd(e, a, b) for c, (a, b) in CRASHES.items()}}

    rep = {"period": f"{cal[0].date()} -> {cal[-1].date()}", "slip": SLIP,
           "benchmarks": {"ew50_costfree": {"full": stats(ew), "eras": era_stats(ew), "yearly": yearly(ew),
                                            "crashes": {c: window_dd(ew, a, b) for c, (a, b) in CRASHES.items()}},
                          "nifty50": {"full": stats(nifty), "eras": era_stats(nifty), "yearly": yearly(nifty),
                                      "crashes": {c: window_dd(nifty, a, b) for c, (a, b) in CRASHES.items()}}}}
    curves = {"ew50": ew, "nifty50": nifty}

    all_eq, rep["all_members_engine"] = go(lambda prev, pool, held: sorted(pool))
    curves["all_members"] = all_eq["equity"]
    variants = {"ytd_reentry": (ytd, True, True), "ytd_no_reentry": (ytd, False, True),
                "roll222": (roll, True, False)}
    counts_by = {}
    for name, (chg, re_, jan) in variants.items():
        counts: dict = {}
        eq, rep[name] = go(filter_picker(chg, re_, jan, counts))
        counts_by[name] = counts
        curves[name] = eq["equity"]
        print(name, rep[name]["full"], f"held {rep[name]['avg_held']:.1f}", flush=True)
        rnd = [go(random_picker(counts, np.random.default_rng(k)))[1] for k in range(SEEDS)]
        rc = [r["full"]["cagr"] for r in rnd]
        rep[name]["random_same_size"] = {
            "cagr_p05": float(np.percentile(rc, 5)), "cagr_median": float(np.median(rc)),
            "cagr_p95": float(np.percentile(rc, 95)),
            "maxdd_median": float(np.median([r["full"]["max_dd"] for r in rnd])),
            "sharpe_median": float(np.median([r["full"]["sharpe"] for r in rnd])),
            "eras_cagr_median": {n: float(np.median([r["eras"][n]["cagr"] for r in rnd])) for n in ERAS}}
        print("  random", rep[name]["random_same_size"], flush=True)

    out = HERE / "ratio_ytd"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    pd.DataFrame({k: pd.Series(v) for k, v in counts_by.items()}).to_csv(out / "book_size.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
