"""Four staggered ratio-YTD sleeves on point-in-time NIFTY 50 (user, 2026-10-18) — extends ratio_ytd.py.

Each sleeve = the ytd_no_reentry rule with its own 12-month "year":
  Jan-Dec, Apr-Mar, Jul-Jun, Oct-Sep.
  * first month of the sleeve's year: every member held (no filter);
  * every later month (first session, signal on the previous close): keep members whose ratio
    (close / Nifty 50 close) is above its anchor = last close before the year started; a name that fails
    once stays out until the sleeve's next year start.
Book mechanics, costs, membership, delisted prices: identical to ratio_ytd.py (rotation_roc.run, monthly).

Combined book: 25% of capital per sleeve on day one, sleeves never rebalanced against each other (sum of the four
equity curves). Controls: per-sleeve random books of the same size each month (20 seeds; seed k of every sleeve
summed into combined random book k), all-members engine book, cost-free EW50, Nifty 50.

  .venv/Scripts/python.exe scripts/research_grid/ratio_ytd_staggered.py
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
from ratio_ytd import NMAX, era_stats, random_picker, yearly  # noqa: E402
from rotation_roc import run  # noqa: E402

SLIP, CAP, SEEDS = 0.0005, 10_000_000.0, 20
SLEEVES = {"jan": 1, "apr": 4, "jul": 7, "oct": 10}       # name -> month the sleeve's year starts


def fiscal_year(idx: pd.DatetimeIndex, m: int) -> np.ndarray:
    """Year label of a 12-month year starting in month m (label = calendar year it starts in)."""
    return np.where(idx.month >= m, idx.year, idx.year - 1)


def fy_change(ratio: pd.DataFrame, m: int) -> pd.DataFrame:
    """Ratio % change from the anchor = last known ratio before the year started (first ratio of the year if the
    stock had none then)."""
    fy = fiscal_year(ratio.index, m)
    prior_last = ratio.ffill().groupby(fy).last()
    anchor = pd.DataFrame(index=ratio.index, columns=ratio.columns, dtype=float)
    for y in np.unique(fy):
        rows = fy == y
        a = prior_last.loc[y - 1] if (y - 1) in prior_last.index else pd.Series(np.nan, index=ratio.columns)
        first = ratio[rows].bfill().iloc[0]
        anchor.loc[rows] = np.tile(a.fillna(first).to_numpy(), (rows.sum(), 1))
    return ratio / anchor - 1


def sleeve_picker(chg: pd.DataFrame, m: int, counts: dict):
    """No re-entry: dropped once -> out until the next year start. Signal close in month m-1 = year-start rebalance."""
    dropped: dict[int, set] = {}
    start_signal_month = 12 if m == 1 else m - 1

    def pick(prev, pool, held):
        if prev.month == start_signal_month:
            out = sorted(pool)
        else:
            row = chg.loc[prev] if prev in chg.index else pd.Series(dtype=float)
            gone = dropped.setdefault(int(fiscal_year(pd.DatetimeIndex([prev]), m)[0]), set())
            gone |= {s for s in pool if not row.get(s, np.nan) > 0}
            out = sorted(s for s in pool if s not in gone)
        counts[prev] = len(out)
        return out
    return pick


def summary(eq: pd.DataFrame | pd.Series, traded: float | None = None) -> dict:
    e = eq["equity"] if isinstance(eq, pd.DataFrame) else eq
    out = {"full": stats(e), "eras": era_stats(e), "yearly": yearly(e),
           "crashes": {c: window_dd(e, a, b) for c, (a, b) in CRASHES.items()}}
    if isinstance(eq, pd.DataFrame):
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        out |= {"turnover_per_year": traded / float(e.mean()) / yrs, "avg_held": float(eq["n_held"].mean())}
    return out


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    ivs = build_intervals(read_mcwb(), START, date.today())
    syms = sorted({iv.symbol for iv in ivs})
    ohlc = load_ohlc(db, syms)
    closes = pd.DataFrame({s: g.set_index("date")["close_price"] for s, g in ohlc.items()}).sort_index()
    opens = pd.DataFrame({s: g.set_index("date")["open_price"] for s, g in ohlc.items()}).sort_index()
    closes = closes[closes[BENCH].notna()]
    opens = opens.reindex(closes.index)
    cols = [c for c in closes.columns if c != BENCH]
    ratio = closes[cols].div(closes[BENCH], axis=0)
    br, sr = charge_rates()
    cal = closes.index[closes.index >= pd.Timestamp(START)]

    rets = closes[cols].pct_change(fill_method=None)
    ew_r = pd.Series([rets.loc[d, [s for s in members_as_of(ivs, d.date()) if s in rets.columns]].mean()
                      for d in cal], index=cal).fillna(0)
    ew = (1 + ew_r).cumprod() * CAP
    nifty = closes[BENCH].reindex(cal) / closes[BENCH].reindex(cal).iloc[0] * CAP

    def go(pick_fn, cap):
        return run(opens, closes, closes, ivs, START, None, NMAX, "high", "monthly", cap, SLIP, br, sr,
                   pick_fn=pick_fn)

    all_eq, all_tr = go(lambda prev, pool, held: sorted(pool), CAP)
    rep = {"period": f"{cal[0].date()} -> {cal[-1].date()}", "sleeves": {},
           "benchmarks": {"ew50_costfree": summary(ew), "nifty50": summary(nifty),
                          "all_members_engine": summary(all_eq, all_tr)}}
    curves = {"ew50": ew, "nifty50": nifty, "all_members": all_eq["equity"]}
    sizes, rnd_sum, traded_sum = {}, [0.0] * SEEDS, 0.0
    combined = 0.0
    for name, m in SLEEVES.items():
        counts: dict = {}
        eq, traded = go(sleeve_picker(fy_change(ratio, m), m, counts), CAP / 4)
        traded_sum += traded
        rep["sleeves"][name] = summary(eq, traded)
        rnd = [go(random_picker(counts, np.random.default_rng(k)), CAP / 4)[0]["equity"] for k in range(SEEDS)]
        rc = [stats(r)["cagr"] for r in rnd]
        rep["sleeves"][name]["random_same_size"] = {"cagr_p05": float(np.percentile(rc, 5)),
                                                    "cagr_median": float(np.median(rc)),
                                                    "cagr_p95": float(np.percentile(rc, 95))}
        rnd_sum = [a + r for a, r in zip(rnd_sum, rnd)]
        combined = combined + eq["equity"]
        curves[f"sleeve_{name}"] = eq["equity"]
        sizes[name] = pd.Series(counts)
        print(name, rep["sleeves"][name]["full"], f"held {rep['sleeves'][name]['avg_held']:.1f}",
              rep["sleeves"][name]["random_same_size"], flush=True)

    rep["combined"] = summary(combined)
    yrs = (combined.index[-1] - combined.index[0]).days / 365.25
    rep["combined"]["turnover_per_year"] = traded_sum / float(combined.mean()) / yrs
    rc = [summary(r) for r in rnd_sum]
    rep["combined_random_same_size"] = {
        f: {"p05": float(np.percentile([r["full"][f] for r in rc], 5)),
            "median": float(np.median([r["full"][f] for r in rc])),
            "p95": float(np.percentile([r["full"][f] for r in rc], 95))} for f in ("cagr", "max_dd", "sharpe")}
    rep["combined_random_same_size"]["eras_cagr_median"] = {
        n: float(np.median([r["eras"][n]["cagr"] for r in rc])) for n in ERAS}
    curves["combined"] = combined
    print("combined", rep["combined"]["full"], rep["combined_random_same_size"], flush=True)

    out = HERE / "ratio_ytd_staggered"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    pd.DataFrame(sizes).to_csv(out / "book_size.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
