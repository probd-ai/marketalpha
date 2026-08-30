"""Find and print the stored definition of strategy_1781939795919 (and list all strategies)."""
import json
from pymongo import MongoClient

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
SID = "strategy_1781939795919"

print("collections:", db.list_collection_names())
for coll in db.list_collection_names():
    try:
        doc = db[coll].find_one({"$or": [{"id": SID}, {"strategy_id": SID}, {"_id": SID}]})
    except Exception:
        doc = None
    if doc:
        doc.pop("_id", None)
        print(f"\n=== found in '{coll}' ===")
        print(json.dumps(doc, indent=2, default=str)[:4000])
        break
else:
    # fallback: dump any strategies collection contents (names + ids + rules)
    for coll in db.list_collection_names():
        if "strateg" in coll.lower():
            print(f"\n--- '{coll}' docs ---")
            for d in db[coll].find({}, {"_id": 0}).limit(20):
                print(json.dumps(d, default=str)[:600])
