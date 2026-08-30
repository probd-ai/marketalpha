"""Load NSE index reconstitution (rebalance) events into market_hunt.index_reconstitution.

This is the STORE step of the `capture_index_rebalance` procedure
(see market-hunt/docs/capture_index_rebalance.md). It upserts dated add/remove
events from a changelog CSV into the canonical Mongo collection.

CSV format (the tvagent seed format):
    effective_date,added,removed,source_url
    2021-03-31,TATACONSUM,GAIL,https://www.niftyindices.com/Press_Release/ind_prs23022021.pdf
  - effective_date: YYYY-MM-DD (date the membership change takes effect)
  - added / removed: ';'-separated NSE tvdatafeed symbols (either may be blank)
  - source_url: the authoritative niftyindices press-release URL (no 'VERIFY' tokens)

Idempotent: re-running upserts on _id = "<INDEX>_<effective_date>".

Usage (run from the market-hunt repo root, with the repo venv):
    ./.venv/Scripts/python.exe scripts/load_reconstitution.py \
        --index NIFTY50 --csv ../backend/data/seed/nifty50_changelog.csv
"""
import argparse
import csv
from datetime import datetime, timezone

from pymongo import MongoClient

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "market_hunt"
COLLECTION = "index_reconstitution"


def _symbols(cell: str | None) -> list[str]:
    return [s.strip() for s in (cell or "").split(";") if s.strip()]


def load(index_name: str, csv_path: str) -> int:
    client = MongoClient(MONGO_URI)
    coll = client[DB_NAME][COLLECTION]
    coll.create_index([("index_name", 1), ("effective_date", 1)], unique=True)
    now = datetime.now(timezone.utc)
    upserts = 0
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            eff_str = row["effective_date"].strip()
            url = row["source_url"].strip()
            if url.upper() == "VERIFY" or not url.lower().startswith("http"):
                raise ValueError(
                    f"unverified source_url for {index_name} {eff_str!r}: {url!r} "
                    "— verify against niftyindices before loading (no fabrication)."
                )
            doc = {
                "_id": f"{index_name}_{eff_str}",
                "index_name": index_name,
                "effective_date": datetime.strptime(eff_str, "%Y-%m-%d"),
                "added": _symbols(row.get("added")),
                "removed": _symbols(row.get("removed")),
                "source_url": url,
                "source": "niftyindices_press_release",
                "verified": True,
                "loaded_at": now,
            }
            coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)
            upserts += 1
    print(f"upserted {upserts} {index_name} reconstitution events into {DB_NAME}.{COLLECTION}")
    client.close()
    return upserts


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Load index reconstitution events into Mongo")
    ap.add_argument("--index", required=True, help="index key, e.g. NIFTY50 / NIFTY100")
    ap.add_argument("--csv", required=True, help="path to a *_changelog.csv")
    args = ap.parse_args()
    load(args.index, args.csv)
