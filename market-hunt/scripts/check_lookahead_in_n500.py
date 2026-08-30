"""Were the biased N100 run's look-ahead picks already in point-in-time NIFTY500?

If yes, the 'lost' gains are legitimately capturable by an HONEST N500 strategy —
the stock was genuinely investable then, just not yet in the N100 sub-band.
"""
import os
import sys
from collections import defaultdict
from datetime import date, datetime

import requests
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

API = "http://localhost:3011/api/simulation/run"
BASE = dict(
    strategy_id="strategy_1781939795919", portfolio_base_value=100000,
    rebalance_frequency="monthly", rebalance_date="first", rebalance_type="equal_weight",
    universe="NIFTY100", benchmark_symbol="Nifty 50", max_holdings=10,
    momentum_ranking="20_day_return", start_date="2020-01-01", end_date="2026-06-30",
)

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
iv100 = load_membership(db, "NIFTY100", anchor=date.today())
iv500 = load_membership(db, "NIFTY500", anchor=date.today())

sim = requests.post(API, json={**BASE, "point_in_time_membership": False}, timeout=900).json()["simulation"]
results = sim["results"]
seen, rebal = set(), []
for day in results:
    ym = day["date"][:7]
    if ym not in seen:
        seen.add(ym); rebal.append(day)

la_slots = 0          # look-ahead in N100
in_n500 = 0           # ...and already in point-in-time N500
not_anywhere = 0
per_stock = defaultdict(lambda: {"la": 0, "n500": 0})
for day in rebal:
    d = datetime.strptime(day["date"], "%Y-%m-%d").date()
    m100, m500 = members_as_of(iv100, d), members_as_of(iv500, d)
    for h in day["holdings"]:
        s = h["symbol"]
        if s in m100:
            continue                       # eligible in N100, not look-ahead
        la_slots += 1
        per_stock[s]["la"] += 1
        if s in m500:
            in_n500 += 1; per_stock[s]["n500"] += 1
        else:
            not_anywhere += 1

print(f"\n=== biased N100 look-ahead slots: were they in point-in-time NIFTY500? ===")
print(f"look-ahead slots (held, not yet N100 member): {la_slots}")
print(f"  ALREADY in point-in-time NIFTY500:  {in_n500}  ({100*in_n500/la_slots:.0f}%)  <- legitimately investable then")
print(f"  not even in N500 then (true look-ahead): {not_anywhere}  ({100*not_anywhere/la_slots:.0f}%)")
print(f"\nper-stock (look-ahead months / of which already in N500):")
for s, d in sorted(per_stock.items(), key=lambda kv: kv[1]["la"], reverse=True)[:15]:
    print(f"  {s:12} held-as-N100-lookahead={d['la']:2}  already-in-N500={d['n500']:2}")
