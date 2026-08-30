"""Where did the PIT-off 'riser' stocks come from BEFORE they joined NIFTY500?

A name that JOINED NIFTY500 after Jan-2020 was, in 2020, ranked below 500 -> outside the index.
This finds the 52 selectable risers (current N500, joined post-2020, had pre-2020 TrueVX data)
and reports which index segment they belonged to then, using whatever index membership data we
have (reconstitution events + current index_names in symbol_mappings).
"""
import os, sys
from collections import Counter
from datetime import date, datetime
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]

# what index data do we have?
print("=== indices with reconstitution events ===")
for r in db.index_reconstitution.aggregate([{"$group": {"_id": "$index_name", "n": {"$sum": 1}}},
                                             {"$sort": {"n": -1}}]):
    print(f"  {r['_id']:28} {r['n']} events")
print("\n=== indices present in index_meta (current snapshots) ===")
for r in db.index_meta.aggregate([{"$group": {"_id": "$index_name", "n": {"$sum": 1}}},
                                  {"$sort": {"n": -1}}]):
    print(f"  {r['_id']:32} {r['n']} members")

# risers
iv = load_membership(db, "NIFTY500", anchor=date.today())
current = members_as_of(iv, date.today())
pit_2020 = members_as_of(iv, date(2020, 1, 1))
entrants = sorted(current - pit_2020)
risers = []
for s in entrants:
    first = db.indicators.find_one({"symbol": s, "indicator_type": "truevx"},
                                   sort=[("date", 1)], projection={"date": 1})
    if first and first["date"] < datetime(2020, 1, 1):
        risers.append(s)
print(f"\n=== {len(risers)} selectable risers (joined N500 post-2020, had pre-2020 data) ===")

# current index memberships of the risers (from symbol_mappings)
seg = Counter()
detail = []
for s in risers:
    m = db.symbol_mappings.find_one({"symbol": s}, {"index_names": 1, "company_name": 1, "industry": 1})
    idxs = (m or {}).get("index_names", []) or []
    # smallest-segment tag for readability
    tag = ("Smallcap250" if "NIFTYSMALLCAP250" in idxs else
           "Midcap150" if "NIFTYMIDCAP150" in idxs else
           "Microcap250" if "NIFTYMICROCAP250" in idxs else
           "TotalMarket-only" if "NIFTYTOTALMARKET" in idxs else
           "other/none")
    for ix in idxs:
        seg[ix] += 1
    detail.append((s, tag, (m or {}).get("industry", "")[:22]))

print("\ncurrent index memberships of the 52 risers (count in each index):")
for ix, n in seg.most_common(15):
    print(f"  {ix:34} {n}")

print("\nper-riser current smallest-segment tag (where they sit in the cap ladder NOW):")
tagc = Counter(t for _, t, _ in detail)
for t, n in tagc.most_common():
    print(f"  {t:18} {n}")

print("\nsample risers (symbol | now-segment | industry):")
for s, t, ind in detail[:30]:
    print(f"  {s:14} {t:16} {ind}")
