"""Baseline vs candidate for the index-ratio endpoint.

(a) how the existing /index-distribution endpoint does it: stream every indicator doc
    into Python and bucket in a loop.
(b) one server-side $group with $switch -- Mongo buckets, Python gets ~1250 rows.
(c) (b) partitioned into membership epochs, which makes it point-in-time correct.
    Membership is piecewise-constant between reconstitution events, so the window
    splits into a couple of dozen epochs, each a single $in over that epoch's members.
"""
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
from pymongo import MongoClient
from settings import MONGO_URI, DB_NAME
from membership import load_membership, members_as_of

INDEX, METRIC = "NIFTY500", "truevx_score"
END = datetime(2026, 9, 4)
START = END - timedelta(days=5 * 365)
BANDS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]

db = MongoClient(MONGO_URI)[DB_NAME]
current = [d["Symbol"] for d in db.index_meta.find({"index_name": INDEX}, {"Symbol": 1})]
print(f"{INDEX}: {len(current)} current members, window {START.date()}..{END.date()}")


def band_switch(field):
    branches = [{"case": {"$lt": [field, hi]}, "then": f"{lo}-{hi}"} for lo, hi in BANDS[:-1]]
    return {"$switch": {"branches": branches, "default": f"{BANDS[-1][0]}-{BANDS[-1][1]}"}}


# ---------- (a) baseline: the existing loop ----------
t = time.time()
cur = db.indicators.find({"indicator_type": "truevx", "symbol": {"$in": current},
                          "date": {"$gte": START, "$lte": END}}).sort("date", 1)
dist = defaultdict(lambda: defaultdict(int))
seen = defaultdict(set)
n = 0
for doc in cur:
    n += 1
    v = doc["data"].get(METRIC)
    if v is None:
        continue
    ds = doc["date"].strftime("%Y-%m-%d")
    seen[ds].add(doc["symbol"])
    for i, (lo, hi) in enumerate(BANDS):
        if lo <= v < hi or (i == len(BANDS) - 1 and v >= lo):
            dist[ds][f"{lo}-{hi}"] += 1
            break
a_secs, a_dates = time.time() - t, len(dist)
print(f"(a) python loop        {a_secs:7.2f}s   {n:,} docs -> {a_dates} dates")

# ---------- (b) one aggregation, current members ----------
t = time.time()
pipe = [
    {"$match": {"indicator_type": "truevx", "symbol": {"$in": current},
                "date": {"$gte": START, "$lte": END}, f"data.{METRIC}": {"$ne": None}}},
    {"$group": {"_id": {"d": "$date", "b": band_switch(f"$data.{METRIC}")}, "n": {"$sum": 1}}},
]
rows_b = list(db.indicators.aggregate(pipe, allowDiskUse=True))
b_secs = time.time() - t
agg_b = defaultdict(dict)
for r in rows_b:
    agg_b[r["_id"]["d"].strftime("%Y-%m-%d")][r["_id"]["b"]] = r["n"]
print(f"(b) mongo $group       {b_secs:7.2f}s   {len(rows_b):,} rows -> {len(agg_b)} dates")

# ---------- (c) epoch-partitioned, point-in-time ----------
t = time.time()
ivals = load_membership(db, INDEX, date.today())
edges = sorted({START.date(), END.date()} |
               {e for iv in ivals for e in (iv.effective_from, iv.effective_to) if e and START.date() < e < END.date()})
epochs = []
for i, lo in enumerate(edges[:-1]):
    hi = edges[i + 1]
    mem = sorted(members_as_of(ivals, lo))
    if mem:
        epochs.append((lo, hi, mem))
agg_c = defaultdict(dict)
rows_c = 0
for lo, hi, mem in epochs:
    pipe = [
        {"$match": {"indicator_type": "truevx", "symbol": {"$in": mem},
                    "date": {"$gte": datetime.combine(lo, datetime.min.time()),
                             "$lt": datetime.combine(hi, datetime.min.time())},
                    f"data.{METRIC}": {"$ne": None}}},
        {"$group": {"_id": {"d": "$date", "b": band_switch(f"$data.{METRIC}")}, "n": {"$sum": 1}}},
    ]
    for r in db.indicators.aggregate(pipe, allowDiskUse=True):
        rows_c += 1
        agg_c[r["_id"]["d"].strftime("%Y-%m-%d")][r["_id"]["b"]] = r["n"]
c_secs = time.time() - t
print(f"(c) PIT epochs ({len(epochs):>2})    {c_secs:7.2f}s   {rows_c:,} rows -> {len(agg_c)} dates")

# ---------- agreement check: (a) vs (b) must be identical ----------
bad = 0
for ds in sorted(set(dist) | set(agg_b)):
    for lo, hi in BANDS:
        lbl = f"{lo}-{hi}"
        if dist[ds].get(lbl, 0) != agg_b[ds].get(lbl, 0):
            bad += 1
            if bad <= 5:
                print(f"    MISMATCH {ds} {lbl}: loop={dist[ds].get(lbl,0)} agg={agg_b[ds].get(lbl,0)}")
print(f"\n(a) vs (b) band counts: {'IDENTICAL' if bad == 0 else str(bad) + ' MISMATCHES'}")
print(f"speedup (b) vs (a): {a_secs / b_secs:.1f}x   |   (c) PIT vs (a): {a_secs / c_secs:.1f}x")

last = max(agg_c)
print(f"\nsample PIT counts {last}: {dict(sorted(agg_c[last].items()))}")
print(f"sample non-PIT    {last}: {dict(sorted(agg_b[last].items()))}")
