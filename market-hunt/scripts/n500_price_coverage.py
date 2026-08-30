"""Report price-coverage gaps for NIFTY500 ever-members over the window.

Computes the union of every symbol that was a NIFTY500 member at any point in
2020-01-01..2026-06-30 (using the loaded reconstitution + current index_meta),
then reports which of those have NO documents in any prices_* collection.
Report only -- performs no backfill.
"""
import sys
from datetime import date

from pymongo import MongoClient

sys.path.insert(0, ".")
from membership import load_membership, union_over  # noqa: E402

INDEX = "NIFTY500"
START, END = date(2020, 1, 1), date(2026, 6, 30)


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    intervals = load_membership(db, INDEX, END)
    ever = union_over(intervals, START, END)
    print(f"ever-members 2020-01-01..2026-06-30: {len(ever)}")

    price_colls = [c for c in db.list_collection_names() if c.startswith("prices_")]
    print(f"price collections scanned: {len(price_colls)} -> {sorted(price_colls)}")

    with_price = set()
    for sym in ever:
        for c in price_colls:
            if db[c].count_documents({"symbol": sym}, limit=1):
                with_price.add(sym)
                break
    missing = sorted(ever - with_price)
    print(f"\never-members WITH price data:    {len(with_price)}")
    print(f"ever-members WITHOUT price data: {len(missing)}")
    print(f"\nMISSING price coverage ({len(missing)}):")
    for s in missing:
        print(" ", s)


if __name__ == "__main__":
    main()
