"""Robustness audit of the LIVE Growth sleeve, run through the production engine (fund.engine, not a re-implementation).

Live design (fund_config v7): kind=tranche, universe NIFTY200, top 5 by 22-day return among Alpha-eligible members
(truevx_score>90, mean_short>80, mean_mid>60, mean_long>50), weekly, each pick adds a tranche of equity/40 held
8 rebalances, 10% cap with trim, leftover cash deployed. Fills at the next open, statutory charges + slippage.

Tests (window 2020-10-01 -> latest bar, the PIT-membership floor):
  1. weekday luck      rebalance weekday 0..4 (week_session k)
  2. eras              halves, calendar years, 3 worst drawdowns
  3. random controls   (a) random top-5 from the SAME Alpha-eligible pool (tests the 22d ranking)
                       (b) random 5 from ALL NIFTY200 members (tests filter + ranking together)
                       same engine, sizing, holding and costs; SEEDS each, weekday 0
  4. benchmarks        Nifty 50 / Nifty 500 buy & hold, equal-weight NIFTY200 monthly
  5. cost stress       slippage 5 / 15 / 30 bp
  6. neighbours        top 3/5/7, weeks 4/8/12, max_w 0.10/0.15/0.20 (one at a time) — plateau or spike?

  .venv/Scripts/python.exe scripts/research_grid/growth_robustness.py
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from fund.data import BENCH, build_market, charge_rates, load_prices  # noqa: E402
from fund.engine.calendar import trading_days  # noqa: E402
from fund.engine.costs import Costs  # noqa: E402
from fund.engine.factory import base_schedule, make_book  # noqa: E402
from fund.engine.replay import replay_sleeve  # noqa: E402
from n200_regime_dd import episodes  # noqa: E402

START = date(2020, 10, 1)
LIVE = {"top": 5, "weeks": 8, "slot_div": 40, "max_w": 0.10}
SEEDS = 40
SPLIT = pd.Timestamp("2023-10-01")
OUT = HERE / "growth_robustness_results.json"


def stats(eq: pd.Series) -> dict:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    vol = r.std() * np.sqrt(252)
    cg = lambda s: (s.iloc[-1] / s.iloc[0]) ** (365.25 / max((s.index[-1] - s.index[0]).days, 1)) - 1
    return {"cagr": (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1, "sharpe": r.mean() * 252 / vol if vol else float("nan"),
            "maxdd": float((eq / eq.cummax() - 1).min()), "h1": cg(eq[eq.index < SPLIT]), "h2": cg(eq[eq.index >= SPLIT])}


def line(name, s, extra=""):
    print(f"  {name:<34} CAGR {s['cagr']:6.1%}  Sharpe {s['sharpe']:.2f}  DD {s['maxdd']:6.1%}  "
          f"H1 {s['h1']:6.1%}  H2 {s['h2']:6.1%}{extra}")


def main():
    t0 = time.time()
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    m = build_market(db, "NIFTY200", "tranche", START)
    cal = trading_days(m.closes[BENCH], START)
    br, sr = charge_rates()
    costs = lambda bp=5.0: Costs(br, sr, bp / 1e4)

    def sim(params=LIVE, k=0, mk=m, bp=5.0):
        res = replay_sleeve(make_book("tranche", params), mk, costs(bp), START, None, base_schedule("tranche", cal, k), 1.0)
        eq = res.equity
        traded = sum(o.value for os in res.orders.values() for o in os)
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        held = [len(v) for v in [None]] if False else None
        return eq, traded / yrs / eq.mean(), res

    out = {}
    print(f"window {cal[0].date()} -> {cal[-1].date()} · NIFTY200 · live params {LIVE}")

    print("\n1. weekday luck (live params)")
    base_eq = None
    for k in range(5):
        eq, to, res = sim(k=k)
        s = {**stats(eq), "turnover": to}
        out[f"weekday{k}"] = s
        base_eq = eq if k == 0 else base_eq
        line(f"weekday {k}", s, f"  turnover {to:.1f}x")
    print(f"   ({time.time() - t0:.0f}s)")

    print("\n2. eras (weekday 0)")
    yearly = base_eq.groupby(base_eq.index.year).apply(lambda s: s.iloc[-1] / s.iloc[0] - 1)
    out["yearly"] = {int(y): float(v) for y, v in yearly.items()}
    print("  calendar-year returns: " + "  ".join(f"{y}:{v:+.1%}" for y, v in yearly.items()))
    out["worst_dd"] = episodes(base_eq)
    for e in out["worst_dd"]:
        print(f"  drawdown {e['depth']:6.1%}  peak {e['peak'].date()}  trough {e['trough'].date()}  "
              f"recovered {e['recovered'].date() if e['recovered'] is not None else 'not yet'}")

    print("\n3. random controls (weekday 0)")
    rng = np.random.default_rng(3)
    mask = m.roc.notna()
    for label, all_members in (("(a) random top-5 from Alpha pool", False), ("(b) random 5 from all NIFTY200", True)):
        cg = []
        for _ in range(SEEDS):
            noise = pd.DataFrame(rng.random(m.roc.shape), index=m.roc.index, columns=m.roc.columns).where(mask)
            mk = replace(m, roc=noise, elig=(m.elig | True) if all_members else m.elig)
            cg.append(stats(sim(mk=mk)[0])["cagr"])
        cg = np.array(cg)
        live = out["weekday0"]["cagr"]
        out[label] = {"p5": float(np.percentile(cg, 5)), "p50": float(np.median(cg)), "p95": float(np.percentile(cg, 95)),
                      "live_beats": float((cg < live).mean())}
        x = out[label]
        print(f"  {label:<34} CAGR p5 {x['p5']:6.1%}  p50 {x['p50']:6.1%}  p95 {x['p95']:6.1%}  -> live beats {x['live_beats']:.0%}")
    print(f"   ({time.time() - t0:.0f}s)")

    print("\n4. benchmarks")
    _, ix = load_prices(db, ["Nifty 500"], datetime(2020, 9, 1))
    for b in (BENCH, "Nifty 500"):
        s = stats(ix[b].reindex(cal).ffill())
        out[f"bench {b}"] = s
        line(f"{b} buy & hold", s)
    ew = m.closes.drop(columns=[BENCH]).reindex(cal)
    rets = ew.pct_change(fill_method=None)
    mem = pd.DataFrame({d: pd.Series(True, index=list(m.members(d.date()))) for d in cal}).T.reindex(columns=ew.columns)
    ew_r = rets.where(mem.shift(1).fillna(False).astype(bool)).mean(axis=1).fillna(0)
    s = stats((1 + ew_r).cumprod())
    out["bench EW NIFTY200 (daily, no costs)"] = s
    line("EW NIFTY200 (daily rebal, no costs)", s)

    print("\n5. cost stress (weekday 0)")
    for bp in (5, 15, 30):
        s = stats(sim(bp=bp)[0])
        out[f"slip{bp}"] = s
        line(f"slippage {bp} bp", s)

    print("\n6. parameter neighbours (weekday 0, one at a time)")
    for key, vals in (("top", (3, 5, 7)), ("weeks", (4, 8, 12)), ("max_w", (0.10, 0.15, 0.20))):
        for v in vals:
            p = {**LIVE, key: v}
            eq, to, _ = sim(params=p)
            s = {**stats(eq), "turnover": to}
            out[f"{key}={v}"] = s
            line(f"{key}={v}" + ("  (live)" if LIVE[key] == v else ""), s, f"  turnover {to:.1f}x")
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print(f"\nsaved {OUT.name} · {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
