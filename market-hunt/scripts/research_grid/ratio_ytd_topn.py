"""Ratio filter + ranking (user, 2026-10-18): same rules as ratio_ytd.py / ratio_ytd_staggered.py, but instead of
holding EVERY name whose ratio is above its anchor, hold only the top 50% of them (ceil) by ratio % change.

Variants re-run:
  * ytd_reentry     — Jan-Dec year, Jan all-in, later months: positives may come back;
  * roll222         — ratio change over the trailing 222 sessions > 0, no year reset;
  * sleeves jan/apr/jul/oct — no re-entry until the sleeve's year restarts (jan sleeve == single ytd_no_reentry);
    combined = 25% each, never rebalanced across sleeves.
The year-start month stays all-in (every ratio is 0% vs a fresh anchor, nothing to rank).
"Dropped" (no re-entry) means went NEGATIVE; a positive name that ranks outside the top half is just not held this
month and can be picked again.

Control (tests the ranking itself): random half drawn from the SAME eligible (positive) pool, 20 seeds.
Book mechanics / costs / membership: identical to ratio_ytd.py.

  .venv/Scripts/python.exe scripts/research_grid/ratio_ytd_topn.py
"""
from __future__ import annotations

import json
import math
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
from meanlong_slope import ERAS, START, load_ohlc, stats  # noqa: E402
from membership import members_as_of  # noqa: E402
from ratio_ytd import NMAX  # noqa: E402
from ratio_ytd_staggered import fiscal_year, fy_change, summary  # noqa: E402
from rotation_roc import run  # noqa: E402

SLIP, CAP, SEEDS, ROLL, FRAC = 0.0005, 10_000_000.0, 20, 222, 0.5


def picker(chg: pd.DataFrame, m: int | None, reentry: bool, rng=None, counts: dict | None = None):
    """m = month the year starts (None: no reset, roll222). Eligible = positive (and not dropped this year when
    reentry=False); hold the top ceil(FRAC * eligible) by chg — or a random draw of that size when rng is given."""
    dropped: dict[int, set] = {}
    start_signal_month = None if m is None else (12 if m == 1 else m - 1)

    def pick(prev, pool, held):
        if start_signal_month is not None and prev.month == start_signal_month:
            out = sorted(pool)
        else:
            row = chg.loc[prev] if prev in chg.index else pd.Series(dtype=float)
            elig = [s for s in pool if row.get(s, np.nan) > 0]
            if not reentry:
                gone = dropped.setdefault(int(fiscal_year(pd.DatetimeIndex([prev]), m)[0]), set())
                gone |= {s for s in pool if s not in elig}
                elig = [s for s in elig if s not in gone]
            k = math.ceil(FRAC * len(elig))
            if rng is not None:                       # random control: eligible holdings kept first (min turnover)
                keep = [s for s in sorted(held) if s in elig]
                if len(keep) >= k:
                    out = sorted(rng.choice(keep, size=k, replace=False)) if k else []
                else:
                    cand = [s for s in sorted(elig) if s not in keep]
                    out = keep + list(rng.choice(cand, size=k - len(keep), replace=False))
            else:
                out = sorted(elig, key=lambda s: (-row[s], s))[:k]
        if counts is not None:
            counts[prev] = len(out)
        return out
    return pick


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
    roll = ratio.apply(lambda c: c / c.dropna().shift(ROLL).reindex(c.index) - 1)
    br, sr = charge_rates()
    cal = closes.index[closes.index >= pd.Timestamp(START)]

    rets = closes[cols].pct_change(fill_method=None)
    ew_r = pd.Series([rets.loc[d, [s for s in members_as_of(ivs, d.date()) if s in rets.columns]].mean()
                      for d in cal], index=cal).fillna(0)
    ew = (1 + ew_r).cumprod() * CAP

    def go(pick_fn, cap):
        return run(opens, closes, closes, ivs, START, None, NMAX, "high", "monthly", cap, SLIP, br, sr,
                   pick_fn=pick_fn)

    def rnd_band(curves):
        r = [summary(c) for c in curves]
        return {"cagr": {q: float(np.percentile([x["full"]["cagr"] for x in r], p)) for q, p in
                         (("p05", 5), ("median", 50), ("p95", 95))},
                "maxdd_median": float(np.median([x["full"]["max_dd"] for x in r])),
                "sharpe_median": float(np.median([x["full"]["sharpe"] for x in r])),
                "eras_cagr_median": {n: float(np.median([x["eras"][n]["cagr"] for x in r])) for n in ERAS}}

    rep = {"period": f"{cal[0].date()} -> {cal[-1].date()}", "frac": FRAC, "ew50_costfree": summary(ew)}
    curves, sizes = {"ew50": ew}, {}
    specs = {"ytd_reentry": (fy_change(ratio, 1), 1, True, CAP),
             "roll222": (roll, None, True, CAP),
             **{f"sleeve_{n}": (fy_change(ratio, m), m, False, CAP / 4)
                for n, m in {"jan": 1, "apr": 4, "jul": 7, "oct": 10}.items()}}
    combined, rnd_comb, traded_comb = 0.0, [0.0] * SEEDS, 0.0
    for name, (chg, m, re_, cap) in specs.items():
        counts: dict = {}
        eq, traded = go(picker(chg, m, re_, counts=counts), cap)
        rep[name] = summary(eq, traded)
        rnd = [go(picker(chg, m, re_, rng=np.random.default_rng(k)), cap)[0]["equity"] for k in range(SEEDS)]
        rep[name]["random_from_positive_pool"] = rnd_band(rnd)
        curves[name], sizes[name] = eq["equity"], pd.Series(counts)
        if name.startswith("sleeve_"):
            combined = combined + eq["equity"]
            traded_comb += traded
            rnd_comb = [a + r for a, r in zip(rnd_comb, rnd)]
        print(name, rep[name]["full"], f"held {rep[name]['avg_held']:.1f}",
              rep[name]["random_from_positive_pool"]["cagr"], flush=True)
    rep["combined"] = summary(combined)
    yrs = (combined.index[-1] - combined.index[0]).days / 365.25
    rep["combined"]["turnover_per_year"] = traded_comb / float(combined.mean()) / yrs
    rep["combined"]["random_from_positive_pool"] = rnd_band(rnd_comb)
    curves["combined"] = combined
    print("combined", rep["combined"]["full"], rep["combined"]["random_from_positive_pool"]["cagr"], flush=True)

    out = HERE / "ratio_ytd_topn"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    pd.DataFrame(sizes).to_csv(out / "book_size.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
