"""Honest re-test of the live LargeCaps portfolio (portfolio_1782072903878).

Config mirrored from the portfolio: Alpha filter (truevx>90 & mean_short>80 & mean_mid>60 & mean_long>50),
NIFTY200 point-in-time, top 7 by 20_day_return (close / close 19 sessions earlier), equal weight, two
50% sleeves — "first" (first session of the month) and "mid" (first session on/after the 15th).
Harness differences vs the market-hunt engine: signal at the previous close, next-open fills, sell-side
charges and 5 bp slippage both ways, indicator/price warm-up before the start.

Windows: Oct 2020 -> Sep 2026 (P1/P2 halves) and Jan 2026 -> Sep 2026 (the portfolio's inception, to
compare with the engine's +18.8%). Controls: the same book with a RANDOM ranking inside the Alpha-eligible
pool (10 seeds), equal-weight PIT NIFTY200 buy & hold (cost-free), Nifty 50.

  .venv/Scripts/python.exe scripts/research_grid/alpha_n200.py
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
from alpha_test import RULES, load_means  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run, stats  # noqa: E402

INDEX, N, SLIP, CAP, SEEDS = "NIFTY200", 7, 0.0005, 1_700_000, 10
WINDOWS = {"full": (date(2020, 10, 1), None), "P1": (date(2020, 10, 1), date(2023, 6, 30)),
           "P2": (date(2023, 7, 1), None), "2026 YTD (inception)": (date(2026, 1, 1), None)}


def book(opens, closes, ret20, iv, elig, s, e, br, sr, slip, rng_seed=None):
    """Two 50% sleeves (first + mid) summed into one equity curve."""
    parts, traded, names = [], 0.0, []
    for k, freq in enumerate(["monthly", "monthly_mid"]):
        rng = None if rng_seed is None else np.random.default_rng(rng_seed * 10 + k)
        eq, t = run(opens, closes, ret20, iv, s, e, N, "alpha", freq, CAP / 2, slip, br, sr,
                    eligible=elig, rng=rng)
        parts.append(eq["equity"])
        names.append(eq["n_held"])
        traded += t
    return parts[0] + parts[1], traded, names[0] + names[1]


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, INDEX, date.today())
    ever = sorted(union_over(iv, WINDOWS["full"][0], date.today()))
    opens, closes, score = load_data(db, ever)
    means = load_means(db, ever)
    fields = {"truevx_score": score, **means}
    cols = [c for c in closes.columns if c != BENCH]
    elig = pd.DataFrame(True, index=closes.index, columns=cols)
    for f, thr in RULES.items():
        elig &= (fields[f].reindex(index=closes.index, columns=cols) > thr).fillna(False)
    ret20 = closes[cols].apply(lambda c: c / c.dropna().shift(19).reindex(c.index) - 1)
    ew = ew_index(closes, iv)
    br, sr = charge_rates()

    rep = {"config": {"index": INDEX, "n_per_sleeve": N, "slip_bps": SLIP * 1e4, "capital": CAP,
                      "ever_members": len(ever), "missing_prices": [s for s in ever if s not in closes.columns]},
           "windows": {}}
    for w, (s, e) in WINDOWS.items():
        eq, traded, nh = book(opens, closes, ret20, iv, elig, s, e, br, sr, SLIP)
        eq0, _, _ = book(opens, closes, ret20, iv, elig, s, e, br, sr, 0.0)
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        rnd = [stats(book(opens, closes, ret20, iv, elig, s, e, br, sr, SLIP, rng_seed=k)[0]) for k in range(SEEDS)]
        c = np.array([r["cagr"] for r in rnd])
        tr = np.array([r["total_return"] for r in rnd])
        e50 = ew.reindex(eq.index).ffill()
        rep["windows"][w] = {
            "period": f"{eq.index[0].date()} -> {eq.index[-1].date()}",
            "book": {**stats(eq), "turnover_pa": traded / eq.mean() / yrs, "avg_names": float(nh.mean())},
            "book_zero_slippage": stats(eq0),
            "random_rank": {"cagr_mean": float(c.mean()), "cagr_p05": float(np.percentile(c, 5)),
                            "cagr_p95": float(np.percentile(c, 95)), "total_return_mean": float(tr.mean())},
            "ew_nifty200_bh": stats(e50), "nifty50_bh": stats(closes[BENCH].reindex(eq.index).ffill()),
        }
        if w == "full":
            pd.DataFrame({"book": eq, "ew200": CAP * e50 / e50.iloc[0],
                          "nifty50": CAP * closes[BENCH].reindex(eq.index).ffill() / closes[BENCH].reindex(eq.index).ffill().iloc[0]}) \
                .to_csv(HERE / "alpha_n200_equity.csv")
    (HERE / "alpha_n200_report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
