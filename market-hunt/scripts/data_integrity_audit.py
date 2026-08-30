"""Data-integrity audit for the NIFTY500 point-in-time backtest universe.

Two survivorship leaks to quantify:
  1. CHANGELOG DEPTH: how many NIFTY500 reconstitution events exist, over what dates?
     If shallow, members_as_of() for early dates collapses toward today's list (hidden bias).
  2. PRICE COVERAGE: of every name that was EVER a member, how many have price data?
     Names removed from the index (delisted/merged) that LACK prices are silently skipped ->
     their (often negative, terminal) returns never hit the backtest -> upward bias.
"""
import os, sys
from datetime import date
import numpy as np
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of, union_over  # noqa: E402

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
IDX = "NIFTY500"
SIM_START, SIM_END = date(2019, 1, 1), date(2026, 6, 30)

# --- 1. changelog depth ---
ev = list(db.index_reconstitution.find({"index_name": IDX}).sort("effective_date", 1))
print(f"=== {IDX} reconstitution changelog ===")
print(f"events: {len(ev)}")
if ev:
    dates = [e["effective_date"] for e in ev]
    n_add = sum(len(e.get("added") or []) for e in ev)
    n_rem = sum(len(e.get("removed") or []) for e in ev)
    print(f"date range: {min(dates).date()} .. {max(dates).date()}")
    print(f"total added: {n_add}   total removed: {n_rem}")
    print("per-event:", [(e['effective_date'].date().isoformat(),
                          len(e.get('added') or []), len(e.get('removed') or [])) for e in ev][:20])

# --- 2. membership universe ---
iv = load_membership(db, IDX, anchor=date.today())
current = members_as_of(iv, date.today())
ever = union_over(iv, SIM_START, SIM_END)
removed_ever = ever - current      # were members during sim window, not now (delist/merge/dropped)
print(f"\n=== membership ===")
print(f"current members: {len(current)}")
print(f"ever-members in {SIM_START}..{SIM_END}: {len(ever)}")
print(f"removed-during-window (survivorship-critical): {len(removed_ever)}")

# --- 3. price coverage ---
priced = set()
for coll in ("prices_2020_2024", "prices_2025_2029"):
    priced |= set(db[coll].distinct("symbol"))
missing = ever - priced
missing_removed = removed_ever - priced
print(f"\n=== price coverage (any price in 2020-2029 collections) ===")
print(f"ever-members WITHOUT any price data: {len(missing)} / {len(ever)} ({len(missing)/len(ever)*100:.1f}%)")
print(f"  of the removed-during-window names: {len(missing_removed)} / {len(removed_ever)} "
      f"({len(missing_removed)/max(len(removed_ever),1)*100:.1f}%) have NO price -> silently skipped")

# --- 4. for removed names WITH price, do prices reach their removal date? ---
import datetime as _dt
def last_price_date(sym):
    docs = []
    for coll in ("prices_2020_2024", "prices_2025_2029"):
        d = db[coll].find_one({"symbol": sym}, sort=[("date", -1)])
        if d:
            docs.append(d["date"])
    return max(docs) if docs else None

stub = []
for s in sorted(removed_ever & priced):
    ivs = [x for x in iv if x.symbol == s and x.effective_to is not None]
    if not ivs:
        continue
    eff_to = max(x.effective_to for x in ivs)
    lpd = last_price_date(s)
    if lpd is not None:
        gap = (eff_to - lpd.date()).days
        if gap > 45:        # price series ends >45d before the name left the index
            stub.append((s, eff_to.isoformat(), lpd.date().isoformat(), gap))

print(f"\n=== removed names whose price series ENDS >45d before they left the index ===")
print(f"count: {len(stub)} (price stops early -> exit return understated/stale)")
for s, et, lp, g in stub[:25]:
    print(f"  {s:14} left~{et}  last price {lp}  gap {g}d")

print(f"\n=== sample of removed-members with NO price at all (would be skipped) ===")
print(sorted(missing_removed)[:40])
