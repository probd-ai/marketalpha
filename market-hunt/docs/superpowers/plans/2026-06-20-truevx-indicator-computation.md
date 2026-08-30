# TrueVX Indicator Computation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-compute and store the TrueVX relative-strength indicator for all 848 priced symbols (ex-benchmark), keeping scores from 2018-01-01, so the strategy simulator can read them.

**Architecture:** A clean standalone CLI runner (`scripts/compute_indicators.py`) that fetches the `Nifty 50` benchmark once, iterates the universe, calls the existing proven `calculate_truevx_ranking` engine function, filters to ≥2018-01-01, and stores via `IndicatorDataManager`. Reuses validated math; replaces the buggy `batch_indicator_processor` path. Pure helpers are unit-tested; orchestration gets an integration smoke test against the running Mongo.

**Tech Stack:** Python 3, pymongo (sync, via existing managers), pandas/numpy (inside engine), pytest. Mongo at `mongodb://localhost:27017` db `market_hunt` (container `mh-mongo`).

## Global Constraints

- Run interpreter: `./.venv/Scripts/python.exe` from repo root `D:\gs\workspace\tvagent\market-hunt`.
- Windows console: prefix runs with `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`.
- Engine parameters are the locked defaults: `s1=22, m2=66, l3=222, strength=2, w_short=0.5, w_mid=1.0, w_long=1.5, deadband_frac=0.02, min_deadband=0.001`.
- `base_symbol = "Nifty 50"`, `indicator_type = "truevx"`.
- Fetch prices from `2016-01-01` (warmup); persist only rows dated `>= 2018-01-01`.
- Do NOT modify `indicator_engine.calculate_truevx_ranking` math.
- Commit only when the user asks; author "Arpitha B", no Co-Authored-By trailer.

---

### Task 1: Pure helpers + CLI scaffold

**Files:**
- Create: `scripts/compute_indicators.py`
- Test: `tests/test_compute_indicators.py`

**Interfaces:**
- Produces:
  - `DEFAULTS: dict` — engine params (values above).
  - `BENCHMARK = "Nifty 50"`, `INDICATOR = "truevx"`.
  - `resolve_universe(symbols: Iterable[str], benchmark: str = BENCHMARK) -> list[str]` — sorted unique, minus benchmark.
  - `filter_from_date(rows: list[dict], store_from: str) -> list[dict]` — keeps rows whose `row["date"]` (`"YYYY-MM-DD"`) is `>= store_from`.
  - `should_compute(existing_count: int, force: bool) -> bool` — `force or existing_count == 0`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compute_indicators.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.compute_indicators import resolve_universe, filter_from_date, should_compute, DEFAULTS

def test_resolve_universe_drops_benchmark_and_dedups_sorts():
    out = resolve_universe(["TCS", "RELIANCE", "Nifty 50", "TCS"], benchmark="Nifty 50")
    assert out == ["RELIANCE", "TCS"]

def test_filter_from_date_keeps_on_or_after_floor():
    rows = [{"date": "2017-12-29"}, {"date": "2018-01-01"}, {"date": "2018-06-10"}]
    assert filter_from_date(rows, "2018-01-01") == [{"date": "2018-01-01"}, {"date": "2018-06-10"}]

def test_should_compute_respects_force_and_existing():
    assert should_compute(0, False) is True
    assert should_compute(5, False) is False
    assert should_compute(5, True) is True

def test_defaults_match_locked_params():
    assert DEFAULTS == {"s1":22,"m2":66,"l3":222,"strength":2,
                        "w_short":0.5,"w_mid":1.0,"w_long":1.5,
                        "deadband_frac":0.02,"min_deadband":0.001}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -m pytest tests/test_compute_indicators.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.compute_indicators'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/compute_indicators.py
"""Compute & store the TrueVX indicator for all priced symbols (ex-benchmark)."""
from __future__ import annotations
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -m pytest tests/test_compute_indicators.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit** (only if user has asked to commit)

```bash
git add scripts/compute_indicators.py tests/test_compute_indicators.py
git commit -m "feat(indicators): pure helpers for TrueVX batch runner"
```

---

### Task 2: Fix `count_indicator_data` so resume works

**Files:**
- Modify: `indicator_data_manager.py:246-270` (the `count_indicator_data` method)
- Test: `tests/test_indicator_count.py`

**Interfaces:**
- Consumes: `IndicatorDataManager` (existing), `store_indicator_data`, `get_indicator_data`.
- Produces: `await IndicatorDataManager().count_indicator_data(symbol, indicator_type, base_symbol) -> int` returns the correct row count (currently raises `AttributeError`).

The bug: it references `self.indicator_data_collection` (nonexistent) and `await`s a sync pymongo call. Correct collection is `self.db[self.indicators_collection]`, and the count is synchronous.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_indicator_count.py
import asyncio
from indicator_data_manager import IndicatorDataManager

TEST_SYMBOL = "__TEST_TVX__"

def test_count_after_store_then_cleanup():
    async def run():
        async with IndicatorDataManager() as m:
            await m._delete_existing_data(TEST_SYMBOL, "truevx", "Nifty 50")
            await m.store_indicator_data(
                TEST_SYMBOL, "truevx", "Nifty 50",
                [{"date": "2024-01-01", "truevx_score": 50.0}], {"s1": 22})
            n = await m.count_indicator_data(TEST_SYMBOL, "truevx", "Nifty 50")
            await m._delete_existing_data(TEST_SYMBOL, "truevx", "Nifty 50")
            return n
    assert asyncio.run(run()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -m pytest tests/test_indicator_count.py -v`
Expected: FAIL — `AttributeError: 'IndicatorDataManager' object has no attribute 'indicator_data_collection'`

- [ ] **Step 3: Apply the fix**

Replace the body of `count_indicator_data` (the `try` block) so it uses the real collection and a synchronous count:

```python
        try:
            query = {
                "symbol": symbol,
                "indicator_type": indicator_type,
                "base_symbol": base_symbol,
            }
            return self.db[self.indicators_collection].count_documents(query)
        except Exception as e:
            logger.error(f"❌ Failed to count indicator data for {symbol}: {e}")
            return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -m pytest tests/test_indicator_count.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit** (only if user has asked to commit)

```bash
git add indicator_data_manager.py tests/test_indicator_count.py
git commit -m "fix(indicators): correct collection + sync count in count_indicator_data"
```

---

### Task 3: Orchestration — benchmark-once fetch, per-symbol compute/store, resume, summary

**Files:**
- Modify: `scripts/compute_indicators.py` (add async orchestration + `main`)
- Test: `tests/test_compute_indicators_integration.py`

**Interfaces:**
- Consumes: `StockDataManager.get_price_data(symbol, start_date, sort_order=1) -> list[PriceData]`; the module-level `indicator_engine.calculate_truevx_ranking(target_data, benchmark_data, **params) -> list[dict]`; `IndicatorDataManager.store_indicator_data(...)`, `.count_indicator_data(...)`.
- Produces:
  - `_to_engine_rows(records) -> list[dict]` — maps `PriceData` to engine dict shape.
  - `async fetch_prices(stock_mgr, symbol, start) -> list[dict]`
  - `async load_priced_symbols(stock_mgr) -> list[str]` — union of distinct symbols across `prices_*`.
  - `async compute_for_symbol(stock_mgr, ind_mgr, symbol, benchmark_rows, start_fetch, store_from) -> tuple[str,int]` — status ∈ {"ok","insufficient","empty"}, rows stored.
  - `async run(start_fetch, store_from, symbols=None, force=False, limit=None) -> dict` — summary `{ok, insufficient, empty, failed:list}`.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/test_compute_indicators_integration.py
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.compute_indicators import run, BENCHMARK, INDICATOR
from indicator_data_manager import IndicatorDataManager

def _count(sym):
    async def c():
        async with IndicatorDataManager() as m:
            return await m.count_indicator_data(sym, INDICATOR, BENCHMARK)
    return asyncio.run(c())

def test_run_one_symbol_stores_scores_from_2018():
    summary = asyncio.run(run(start_fetch="2016-01-01", store_from="2018-01-01",
                              symbols=["RELIANCE"], force=True))
    assert summary["ok"] == 1
    assert summary["failed"] == []
    assert _count("RELIANCE") > 0

    # date floor + score range
    async def check():
        async with IndicatorDataManager() as m:
            rows = await m.get_indicator_data("RELIANCE", INDICATOR, BENCHMARK)
            return rows
    rows = asyncio.run(check())
    assert min(r["date"] for r in rows) >= "2018-01-01"
    assert all(0.0 <= r["truevx_score"] <= 100.0 for r in rows)

def test_rerun_is_idempotent():
    n1 = _count("RELIANCE")
    asyncio.run(run(start_fetch="2016-01-01", store_from="2018-01-01",
                    symbols=["RELIANCE"], force=True))
    n2 = _count("RELIANCE")
    assert n1 == n2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -m pytest tests/test_compute_indicators_integration.py -v`
Expected: FAIL — `ImportError: cannot import name 'run'`

- [ ] **Step 3: Add the orchestration**

Append to `scripts/compute_indicators.py`:

```python
import argparse
import asyncio
import logging
from datetime import datetime

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
        for s in stock_mgr.db[cname].distinct("symbol"):
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
                summary[status] += 1 if status in summary else 0
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest tests/test_compute_indicators_integration.py -v`
Expected: PASS (2 passed). (Requires `mh-mongo` running with RELIANCE + Nifty 50 prices — already loaded.)

- [ ] **Step 5: Commit** (only if user has asked to commit)

```bash
git add scripts/compute_indicators.py tests/test_compute_indicators_integration.py
git commit -m "feat(indicators): full-universe TrueVX runner with resume + summary"
```

---

### Task 4: Smoke run, then full-universe run + coverage validation

**Files:** none (operational). Uses `scripts/compute_indicators.py`.

- [ ] **Step 1: Smoke test on 5 symbols**

Run: `PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/compute_indicators.py --limit 5 --force`
Expected: log shows 5 symbols processed, `ok` count > 0, `failed=0`. Note wall-clock per symbol to estimate the full run.

- [ ] **Step 2: Full universe run** (run in background; ~848 symbols)

Run: `PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/compute_indicators.py`
Expected: final `DONE ok=... failed=...`. Retry any failures: `--symbols A,B,C`.

- [ ] **Step 3: Validate coverage in Mongo**

Run:
```bash
docker exec mh-mongo mongosh --quiet --eval '
const db=db.getSiblingDB("market_hunt");
const haveIdx=new Set(db.indicators.distinct("symbol",{indicator_type:"truevx",base_symbol:"Nifty 50"}));
let priced=new Set();
db.getCollectionNames().filter(c=>/^prices_/.test(c)).forEach(c=>db.getCollection(c).distinct("symbol").forEach(s=>priced.add(s)));
priced.delete("Nifty 50");
const missing=[...priced].filter(s=>!haveIdx.has(s));
print("priced(ex-benchmark)="+priced.size+" with TrueVX="+haveIdx.size+" missing="+missing.length);
if(missing.length) print("missing: "+JSON.stringify(missing.slice(0,40)));
const r=db.indicators.findOne({symbol:"RELIANCE",indicator_type:"truevx"},{},{sort:{date:1}});
print("RELIANCE earliest indicator date: "+(r?r.date.toISOString().slice(0,10):"NONE"));
'
```
Expected: `with TrueVX` ≈ priced count, `missing` is only genuine insufficient-data symbols (e.g. brand-new IPOs with < 222 bars since 2016); RELIANCE earliest date ≥ `2018-01-01`.

- [ ] **Step 4: Commit** (only if user has asked) — no code change; nothing to commit unless retries changed files.

---

## Self-Review

**Spec coverage:** clean runner (Task 3), benchmark-once (Task 3 `run`), warmup-2016/store-2018 (Tasks 3–4 defaults + `filter_from_date`), resume/`--force` (Task 3 + Task 2 fix), per-symbol error capture/summary (Task 3 `run`), storage unchanged (uses existing `store_indicator_data`), verification (Task 4). All spec sections mapped.

**Placeholder scan:** no TBD/TODO; every code/test step shows full code; commands have expected output.

**Type consistency:** `DEFAULTS`, `BENCHMARK`, `INDICATOR`, `resolve_universe`, `filter_from_date`, `should_compute` defined in Task 1 and used consistently in Task 3; `count_indicator_data` signature fixed in Task 2 matches the call in Task 3; engine `calculate_truevx_ranking(target, benchmark, **DEFAULTS)` matches `indicator_engine.py:440` signature.
