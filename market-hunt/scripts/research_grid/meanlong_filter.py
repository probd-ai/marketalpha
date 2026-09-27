"""NIFTY50 22d-loser book (buffer 30) restricted to mean_long > 50, point-in-time.

Variants:
  base      : no filter (the reference book)
  a_exit    : only mean_long>50 names can be bought; a holding that drops to <=50 is sold
  b_entry   : only mean_long>50 names can be bought; holdings stay under the normal buffer rule
Each variant has a random-ranking control with the same filter/mechanics (20 seeds).

  .venv/Scripts/python.exe scripts/research_grid/meanlong_filter.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid_meanrev import BENCH, WINDOWS, charge_rates, ew_index, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run, stats  # noqa: E402

LB, N, BUF, SLIP, CAP, SEEDS, THR = 22, 20, 30, 0.0005, 10_000_000, 20, 50
VARIANTS = {"base": {}, "a_exit": {"elig_exit": True}, "b_entry": {"elig_exit": False}}


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, WINDOWS["full"][0], date.today()))
    opens, closes, ml = load_data(db, ever, field="mean_long")
    cols = [c for c in closes.columns if c != BENCH]
    roc = closes[cols].apply(lambda c: c / c.dropna().shift(LB).reindex(c.index) - 1)
    elig = (ml.reindex(index=closes.index, columns=cols) > THR).fillna(False)
    ew = ew_index(closes, iv)
    br, sr = charge_rates()
    out = Path(__file__).with_name("meanlong_filter")
    out.mkdir(exist_ok=True)

    report = {}
    for v, kw in VARIANTS.items():
        extra = {} if v == "base" else {"eligible": elig, **kw}
        report[v] = {}
        for w, (s, e) in WINDOWS.items():
            eq, traded = run(opens, closes, roc, iv, s, e, N, "low", "monthly", CAP, SLIP, br, sr,
                             buffer=BUF, **extra)
            yrs = (eq.index[-1] - eq.index[0]).days / 365.25
            rnd = [stats(run(opens, closes, roc, iv, s, e, N, "random", "monthly", CAP, SLIP, br, sr,
                             rng=np.random.default_rng(k), buffer=BUF, **extra)[0]["equity"])
                   for k in range(SEEDS)]
            c = np.array([r["cagr"] for r in rnd])
            report[v][w] = {**stats(eq["equity"]), "turnover_pa": traded / eq["equity"].mean() / yrs,
                            "avg_names": float(eq["n_held"].mean()), "min_names": int(eq["n_held"].min()),
                            "random_cagr_mean": float(c.mean()), "random_cagr_p05": float(np.percentile(c, 5)),
                            "random_cagr_p95": float(np.percentile(c, 95)),
                            "random_sharpe_mean": float(np.mean([r["sharpe"] for r in rnd])),
                            "ew50_bh": stats(ew.reindex(eq.index).ffill())["cagr"]}
            if w == "full":
                eq.to_csv(out / f"{v}_equity.csv")
    # how many NIFTY50 members pass mean_long>50 on a typical rebalance day
    from membership import members_as_of
    firsts = pd.Series(closes.index, index=closes.index).groupby(closes.index.to_period("M")).first()
    counts = [int(sum(bool(elig.at[d, s]) for s in members_as_of(iv, d.date()) if s in elig.columns))
              for d in firsts if d >= pd.Timestamp(WINDOWS["full"][0])]
    report["eligible_per_rebalance"] = {"mean": float(np.mean(counts)), "min": int(min(counts)),
                                        "max": int(max(counts)),
                                        "share_below_20": float(np.mean(np.array(counts) < N))}
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
