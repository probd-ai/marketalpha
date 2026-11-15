"""Top-10 NIFTY 50 by mean_long "streak": consecutive sessions mean_long has stayed above its own 222-session SMA.

Rule (user, 2026-10-04): fully invested, equal-weight top 10 by current streak (longest first); a stock below its
SMA has no streak and cannot be picked. Point-in-time NIFTY 50, Jan 2008 -> latest, ranked at the previous close and
traded at the open (rotation_roc.run: sells first, equal-weight resize, index exits forced), statutory costs + 5 bp.

Controls: 20 random 10-stock books drawn from the SAME qualifying pool (does the streak ranking add anything beyond
"above its SMA"?), the 10 SHORTEST streaks (is the ranking monotonic?), equal-weight PIT NIFTY 50, Nifty 50 index.
mean_long is recomputed from prices (meanlong_slope.py; matches the stored series exactly).

  .venv/Scripts/python.exe scripts/research_grid/meanlong_streak.py
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
from membership import members_as_of  # noqa: E402
from rotation_roc import run  # noqa: E402

N, SLIP, CAP, SEEDS = 10, 0.0005, 10_000_000.0, 20


def streaks(ml: pd.DataFrame, ma: pd.DataFrame) -> pd.DataFrame:
    """Consecutive sessions (in the stock's own sessions) with mean_long > SMA; NaN when below or not yet live."""
    out = {}
    for s in ml.columns:
        live = ml[s].notna() & ma[s].notna()
        a = (ml[s] > ma[s]).where(live)
        x = a.dropna().astype(int)
        run_id = (x != x.shift()).cumsum()
        k = x.groupby(run_id).cumsum() * x
        out[s] = k.where(k > 0).reindex(ml.index)
    return pd.DataFrame(out)


def daily_picker(score: pd.DataFrame, order: str = "longest", rng=None):
    """Event-driven book (user rule): keep every holding while it stays above its SMA (still in the pool); only a
    vacancy (a holding below its SMA, or out of the index) is refilled — by the longest streak, the shortest, or at
    random from the qualifying pool. Retained names are never trimmed (rotation_roc pick_fn semantics)."""
    def pick(prev, pool, held):
        keep = sorted(s for s in held if s in pool)
        cand = [s for s in pool if s not in keep]
        if order == "random":
            cand = list(rng.permutation(sorted(cand)))
        else:
            cand = sorted(cand, key=lambda s: (score.at[prev, s], s), reverse=(order == "longest"))
        return keep + cand[: max(0, N - len(keep))]
    return pick


def era_stats(eq: pd.Series) -> dict:
    return {n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()}


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
    score = streaks(ml, ma)
    br, sr = charge_rates()
    cal = closes.index[(closes.index >= pd.Timestamp(START)) & closes[BENCH].notna().to_numpy()]

    rets = closes.drop(columns=[BENCH]).pct_change(fill_method=None)
    ew_r = pd.Series([rets.loc[d, [s for s in members_as_of(ivs, d.date()) if s in rets.columns]].mean()
                      for d in cal], index=cal).fillna(0)
    ew = (1 + ew_r).cumprod() * CAP
    nifty = closes[BENCH].reindex(cal) / closes[BENCH].reindex(cal).iloc[0] * CAP

    rep = {"period": f"{cal[0].date()} -> {cal[-1].date()}", "n": N, "ma": MA,
           "benchmarks": {"ew50": {"full": stats(ew), "eras": era_stats(ew)},
                          "nifty50": {"full": stats(nifty), "eras": era_stats(nifty)}}}
    out = HERE / "meanlong_streak"
    out.mkdir(exist_ok=True)
    curves = {"ew50": ew, "nifty50": nifty}
    # daily, event-driven (the user's rule): exit only when a holding drops below its SMA, refill that seat at once
    def go(order, rng=None):
        return run(opens, closes, score, ivs, START, None, N, "high", "daily", CAP, SLIP, br, sr,
                   pick_fn=daily_picker(score, order, rng))
    eq, traded = go("longest")
    lo, _ = go("shortest")
    rnd = [go("random", np.random.default_rng(k))[0]["equity"] for k in range(SEEDS)]
    e = eq["equity"]
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    rc = [stats(r)["cagr"] for r in rnd]
    rep["daily"] = {
        "longest_streak": {"full": stats(e), "eras": era_stats(e), "turnover_per_year": traded / float(e.mean()) / yrs,
                           "avg_held": float(eq["n_held"].mean()),
                           "crashes": {c: window_dd(e, a, b) for c, (a, b) in CRASHES.items()}},
        "shortest_streak": {"full": stats(lo["equity"]), "eras": era_stats(lo["equity"])},
        "random_fill": {"cagr_mean": float(np.mean(rc)), "cagr_p05": float(np.percentile(rc, 5)),
                        "cagr_p95": float(np.percentile(rc, 95)),
                        "maxdd_mean": float(np.mean([stats(r)["max_dd"] for r in rnd])),
                        "eras_cagr_mean": {n: float(np.mean([era_stats(r)[n]["cagr"] for r in rnd])) for n in ERAS}},
        "crashes_bench": {c: {"ew50": window_dd(ew, a, b), "nifty50": window_dd(nifty, a, b)}
                          for c, (a, b) in CRASHES.items()},
    }
    curves["daily_longest"], curves["daily_shortest"] = e, lo["equity"]

    for freq in ("weekly", "monthly"):
        eq, traded = run(opens, closes, score, ivs, START, None, N, "high", freq, CAP, SLIP, br, sr)
        lo, _ = run(opens, closes, score, ivs, START, None, N, "low", freq, CAP, SLIP, br, sr)
        rnd = [run(opens, closes, score, ivs, START, None, N, "random", freq, CAP, SLIP, br, sr,
                   rng=np.random.default_rng(k))[0]["equity"] for k in range(SEEDS)]
        e = eq["equity"]
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        rc = [stats(r)["cagr"] for r in rnd]
        rep[freq] = {
            "longest_streak": {"full": stats(e), "eras": era_stats(e),
                               "turnover_per_year": traded / float(e.mean()) / yrs,
                               "avg_held": float(eq["n_held"].mean()),
                               "crashes": {c: window_dd(e, a, b) for c, (a, b) in CRASHES.items()}},
            "shortest_streak": {"full": stats(lo["equity"]), "eras": era_stats(lo["equity"])},
            "random_from_above": {"cagr_mean": float(np.mean(rc)), "cagr_p05": float(np.percentile(rc, 5)),
                                  "cagr_p95": float(np.percentile(rc, 95)),
                                  "maxdd_mean": float(np.mean([stats(r)["max_dd"] for r in rnd])),
                                  "eras_cagr_mean": {n: float(np.mean([era_stats(r)[n]["cagr"] for r in rnd]))
                                                     for n in ERAS}},
            "crashes_bench": {c: {"ew50": window_dd(ew, a, b), "nifty50": window_dd(nifty, a, b)}
                              for c, (a, b) in CRASHES.items()},
        }
        curves[f"top10_longest_{freq}"] = e
        curves[f"top10_shortest_{freq}"] = lo["equity"]
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
