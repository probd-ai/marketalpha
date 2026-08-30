"""Compute & store the TrueVX indicator for all priced symbols (ex-benchmark)."""
from __future__ import annotations
import os as _os
import sys as _sys
# Allow running as `python scripts/compute_indicators.py` from the repo root:
# put the repo root (parent of scripts/) on the path so root-level modules import.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from typing import Iterable

DEFAULTS = {
    "s1": 22, "m2": 66, "l3": 222, "strength": 2,
    "w_short": 0.5, "w_mid": 1.0, "w_long": 1.5,
    "deadband_frac": 0.02, "min_deadband": 0.001,
}
BENCHMARK = "Nifty 50"
INDICATOR = "truevx"


def resolve_universe(symbols: Iterable[str], benchmark: str = BENCHMARK) -> list[str]:
    return sorted({s for s in symbols if s != benchmark})


def filter_from_date(rows: list[dict], store_from: str) -> list[dict]:
    return [r for r in rows if r["date"] >= store_from]


def should_compute(existing_count: int, force: bool) -> bool:
    return force or existing_count == 0


import argparse
import asyncio
import logging
from datetime import datetime

from indicator_data_manager import IndicatorDataManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("compute_indicators")


def _to_engine_rows(records) -> list[dict]:
    return [{
        "date": r.date.isoformat(),
        "open_price": r.open_price,
        "high_price": r.high_price,
        "low_price": r.low_price,
        "close_price": r.close_price,
        "volume": r.volume,
    } for r in records]


async def fetch_prices(stock_mgr, symbol: str, start: datetime) -> list[dict]:
    recs = await stock_mgr.get_price_data(symbol=symbol, start_date=start, sort_order=1)
    return _to_engine_rows(recs)


async def load_priced_symbols(stock_mgr) -> list[str]:
    names = await stock_mgr.get_all_price_collections()
    found: set[str] = set()
    for cname in names:
        # StockDataManager.db is async (motor) -> distinct() must be awaited.
        for s in await stock_mgr.db[cname].distinct("symbol"):
            found.add(s)
    return sorted(found)


async def compute_for_symbol(stock_mgr, ind_mgr, symbol, benchmark_rows, start_fetch, store_from):
    from indicator_engine import calculate_truevx_ranking
    target = await fetch_prices(stock_mgr, symbol, start_fetch)
    if len(target) < DEFAULTS["l3"]:
        return ("insufficient", 0)
    rows = calculate_truevx_ranking(target, benchmark_rows, **DEFAULTS)
    rows = filter_from_date(rows, store_from)
    if not rows:
        return ("empty", 0)
    await ind_mgr.store_indicator_data(symbol, INDICATOR, BENCHMARK, rows, DEFAULTS)
    return ("ok", len(rows))


async def run(start_fetch="2016-01-01", store_from="2018-01-01",
              symbols=None, force=False, limit=None) -> dict:
    from stock_data_manager import StockDataManager
    start_dt = datetime.fromisoformat(start_fetch)
    summary = {"ok": 0, "insufficient": 0, "empty": 0, "failed": []}

    async with StockDataManager() as stock_mgr, IndicatorDataManager() as ind_mgr:
        benchmark_rows = await fetch_prices(stock_mgr, BENCHMARK, start_dt)
        if not benchmark_rows:
            raise RuntimeError(f"No benchmark price data for {BENCHMARK}")

        universe = symbols if symbols else resolve_universe(await load_priced_symbols(stock_mgr))
        if limit:
            universe = universe[:limit]

        total = len(universe)
        for i, sym in enumerate(universe, 1):
            try:
                existing = await ind_mgr.count_indicator_data(sym, INDICATOR, BENCHMARK)
                if not should_compute(existing, force):
                    logger.info(f"[{i}/{total}] skip {sym} (have {existing})")
                    continue
                status, n = await compute_for_symbol(
                    stock_mgr, ind_mgr, sym, benchmark_rows, start_dt, store_from)
                if status in summary:
                    summary[status] += 1
                logger.info(f"[{i}/{total}] {sym}: {status} ({n} rows)")
            except Exception as e:
                summary["failed"].append(sym)
                logger.error(f"[{i}/{total}] {sym}: FAILED {e}")

    logger.info(f"DONE ok={summary['ok']} insufficient={summary['insufficient']} "
                f"empty={summary['empty']} failed={len(summary['failed'])}")
    if summary["failed"]:
        logger.info("failed symbols: " + ",".join(summary["failed"]))
    return summary


def main():
    p = argparse.ArgumentParser(description="Compute TrueVX indicators for priced symbols")
    p.add_argument("--start-fetch", default="2016-01-01")
    p.add_argument("--store-from", default="2018-01-01")
    p.add_argument("--symbols", default=None, help="comma-separated subset/retry list")
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args()
    syms = [s.strip() for s in a.symbols.split(",")] if a.symbols else None
    asyncio.run(run(start_fetch=a.start_fetch, store_from=a.store_from,
                    symbols=syms, force=a.force, limit=a.limit))


if __name__ == "__main__":
    main()
