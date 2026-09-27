"""Reconcile the honest harness with the market-hunt engine on the live LargeCaps config.

Adds the engine's habits to the harness one at a time and compares with the engine's own saved runs:
  0 honest          : signal at previous close, next-open fills, buy+sell charges, 5 bp slippage
  1 same-close      : rank on the rebalance day's close and fill at that close
  2 + no sell costs : ... and sells free, zero slippage (engine bugs #1 / portfolio slippage_bps=0)
  3 + alpha day-1   : ... and day-one ties broken alphabetically (engine no-warm-up bug #3)
Engine reference numbers are read from portfolio_runs (read-only; nothing is run through the API,
because a portal run becomes the newest cached run that live reconciliation reads).

  .venv/Scripts/python.exe scripts/research_grid/reconcile_engine.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from alpha_test import RULES, load_means  # noqa: E402
from grid_meanrev import BENCH, charge_rates, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402

PID, CAP, N = "portfolio_1782072903878", 1_700_000, 7
WINDOWS = {"2022-01 -> 2024-01": ("2022-01-01", "2024-01-01", "2024-01-01"),
           "2022-01 -> 2026-09": ("2022-01-01", "2026-09-25", "latest:2026-09-25"),
           "2026-01 -> 2026-09 (inception)": ("2026-01-01", "2026-09-25", "latest:2026-09-25")}
VARIANTS = {"0 honest": dict(slip=0.0005, sell=True, same_close=False, alpha_day1=False),
            "1 same-close fills": dict(slip=0.0005, sell=True, same_close=True, alpha_day1=False),
            "2 + no sell cost, 0 slip": dict(slip=0.0, sell=False, same_close=True, alpha_day1=False),
            "3 + alphabetical day-1 (= engine)": dict(slip=0.0, sell=False, same_close=True, alpha_day1=True)}


def summarize(s: pd.Series) -> dict:
    return {"total_return": float(s.iloc[-1] / s.iloc[0] - 1), "max_dd": float((s / s.cummax() - 1).min())}


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY200", date.today())
    ever = sorted(union_over(iv, date(2021, 10, 1), date.today()))
    opens, closes, score = load_data(db, ever)
    means = load_means(db, ever)
    fields = {"truevx_score": score, **means}
    cols = [c for c in closes.columns if c != BENCH]
    elig = pd.DataFrame(True, index=closes.index, columns=cols)
    for f, thr in RULES.items():
        elig &= (fields[f].reindex(index=closes.index, columns=cols) > thr).fillna(False)
    ret20 = closes[cols].apply(lambda c: c / c.dropna().shift(19).reindex(c.index) - 1)
    br, sr = charge_rates()

    rep = {}
    for w, (a, b, end_key) in WINDOWS.items():
        doc = db.portfolio_runs.find_one({"portfolio_id": PID, "inception": a, "end_key": end_key},
                                         {"_id": 0, "result.summary": 1, "created_at": 1},
                                         sort=[("created_at", -1)])
        rep[w] = {"engine": ({"total_return": doc["result"]["summary"]["total_return"] / 100,
                              "max_dd": doc["result"]["summary"]["max_drawdown"] / 100} if doc else None)}
        for v, k in VARIANTS.items():
            curve = None
            for freq in ["monthly", "monthly_mid"]:
                eq, _ = run(opens, closes, ret20, iv, date.fromisoformat(a), date.fromisoformat(b), N, "alpha",
                            freq, CAP / 2, k["slip"], br, sr if k["sell"] else 0.0, eligible=elig,
                            same_close=k["same_close"], alpha_day1=k["alpha_day1"])
                curve = eq["equity"] if curve is None else curve + eq["equity"]
            rep[w][v] = summarize(curve)
    (HERE / "reconcile_engine.json").write_text(json.dumps(rep, indent=2, default=str))
    for w, x in rep.items():
        print(f"== {w}")
        for k, m in x.items():
            print(f"   {k:36s} " + ("-" if m is None else f"total {m['total_return'] * 100:7.1f}%  maxDD {m['max_dd'] * 100:6.1f}%"))


if __name__ == "__main__":
    main()
