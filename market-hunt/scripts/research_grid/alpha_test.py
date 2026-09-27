"""The live 'Alpha' strategy re-tested in the research harness (honest fills, full costs).

Alpha (strategy_1781939795919): eligible if truevx_score>90 & mean_short>80 & mean_mid>60 &
mean_long>50; rank eligible by 20_day_return (close / close 19 sessions earlier, highest first);
hold the top N equal-weight; an under-filled book splits equity across whatever qualifies.

Differences from the market-hunt engine (all deliberate — they are the engine's known defects):
signal at the PREVIOUS close and fill at the next open (engine signals and fills on the same
close), sell-side charges + slippage applied, lookbacks warmed up before the start date.

Controls per cell: equal-weight PIT universe buy & hold (cost-free), Nifty 50 buy & hold, and
Alpha with a RANDOM ranking inside the same eligible pool (10 seeds) -> how much the ranker adds.
Also the NIFTY50 22d-reversal book (lowest ROC, buffer 30) for a head-to-head.

  .venv/Scripts/python.exe scripts/research_grid/alpha_test.py [--cells all|n50]
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid_meanrev import BENCH, LOAD_FROM, WINDOWS, charge_rates, ew_index, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run, stats  # noqa: E402

CELLS = [  # (label, index, freq, n)
    ("N50_monthly_20", "NIFTY50", "monthly", 20),
    ("N50_monthly_7", "NIFTY50", "monthly", 7),
    ("N200_monthly_7 (live LargeCaps)", "NIFTY200", "monthly", 7),
    ("MID150_monthly_20", "NIFTYMIDCAP150", "monthly", 20),
    ("N500_monthly_20", "NIFTY500", "monthly", 20),
    ("N750_quarterly_20 (live Alpha-Market)", "NIFTY500MICRO750", "quarterly", 20),
]
RULES = {"truevx_score": 90, "mean_short": 80, "mean_mid": 60, "mean_long": 50}
SLIP, CAP, SEEDS = 0.0005, 10_000_000, 10


def load_means(db, symbols) -> dict[str, pd.DataFrame]:
    docs = db.indicators.find(
        {"symbol": {"$in": symbols}, "indicator_type": "truevx", "base_symbol": BENCH,
         "date": {"$gte": LOAD_FROM}},
        {"_id": 0, "symbol": 1, "date": 1, "data.mean_short": 1, "data.mean_mid": 1, "data.mean_long": 1})
    df = pd.DataFrame([{"symbol": d["symbol"], "date": d["date"], **(d.get("data") or {})} for d in docs])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.drop_duplicates(["symbol", "date"])
    return {f: df.pivot(index="date", columns="symbol", values=f).sort_index()
            for f in ["mean_short", "mean_mid", "mean_long"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", default="all", help="all | n50 (only the NIFTY50 cells)")
    args = ap.parse_args()
    cells = [c for c in CELLS if args.cells == "all" or c[1] == "NIFTY50"]
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    br, sr = charge_rates()
    out_dir = Path(__file__).with_name("alpha_test")
    out_dir.mkdir(exist_ok=True)
    report, cache = {}, {}

    for label, index, freq, n in cells:
        if index not in cache:
            iv = load_membership(db, index, date.today())
            ever = sorted(union_over(iv, WINDOWS["full"][0], date.today()))
            opens, closes, score = load_data(db, ever)
            means = load_means(db, ever)
            fields = {"truevx_score": score, **means}
            idx, cols = closes.index, [c for c in closes.columns if c != BENCH]
            elig = pd.DataFrame(True, index=idx, columns=cols)
            for f, thr in RULES.items():
                elig &= (fields[f].reindex(index=idx, columns=cols) > thr).fillna(False)
            ret20 = closes[cols].apply(lambda c: c / c.dropna().shift(19).reindex(c.index) - 1)
            cache[index] = (iv, ever, opens, closes, elig, ret20, ew_index(closes, iv))
        iv, ever, opens, closes, elig, ret20, ew = cache[index]

        cell = {"index": index, "freq": freq, "n": n, "ever_members": len(ever),
                "missing_prices": [s for s in ever if s not in closes.columns], "windows": {}}
        for w, (s, e) in WINDOWS.items():
            eq, traded = run(opens, closes, ret20, iv, s, e, n, "alpha", freq, CAP, SLIP, br, sr,
                             eligible=elig)
            yrs = (eq.index[-1] - eq.index[0]).days / 365.25
            rnd = [stats(run(opens, closes, ret20, iv, s, e, n, "alpha", freq, CAP, SLIP, br, sr,
                             eligible=elig, rng=np.random.default_rng(k))[0]["equity"]) for k in range(SEEDS)]
            c = np.array([r["cagr"] for r in rnd])
            win = eq.index
            elig_n = elig.reindex(win).sum(axis=1)
            cell["windows"][w] = {
                "period": f"{win[0].date()} -> {win[-1].date()}",
                **stats(eq["equity"]), "turnover_pa": traded / eq["equity"].mean() / yrs,
                "avg_names": float(eq["n_held"].mean()), "min_names": int(eq["n_held"].min()),
                "avg_eligible": float(elig_n.mean()),
                "random_rank_cagr_mean": float(c.mean()), "random_rank_cagr_p05": float(np.percentile(c, 5)),
                "random_rank_cagr_p95": float(np.percentile(c, 95)),
                "ew_universe_bh": stats(ew.reindex(win).ffill())["cagr"],
                "nifty50_bh": stats(closes[BENCH].reindex(win).ffill())["cagr"],
            }
            if w == "full":
                eq.to_csv(out_dir / f"{index}_{freq}_n{n}_equity.csv")
        report[label] = cell
        print(label, json.dumps({k: round(v["cagr"] * 100, 1) for k, v in cell["windows"].items()}), flush=True)

    (out_dir / f"report_{args.cells}.json").write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
