"""Feasibility/state check before building a Microcap250 PIT universe.

Answers: what's already in Mongo (current list, reconstitution), how much price/indicator
coverage exists for current microcaps, and whether tvdatafeed is usable for loading more.
The decisive gate: can we get prices for DELISTED/dead names? (without them -> survivorship).
"""
import os, sys
from datetime import date
from pymongo import MongoClient

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
IDX = "NIFTYMICROCAP250"

micro = sorted({d["Symbol"] for d in db.index_meta.find({"index_name": IDX}, {"Symbol": 1}) if d.get("Symbol")})
print(f"=== {IDX} current snapshot in index_meta ===")
print(f"members: {len(micro)}")

recon = db.index_reconstitution.count_documents({"index_name": IDX})
print(f"reconstitution events for {IDX}: {recon}  {'(NONE -> no PIT history yet)' if recon == 0 else ''}")

# price + indicator coverage of current microcaps
priced = set()
for coll in ("prices_2020_2024", "prices_2025_2029"):
    priced |= set(db[coll].distinct("symbol"))
ind = set(db.indicators.distinct("symbol", {"indicator_type": "truevx"}))
has_px = [s for s in micro if s in priced]
has_ind = [s for s in micro if s in ind]
print(f"\ncurrent microcaps WITH price data:     {len(has_px)}/{len(micro)} ({len(has_px)/len(micro)*100:.0f}%)")
print(f"current microcaps WITH truevx indicator: {len(has_ind)}/{len(micro)} ({len(has_ind)/len(micro)*100:.0f}%)")
print(f"need indicators computed for: {len([s for s in has_px if s not in ind])} priced-but-no-indicator")
print(f"no price at all (sample): {sorted(set(micro)-priced)[:25]}")

# tvdatafeed availability (the loader the pipeline uses)
print("\n=== tvdatafeed availability ===")
try:
    import tvDatafeed  # noqa: F401
    print(f"tvDatafeed importable: YES (version {getattr(tvDatafeed,'__version__','?')})")
except Exception as e:
    print(f"tvDatafeed import FAILED: {e}")

# can we read a known-dead name's price from our store? (the survivorship gate)
print("\n=== delisted/dead-name price coverage in our store ===")
for dead in ("DHFL", "FRETAIL", "LAKSHVILAS", "RELCAPITAL", "DHANI"):
    n = sum(db[c].count_documents({"symbol": dead}) for c in ("prices_2015_2019", "prices_2020_2024", "prices_2025_2029"))
    print(f"  {dead:12} price docs: {n}")
