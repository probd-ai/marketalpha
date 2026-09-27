"""Oracle for the price-read refactor: exact records get_price_data returns today.
Run once before the change, once after; the two dumps must be byte-identical."""
import asyncio, hashlib, json, sys
from datetime import datetime
from stock_data_manager import StockDataManager

OUT = sys.argv[1]
START, END = datetime(2020, 1, 1), datetime(2026, 8, 28)

async def main():
    async with StockDataManager() as m:
        syms = sorted(s for s in await m.db["prices_2025_2029"].distinct("symbol")
                      if isinstance(s, str))[:30]
        dump = {}
        for s in syms:
            recs = await m.get_price_data(symbol=s, start_date=START, end_date=END,
                                          limit=10000, sort_order=1)
            dump[s] = [[r.date.isoformat(), float(r.close_price), float(r.open_price),
                        float(r.high_price), float(r.low_price),
                        int(r.volume) if r.volume else 0] for r in recs]
    blob = json.dumps(dump, sort_keys=True)
    open(OUT, "w").write(blob)
    print(f"{len(dump)} symbols, {sum(len(v) for v in dump.values())} rows")
    print("sha256", hashlib.sha256(blob.encode()).hexdigest())

asyncio.run(main())
