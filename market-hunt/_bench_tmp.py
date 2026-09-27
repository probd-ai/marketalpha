"""Micro-benchmark: current per-symbol get_price_data vs a deduped direct read."""
import asyncio, time
from datetime import datetime
import motor.motor_asyncio
from settings import MONGO_URI, DB_NAME
from stock_data_manager import StockDataManager

START, END = datetime(2020,1,1), datetime(2026,8,28)

async def main():
    db = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)[DB_NAME]
    syms = await db["prices_2025_2029"].distinct("symbol")
    syms = sorted(s for s in syms if isinstance(s,str))[:20]

    # how many times does the current code touch each partition?
    sm = StockDataManager()
    hits = {}
    for y in range(START.year, END.year+1):
        hits[sm._get_partition_collection_name(y)] = hits.get(sm._get_partition_collection_name(y),0)+1
    print("partition queries per symbol (current):", hits, "=", sum(hits.values()), "queries")
    print("create_index calls per symbol (current):", sum(hits.values())*5)
    print()

    # A) current path
    t=time.time()
    async with StockDataManager() as m:
        sem=asyncio.Semaphore(20)
        async def one(s):
            async with sem: return await m.get_price_data(symbol=s,start_date=START,end_date=END,limit=10000,sort_order=1)
        cur = await asyncio.gather(*[one(s) for s in syms])
    ta=time.time()-t
    print(f"A) current get_price_data      {len(syms)} syms  {ta:6.2f}s   rows={sum(len(x) for x in cur)}")

    # B) deduped partitions, no create_index, raw dicts, one query per partition for ALL symbols
    t=time.time()
    parts = sorted(set(hits))
    rows=0; out={}
    q={"symbol":{"$in":syms},"date":{"$gte":START,"$lte":END}}
    proj={"_id":0,"symbol":1,"date":1,"close_price":1,"open_price":1,"high_price":1,"low_price":1,"volume":1}
    for p in parts:
        async for d in db[p].find(q,proj):
            out.setdefault(d["symbol"],[]).append(d); rows+=1
    tb=time.time()-t
    print(f"B) batched $in, deduped parts  {len(syms)} syms  {tb:6.2f}s   rows={rows}")
    print(f"\nspeedup: {ta/tb:.1f}x on this slice")

asyncio.run(main())
