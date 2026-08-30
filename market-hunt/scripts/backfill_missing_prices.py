"""Backfill OHLC for index ever-members that have no price data in market_hunt.

Used to close the point-in-time coverage gap: a since-removed constituent often
has no prices loaded (it left every loaded universe). This fetches them via
tvdatafeed so point-in-time backtests can actually trade the removed cohort.

Computes the gap itself (union of NIFTY500 ever-members in the window MINUS
symbols that already have prices), ensures each has a symbol_mapping (inserting a
synthetic one if absent — the download path gates on it), then downloads full
history into the prices_YYYY_YYYY partitions. Idempotent and resumable: already
priced symbols are skipped, so re-running only retries the failures.

Run from the market-hunt repo root with the repo venv:
    ./.venv/Scripts/python.exe scripts/backfill_missing_prices.py --index NIFTY500
"""
import argparse
import asyncio
import os
import sys
from datetime import date, datetime

from pymongo import MongoClient

# scripts/ lives under the repo root; make repo-root modules importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from membership import load_membership, union_over  # noqa: E402
from stock_data_manager import StockDataManager  # noqa: E402

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "market_hunt"
WINDOW_START = date(2020, 1, 1)
WINDOW_END = date(2026, 6, 30)


def compute_missing(index_name: str):
    db = MongoClient(MONGO_URI)[DB_NAME]
    intervals = load_membership(db, index_name, anchor=date.today())
    ever = union_over(intervals, WINDOW_START, WINDOW_END)
    price_cols = [c for c in db.list_collection_names() if c.startswith("prices_")]
    priced = set()
    for c in price_cols:
        priced |= set(db[c].distinct("symbol", {"symbol": {"$in": list(ever)}}))
    return db, sorted(ever - priced)


def ensure_mappings(db, symbols: list[str]) -> int:
    coll = db.symbol_mappings
    have = set(coll.distinct("symbol", {"symbol": {"$in": symbols}}))
    top = coll.find_one(sort=[("nse_scrip_code", -1)])
    nxt = max(9500000, (top.get("nse_scrip_code", 0) if top else 0) + 1)
    inserted = 0
    for s in symbols:
        if s in have:
            continue
        coll.replace_one({"_id": s}, {
            "_id": s, "company_name": s, "symbol": s, "industry": "Unknown",
            "index_names": ["NIFTY500"], "nse_scrip_code": nxt, "nse_symbol": s,
            "nse_name": None, "match_confidence": "synthetic_backfill",
            "last_updated": datetime.utcnow(),
        }, upsert=True)
        nxt += 1
        inserted += 1
    return inserted


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="NIFTY500")
    ap.add_argument("--delay", type=float, default=0.6, help="seconds between fetches (rate-limit pacing)")
    args = ap.parse_args()

    db, missing = compute_missing(args.index)
    print(f"{args.index}: {len(missing)} ever-members missing prices", flush=True)
    if not missing:
        print("nothing to backfill")
        return
    print(f"ensured synthetic mappings: {ensure_mappings(db, missing)} inserted", flush=True)

    start, end = datetime(2005, 1, 1), datetime(WINDOW_END.year, WINDOW_END.month, WINDOW_END.day)
    ok, fail, failed = 0, 0, []
    async with StockDataManager() as sm:
        for i, sym in enumerate(missing, 1):
            try:
                res = await sm.download_historical_data_for_symbol(
                    symbol=sym, start_date=start, end_date=end, force_refresh=True
                )
                if isinstance(res, dict) and res.get("error"):
                    fail += 1; failed.append((sym, str(res["error"])[:60]))
                else:
                    ok += 1
            except Exception as e:  # noqa: BLE001 - one bad symbol must not abort the batch
                fail += 1; failed.append((sym, str(e)[:60]))
            if i % 20 == 0 or i == len(missing):
                print(f"  {i}/{len(missing)} (ok={ok} fail={fail})", flush=True)
            await asyncio.sleep(args.delay)

    print(f"DONE: loaded={ok} failed={fail}", flush=True)
    if failed:
        print("FAILED symbols (likely delisted / no TV data):", flush=True)
        for s, e in failed:
            print(f"  {s}: {e}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
