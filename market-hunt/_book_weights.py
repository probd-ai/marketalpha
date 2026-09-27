"""Weighting the two-sleeve book, and what it actually costs in liquidity terms.

Sleeves (each split first / mid, equally within the sleeve):
  A  NIFTY200         monthly    Alpha + 22d top-7
  B  NIFTY500MICRO750 quarterly  Alpha + 22d top-20

Sweeps the A:B split from 0/100 to 100/0 on daily equity curves, and separately measures
where the money actually sits: at each date, the share of book market value held in names
that were PIT members of NIFTY200 / Smallcap250 / Microcap250. That is the number the
liquidity question turns on -- sleeve B reaches into microcap, but only for part of its book.
"""
from datetime import date, datetime

import numpy as np
import requests

from pymongo import MongoClient
from settings import MONGO_URI, DB_NAME
from membership import load_membership, members_as_of

BASE = "http://127.0.0.1:3001"
YEARS, RF = 4.657, 0.06
COMMON = {"strategy_id": "strategy_1781939795919", "start_date": "2022-01-01",
          "end_date": "2026-08-28", "portfolio_base_value": 100000,
          "rebalance_type": "equal_weight", "point_in_time_membership": True,
          "include_brokerage": True, "benchmark_symbol": "Nifty 50",
          "primary_filter_take": "none", "momentum_ranking": "price_roc_22d"}
SLEEVES = {
    "A1": {"universe": "NIFTY200", "rebalance_frequency": "monthly",
           "rebalance_date": "first", "max_holdings": 7},
    "A2": {"universe": "NIFTY200", "rebalance_frequency": "monthly",
           "rebalance_date": "mid", "max_holdings": 7},
    "B1": {"universe": "NIFTY500MICRO750", "rebalance_frequency": "quarterly",
           "rebalance_date": "first", "max_holdings": 20},
    "B2": {"universe": "NIFTY500MICRO750", "rebalance_frequency": "quarterly",
           "rebalance_date": "mid", "max_holdings": 20},
}

runs = {}
for k, extra in SLEEVES.items():
    print(f"running {k} ...", flush=True)
    r = requests.post(f"{BASE}/api/simulation/run", json={**COMMON, **extra}, timeout=7200)
    r.raise_for_status()
    runs[k] = r.json()["simulation"]

dates = sorted(set.intersection(*[{d["date"] for d in s["results"]} for s in runs.values()]))
val = {k: {d["date"]: d["portfolio_value"] for d in s["results"]} for k, s in runs.items()}
cur = {k: np.array([val[k][d] for d in dates]) for k in runs}
rets = {k: np.diff(v) / v[:-1] for k, v in cur.items()}


def stats(v):
    v = np.asarray(v, float); peak = np.maximum.accumulate(v)
    r = np.diff(v) / v[:-1]
    sh = ((r.mean() * 252 - RF) / (r.std(ddof=1) * np.sqrt(252))) if r.std(ddof=1) > 0 else 0.0
    return ((v[-1] / v[0] - 1) * 100, ((v[-1] / v[0]) ** (1 / YEARS) - 1) * 100,
            ((v - peak) / peak).min() * 100, sh)


print(f"\nWEIGHT SWEEP   A = NIFTY200 sleeve, B = Total750 sleeve (each split first/mid)")
print(f"{'A : B':<12}{'totRet%':>9}{'CAGR%':>8}{'maxDD%':>9}{'Sharpe':>8}")
print("-" * 46)
best = None
for wa in (0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0):
    wb = 1 - wa
    r = (wa / 2) * (rets["A1"] + rets["A2"]) + (wb / 2) * (rets["B1"] + rets["B2"])
    v = np.concatenate([[100000.0], 100000.0 * np.cumprod(1 + r)])
    t, c, d, s = stats(v)
    tag = "  <-- your 60/40" if abs(wa - 0.6) < 1e-9 else ("  <-- 50/50" if abs(wa - .5) < 1e-9 else "")
    print(f"{int(wa*100):>3} : {int(wb*100):<6}{t:>9.2f}{c:>8.2f}{d:>9.2f}{s:>8.3f}{tag}")
    if best is None or s > best[1]:
        best = (wa, s)
print(f"\nbest Sharpe at A = {int(best[0]*100)}%")

# ---- where the money actually sits ----
db = MongoClient(MONGO_URI)[DB_NAME]
tiers = {t: load_membership(db, t, date.today())
         for t in ("NIFTY200", "NIFTYSMALLCAP250", "NIFTYMICROCAP250")}
cache = {}


def tier_of(sym, d):
    key = (sym, d)
    if key in cache:
        return cache[key]
    dd = datetime.strptime(d, "%Y-%m-%d").date()
    out = "mid/other"
    for name, label in (("NIFTY200", "NIFTY200"), ("NIFTYMICROCAP250", "microcap"),
                        ("NIFTYSMALLCAP250", "smallcap")):
        if sym in members_as_of(tiers[name], dd):
            out = label; break
    cache[key] = out
    return out


hold = {k: {d["date"]: d.get("holdings") or [] for d in s["results"]} for k, s in runs.items()}
print(f"\nLIQUIDITY EXPOSURE — share of BOOK VALUE by cap tier")
print(f"{'A : B':<12}{'NIFTY200':>10}{'mid/other':>11}{'smallcap':>10}{'microcap':>10}")
print("-" * 53)
for wa in (0.5, 0.6, 0.7, 0.8):
    wb = 1 - wa
    acc = {"NIFTY200": 0.0, "mid/other": 0.0, "smallcap": 0.0, "microcap": 0.0}
    n = 0
    for d in dates[::21]:                       # monthly sample keeps this quick
        tot, part = 0.0, {k: 0.0 for k in acc}
        for k, w in (("A1", wa / 2), ("A2", wa / 2), ("B1", wb / 2), ("B2", wb / 2)):
            hs = hold[k].get(d, [])
            sv = sum(h["market_value"] for h in hs) or 1.0
            for h in hs:
                part[tier_of(h["symbol"], d)] += w * h["market_value"] / sv
                tot += w * h["market_value"] / sv
        if tot > 0:
            for kk in acc:
                acc[kk] += part[kk] / tot
            n += 1
    print(f"{int(wa*100):>3} : {int(wb*100):<6}" + "".join(
        f"{100*acc[k]/n:>10.1f}%" for k in ("NIFTY200", "mid/other", "smallcap", "microcap")))
