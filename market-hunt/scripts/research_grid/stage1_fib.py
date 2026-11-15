"""NIFTY200 -> Growth stacked filter -> price above 66% of the rolling 222-session high-low range -> hold all, equal weight.

Universe   point-in-time NIFTY200 members on the trade day (no second index)
Filter 1   Growth/Alpha stack on the signal close: truevx_score>90, mean_short>80, mean_mid>60, mean_long>50
Filter 2   close > L + 0.66 * (H - L), H / L = highest high / lowest low of the last 222 sessions up to the signal day
           (each stock's own bars); a name that fails is not bought — its share of capital stays in cash
Book       every name passing both, equal weight; signal = previous session, trade at the open
Exit modes for a HOLDING that falls below the 66% line:
  exit       filters re-applied at every rebalance — fails either -> sold
  entry-only filter 2 gates new buys only; a holding is kept while it still passes filter 1
Cadence    weekly (first session of each ISO week) and monthly (first session of the month)
Controls   same book without filter 2 (Growth-only), and RANDOM same-count draws from the Growth-eligible names
           (SEEDS draws; tests whether filter 2 beats chance at choosing that many names)
Costs: statutory charges + 5 bp slippage; a name that leaves NIFTY200 is sold at the next open. Rf = 0.

  .venv/Scripts/python.exe scripts/research_grid/stage1_fib.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from fund.data import BENCH, charge_rates, load_truevx  # noqa: E402
from fund.engine.calendar import period_start_shifted, trading_days, week_session  # noqa: E402
from fund.engine.signals import alpha_eligible  # noqa: E402
from lowrange_breakout import SPLIT, cagr_between, load_ohlc, stats  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402
from stage1_trail import run  # noqa: E402

START = date(2020, 10, 1)
UNIVERSE = "NIFTY200"
LOOKBACK, LEVEL = 222, 0.66
SEEDS = 100
OUT = HERE / "stage1_fib_results.json"


def fib_ok(ohlc, cols) -> pd.DataFrame:
    """close > low + LEVEL*(high-low) over each stock's own last LOOKBACK bars (needs a full window)."""
    out = {}
    for s in cols:
        c = ohlc["close"][s].dropna()
        h = ohlc["high"][s].reindex(c.index).rolling(LOOKBACK, min_periods=LOOKBACK).max()
        lo = ohlc["low"][s].reindex(c.index).rolling(LOOKBACK, min_periods=LOOKBACK).min()
        out[s] = (c > lo + LEVEL * (h - lo)).reindex(ohlc["close"].index)
    return pd.DataFrame(out).ffill().fillna(False).astype(bool)


def summ(r) -> dict:
    return {**stats(r["eq"]), "h1": cagr_between(r["eq"], b=SPLIT), "h2": cagr_between(r["eq"], a=SPLIT),
            "invested": r["invested"], "turnover_yr": r["turnover_yr"]}


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    today = date.today()
    iv = load_membership(db, UNIVERSE, today)
    syms = sorted(union_over(iv, START, today))
    since = datetime(2019, 1, 1)                          # 222-bar window + TrueVX history before the start
    ohlc = load_ohlc(db, syms, since)
    cols = [c for c in ohlc["close"].columns if c != BENCH]
    idx = ohlc["close"].index
    elig = alpha_eligible(load_truevx(db, cols, since), idx, cols)
    fib = fib_ok(ohlc, cols)
    cal = trading_days(ohlc["close"][BENCH], START)
    pool_of = lambda day: members_as_of(iv, day)
    prev_of = {d: idx[idx.get_loc(d) - 1] for d in cal}
    br, sr = charge_rates()

    def growth(d):
        p = prev_of[d]
        return [s for s in sorted(pool_of(d.date())) if s in elig.columns and bool(elig.at[p, s])]

    def both(d, held=None):
        p = prev_of[d]
        return [s for s in growth(d) if bool(fib.at[p, s])]

    def entry_only(d, held=None):
        p = prev_of[d]
        return [s for s in growth(d) if bool(fib.at[p, s]) or s in (held or ())]

    schedules = {"weekly": sorted(week_session(cal, 0)), "monthly": sorted(period_start_shifted(cal, "monthly", 0))}
    out = {}
    for cad, sched in schedules.items():
        g_n = np.mean([len(growth(d)) for d in sched])
        b_n = np.mean([len(both(d)) for d in sched])
        print(f"\n=== {cad}: {len(sched)} rebalances · Growth-eligible avg {g_n:.1f} · + 66% filter avg {b_n:.1f} "
              f"(cash rebalances {sum(not both(d) for d in sched)})")
        res = {}
        for name, fn in (("Growth only", lambda d, held=None: growth(d)), ("Growth + 66% (exit)", both),
                         ("Growth + 66% (entry-only)", entry_only)):
            res[name] = summ(run(cal, ohlc, cols, sched, fn, pool_of, {}, None, br, sr))
        rng = np.random.default_rng(5)
        rc = []
        for _ in range(SEEDS):
            draw = {d: sorted(rng.choice(growth(d), size=len(both(d)), replace=False)) if both(d) else [] for d in sched}
            rc.append(summ(run(cal, ohlc, cols, sched, lambda d, held=None: draw.get(d, []), pool_of, {}, None,
                               br, sr))["cagr"])
        rc = np.array(rc)
        res["random same-count"] = {"p5": float(np.percentile(rc, 5)), "p50": float(np.median(rc)),
                                    "p95": float(np.percentile(rc, 95)),
                                    "exit_beats": float((rc < res["Growth + 66% (exit)"]["cagr"]).mean())}
        for name, s in res.items():
            if "cagr" in s:
                print(f"  {name:<26} CAGR {s['cagr']:6.1%}  Sharpe {s['sharpe']:.2f}  DD {s['maxdd']:6.1%}  "
                      f"H1 {s['h1']:6.1%}  H2 {s['h2']:6.1%}  invested {s['invested']:.0%}  turnover {s['turnover_yr']:.1f}x")
        x = res["random same-count"]
        print(f"  random same-count ({SEEDS})   CAGR p5 {x['p5']:.1%} p50 {x['p50']:.1%} p95 {x['p95']:.1%} "
              f"-> 66% (exit) beats {x['exit_beats']:.0%}")
        out[cad] = {"growth_avg": g_n, "final_avg": b_n, **res}
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print(f"\nsaved {OUT.name}")


if __name__ == "__main__":
    main()
