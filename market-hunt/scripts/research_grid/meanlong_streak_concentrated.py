"""Concentration test for the below-30 streak book: does fewer seats (or deploying all cash) raise the return on
capital, and at what drawdown cost?

Variants (entry = up-cross that happened with mean_long < 30; exit = mean_long below its 222-SMA or index exit;
refill = shortest streak), daily, point-in-time NIFTY 50, Jan 2008 -> latest, statutory charges + 5 bp:
  seats5 / seats6  fixed seats of equity/5 or equity/6, empty seats in cash
  full10           up to 10 names, ALL cash deployed across whatever qualifies (rotation_roc default pick_fn sizing)
  seats10          the earlier below_30 book (fixed 10 seats), for reference
Each with 10 random books from the same filtered pool.

  .venv/Scripts/python.exe scripts/research_grid/meanlong_streak_concentrated.py
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
from meanlong_slope import ERAS, MA, START, load_ohlc, mean_long_series, stats  # noqa: E402
from meanlong_streak import streaks  # noqa: E402
from meanlong_streak_levels import level_at_cross  # noqa: E402
from membership import members_as_of  # noqa: E402
from rotation_roc import run  # noqa: E402

SLIP, CAP, SEEDS, LEVEL = 0.0005, 10_000_000.0, 10, 30
VARIANTS = {"seats5": (5, True), "seats6": (6, True), "full10": (10, False), "seats10": (10, True)}


def picker(score, n, order, rng=None):
    def pick(prev, pool, held):
        keep = sorted(s for s in held if s in pool)
        cand = [s for s in pool if s not in keep]
        cand = list(rng.permutation(sorted(cand))) if order == "random" else \
            sorted(cand, key=lambda s: (score.at[prev, s], s))
        return keep + cand[: max(0, n - len(keep))]
    return pick


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    ivs = build_intervals(read_mcwb(), START, date.today())
    syms = sorted({iv.symbol for iv in ivs})
    ohlc = load_ohlc(db, syms)
    ml = mean_long_series(ohlc, syms)
    closes = pd.DataFrame({s: g.set_index("date")["close_price"] for s, g in ohlc.items()}).sort_index()
    opens = pd.DataFrame({s: g.set_index("date")["open_price"] for s, g in ohlc.items()}).sort_index()
    ml = ml.reindex(closes.index)
    ma = ml.apply(lambda c: c.dropna().rolling(MA).mean().reindex(c.index))
    streak = streaks(ml, ma)
    score = streak.where(level_at_cross(ml, streak) < LEVEL)
    br, sr = charge_rates()

    def era(eq):
        out = {"full": stats(eq)}
        out.update({n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()})
        return out

    def go(n, fixed, order, rng=None):
        return run(opens, closes, score, ivs, START, None, n, "high", "daily", CAP, SLIP, br, sr,
                   pick_fn=picker(score, n, order, rng), fixed_seats=fixed)

    rep, curves = {"level": f"cross below {LEVEL}"}, {}
    for name, (n, fixed) in VARIANTS.items():
        eq, traded = go(n, fixed, "shortest")
        e = eq["equity"]
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        rnd = [era(go(n, fixed, "random", np.random.default_rng(k))[0]["equity"]) for k in range(SEEDS)]
        rep[name] = {"book": era(e), "invested": float((eq["held"] / eq["equity"]).mean()),
                     "avg_names": float(eq["n_held"].mean()), "turnover_per_year": traded / float(e.mean()) / yrs,
                     "random_full": {q: float(np.percentile([r["full"]["cagr"] for r in rnd], p))
                                     for q, p in (("p5", 5), ("median", 50), ("p95", 95))},
                     "crashes": {c: window_dd(e, a, b) for c, (a, b) in CRASHES.items()}}
        curves[name] = e
        print(name, f"{stats(e)['cagr']:.4f} dd {stats(e)['max_dd']:.3f}", flush=True)
    out = HERE / "meanlong_streak_concentrated"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
