#!/usr/bin/env python3
"""Fetch small/micro/mid-cap NSE index benchmark series from TradingView (tvdatafeed)
and store them as priced symbols, so the simulator can benchmark against them.

Run with the project venv: .venv/Scripts/python.exe load_index_benchmarks.py
Idempotent: store_price_data upserts on (symbol, date)."""
import asyncio
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("load_index_benchmarks")

from tv_price_client import TvPriceClient
from stock_data_manager import StockDataManager

# (stored symbol name, synthetic scrip_code). Name must match INDEX_TV_SYMBOLS in tv_price_client.
TARGETS = [
    ("Nifty 100",          900100),
    ("Nifty Smallcap 250", 900250),
    ("Nifty Microcap 250", 900251),
    ("Nifty Midcap 150",   900150),
]

START = datetime(2005, 1, 1)
END = datetime.now()


async def main():
    client = TvPriceClient()
    async with StockDataManager() as m:
        for name, scrip in TARGETS:
            log.info(f"=== {name} (scrip {scrip}) ===")
            data = await client.fetch_historical_data(scrip, name, START, END)
            if not data:
                log.error(f"NO DATA for {name} — skipping")
                continue
            res = await m.store_price_data(data)
            dmin = min(d.date for d in data).date()
            dmax = max(d.date for d in data).date()
            log.info(f"{name}: {len(data)} bars {dmin}..{dmax}  store={res}")


if __name__ == "__main__":
    asyncio.run(main())
