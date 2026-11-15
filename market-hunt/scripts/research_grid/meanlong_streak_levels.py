"""Streak book with an entry LEVEL filter: buy only stocks whose current up-cross (mean_long crossing above its own
222-session SMA) happened while mean_long was BELOW X (or ABOVE X with --side above) — "just turning strong from a low base".

Same daily rule as meanlong_streak.py: 10 seats; sell a holding when mean_long drops below its SMA (or it leaves the
index); refill with the shortest streak among qualifying stocks. Seats are FIXED (rotation_roc fixed_seats): when fewer
than 10 qualify, the empty seat stays in cash instead of concentrating into the rest. X = 30..80 and no filter.
Control per level: 10 random books drawn from that level's filtered pool (same exits, same seats).
Point-in-time NIFTY 50, Jan 2008 -> latest, statutory charges + 5 bp; mean_long recomputed from prices.

  .venv/Scripts/python.exe scripts/research_grid/meanlong_streak_levels.py
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from crash_test import CRASHES, build_intervals, read_mcwb, window_dd  # noqa: E402
from grid_meanrev import BENCH, charge_rates  # noqa: E402
from meanlong_slope import ERAS, MA, START, load_ohlc, mean_long_series, stats  # noqa: E402
from meanlong_streak import daily_picker, streaks  # noqa: E402
from membership import members_as_of  # noqa: E402
from rotation_roc import run  # noqa: E402

N, SLIP, CAP, SEEDS = 10, 0.0005, 10_000_000.0, 10
LEVELS = [30, 40, 50, 60, 70, 80, None]


def level_at_cross(ml: pd.DataFrame, streak: pd.DataFrame) -> pd.DataFrame:
    """mean_long on the first day of the current streak, held for the whole streak (NaN when not above)."""
    first = ml.where(streak == 1)
    return first.ffill().where(streak.notna())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=["below", "above"], default="below",
                    help="buy only crosses that happened with mean_long below / above each level")
    side = ap.parse_args().side
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
    lvl = level_at_cross(ml, streak)
    br, sr = charge_rates()
    cal = closes.index[(closes.index >= pd.Timestamp(START)) & closes[BENCH].notna().to_numpy()]

    def era(eq):
        out = {"full": stats(eq)}
        out.update({n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()})
        return out

    def go(score, order, rng=None, fixed=True):
        return run(opens, closes, score, ivs, START, None, N, "high", "daily", CAP, SLIP, br, sr,
                   pick_fn=daily_picker(score, order, rng), fixed_seats=fixed)

    # regression: without the filter and without fixed seats this is meanlong_streak's daily book exactly
    base_old, _ = go(streak, "shortest", fixed=False)
    prev = pd.read_csv(HERE / "meanlong_streak" / "equity.csv", index_col=0, parse_dates=True)["daily_shortest"]
    gap = float((base_old["equity"] - prev.reindex(base_old.index)).abs().max())
    rep = {"period": f"{cal[0].date()} -> {cal[-1].date()}", "regression_vs_meanlong_streak_max_abs_gap": gap, "levels": {}}
    print("regression gap", gap, flush=True)
    curves = {}
    for x in LEVELS:
        score = streak if x is None else streak.where(lvl < x if side == "below" else lvl > x)
        eq, traded = go(score, "shortest")
        e = eq["equity"]
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        rnd = [era(go(score, "random", np.random.default_rng(k))[0]["equity"]) for k in range(SEEDS)]
        pool = [int(score.loc[d].notna().sum()) for d in cal[::5]]
        key = "none" if x is None else f"{side}_{x}"
        rep["levels"][key] = {
            "book": era(e), "invested": float((eq["held"] / eq["equity"]).mean()),
            "avg_names": float(eq["n_held"].mean()), "turnover_per_year": traded / float(e.mean()) / yrs,
            "pool_median": float(np.median(pool)), "pool_p10": float(np.percentile(pool, 10)),
            "random": {w: {"p5": float(np.percentile([r[w]["cagr"] for r in rnd], 5)),
                           "median": float(np.median([r[w]["cagr"] for r in rnd])),
                           "p95": float(np.percentile([r[w]["cagr"] for r in rnd], 95))} for w in rnd[0]},
            "crashes": {c: window_dd(e, a, b) for c, (a, b) in CRASHES.items()}}
        curves[key] = e
        print(key, f"{stats(e)['cagr']:.4f}", flush=True)
    out = HERE / ("meanlong_streak_levels" if side == "below" else "meanlong_streak_levels_above")
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
