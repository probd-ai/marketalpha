"""Independent cross-check of the /index-ratios endpoint.

Recomputes the latest date from raw indicator documents, by hand, with PIT membership
applied the slow obvious way -- then compares against what the endpoint returned. The
endpoint's speed comes from doing this in Mongo; this proves the answer did not change.
"""
import sys
from datetime import date, datetime

import requests

sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
from pymongo import MongoClient
from settings import MONGO_URI, DB_NAME
from membership import load_membership, members_as_of

BANDS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
PAIRS = [("80-100", "60-80"), ("60-80", "40-60"), ("40-60", "20-40"), ("80-100", "20-40")]
db = MongoClient(MONGO_URI)[DB_NAME]

failures = 0
for index_symbol in ("NIFTY50", "NIFTY200", "NIFTY500"):
    res = requests.get("http://127.0.0.1:3001/api/analytics/index-ratios",
                       params={"index_symbol": index_symbol, "lookback": "5Y"}, timeout=300).json()
    row = res["data"][-1]
    d = datetime.strptime(row["date"], "%Y-%m-%d")

    ivals = load_membership(db, index_symbol, date.today())
    members = members_as_of(ivals, d.date())

    counts = {f"{lo}-{hi}": 0 for lo, hi in BANDS}
    for doc in db.indicators.find({"indicator_type": "truevx", "date": d,
                                   "symbol": {"$in": sorted(members)}},
                                  {"_id": 0, "symbol": 1, "data.truevx_score": 1}):
        v = (doc.get("data") or {}).get("truevx_score")
        if v is None:
            continue
        for i, (lo, hi) in enumerate(BANDS):
            if lo <= v < hi or (i == len(BANDS) - 1 and v >= lo):
                counts[f"{lo}-{hi}"] += 1
                break

    ratios = {f"{n}/{den}": (round(counts[n] / counts[den], 4) if counts[den] else None)
              for n, den in PAIRS}

    ok_c = counts == row["counts"]
    ok_r = ratios == row["ratios"]
    failures += (not ok_c) + (not ok_r)
    print(f"{index_symbol:<18} {row['date']}  counts {'MATCH' if ok_c else 'DIFFER'}   "
          f"ratios {'MATCH' if ok_r else 'DIFFER'}")
    if not ok_c:
        print(f"   hand     {counts}")
        print(f"   endpoint {row['counts']}")
    if not ok_r:
        print(f"   hand     {ratios}")
        print(f"   endpoint {row['ratios']}")

print("\nOK — endpoint agrees with the hand computation" if failures == 0
      else f"\n{failures} DISAGREEMENTS")
sys.exit(1 if failures else 0)
