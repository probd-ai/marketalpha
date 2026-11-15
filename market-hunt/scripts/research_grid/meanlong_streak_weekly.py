"""The top-10 shortest-streak book checked WEEKLY instead of daily.

Same rule as meanlong_streak.py's daily book (10 names; sell when mean_long has dropped below its 222-session SMA;
refill with the shortest streak; retained names never trimmed), but exits and refills happen only on the weekly
rebalance session — weekly+k, k = 0..4 (weekday luck; median and range reported). Index exits are still sold the next
session. Point-in-time NIFTY 50, Jan 2008 -> latest, statutory charges + 5 bp, mean_long recomputed from prices.

  .venv/Scripts/python.exe scripts/research_grid/meanlong_streak_weekly.py
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
from analyze_book import episodes  # noqa: E402
from crash_test import CRASHES, build_intervals, read_mcwb, window_dd  # noqa: E402
from grid_meanrev import BENCH, charge_rates  # noqa: E402
from meanlong_slope import ERAS, MA, START, load_ohlc, mean_long_series, stats  # noqa: E402
from meanlong_streak import daily_picker, streaks  # noqa: E402
from rotation_roc import run  # noqa: E402

N, SLIP, CAP = 10, 0.0005, 10_000_000.0


def trailing(e: pd.Series) -> dict:
    end, out = e.index[-1], {}
    for y in (10, 5, 3, 2, 1):
        seg = e[e.index >= end - pd.DateOffset(years=y)]
        yrs = (seg.index[-1] - seg.index[0]).days / 365.25
        out[f"{y}y"] = (seg.iloc[-1] / seg.iloc[0]) ** (1 / yrs) - 1
    return out


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
    br, sr = charge_rates()
    last_close = closes.ffill().iloc[-1]

    def era(eq):
        out = {"full": stats(eq)}
        out.update({n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()})
        return out

    res, curves = {}, {}
    for k in ["daily"] + list(range(5)):
        freq = "daily" if k == "daily" else ("weekly" if k == 0 else f"weekly+{k}")
        log = []
        eq, traded = run(opens, closes, streak, ivs, START, None, N, "high", freq, CAP, SLIP, br, sr,
                         pick_fn=daily_picker(streak, "shortest"), log=log)
        e = eq["equity"]
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        ep = episodes(pd.DataFrame(log), last_close, e.index[-1])
        c = ep[ep.status == "closed"]
        res[str(k)] = {"era": era(e), "trailing": trailing(e), "turnover": traded / float(e.mean()) / yrs,
                       "trades_per_year": len(c) / yrs, "hold_median": float(c.days.median()),
                       "win_rate": float((c.ret > 0).mean()),
                       "crashes": {n: window_dd(e, a, b) for n, (a, b) in CRASHES.items()}}
        curves[f"k{k}"] = e
        print(k, f"{stats(e)['cagr']:.4f} dd {stats(e)['max_dd']:.3f}", flush=True)
    wk = [str(k) for k in range(5)]
    summ = {}
    for w in res["0"]["era"]:
        summ[w] = {f: {"median": float(np.median([res[k]["era"][w][f] for k in wk])),
                       "min": min(res[k]["era"][w][f] for k in wk), "max": max(res[k]["era"][w][f] for k in wk)}
                   for f in ("cagr", "max_dd", "sharpe")}
    for t in res["0"]["trailing"]:
        summ[f"trailing_{t}"] = {"median": float(np.median([res[k]["trailing"][t] for k in wk])),
                                 "min": min(res[k]["trailing"][t] for k in wk), "max": max(res[k]["trailing"][t] for k in wk)}
    for f in ("turnover", "trades_per_year", "hold_median", "win_rate"):
        summ[f] = float(np.median([res[k][f] for k in wk]))
    summ["crashes"] = {n: float(np.median([res[k]["crashes"][n] for k in wk])) for n in CRASHES}
    rep = {"weekly_summary": summ, "runs": res}
    out = HERE / "meanlong_streak_weekly"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(summ, indent=2, default=str))


if __name__ == "__main__":
    main()
