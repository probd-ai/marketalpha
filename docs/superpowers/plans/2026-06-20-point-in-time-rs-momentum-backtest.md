# Point-in-Time RS-Momentum Backtest + Bias Measurement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run tvagent's existing RS-Momentum engine on real, already-loaded MongoDB prices, point-in-time correct over NIFTY50, and emit a biased-vs-point-in-time delta that quantifies survivorship/look-ahead bias.

**Architecture:** Add a read-only `MongoDataSource` implementing the existing `DataSource` protocol so the engine reads market-hunt's MongoDB OHLC with no re-fetch. Add a `BiasedUniverseProvider` (today's list at every date) alongside the existing point-in-time `IndexMembershipProvider`. A comparison harness runs `BacktestEngine` twice — swapping only the provider — and reports the delta plus a price-coverage report. Verify NIFTY50 membership data so the point-in-time run is real.

**Tech Stack:** Python 3.12, pandas, pymongo (sync — matches the sync engine), pytest. MongoDB at `mongodb://localhost:27017/`, database `market_hunt` (market-hunt's store).

## Global Constraints

- Production-grade from line one; close issues at the source, no "fix later" caveats. (project CLAUDE.md)
- TDD: failing test first, minimal implementation, verify, commit. Frequent commits.
- `MongoDataSource` is strictly **read-only** — never write to or mutate market-hunt data.
- No fabricated data: changelog `source_url` values and historical membership facts are gathered from real niftyindices.com sources at execution time, never invented. Name→symbol mapping derived from the NSE scrip master, never guessed.
- Commits authored "Arpitha B"; **no `Co-Authored-By` trailer**. Commit only the files listed per task.
- Tests that touch Mongo run against a **real** MongoDB (a throwaway `market_hunt_test` db), not a mock — per the spec's "no mock that masks query behavior." Prerequisite: `docker start mh-mongo` (:27017).
- Python invocations on Windows: prefix with `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` if the process prints non-ASCII.
- Run all `pytest`/`python` commands from `backend/` (its `pyproject.toml` sets `pythonpath=["."]`).

## Scope

**In scope (this plan):** MongoDataSource, BiasedUniverseProvider, coverage report, comparison harness + CLI, and **NIFTY50** verified membership data → a real NIFTY50 biased-vs-PIT delta.

**Out of scope (follow-on plan):** NIFTY500 membership curation (needs a niftyindices fetchability spike) and yfinance/tvdatafeed backfill of since-removed names (gated on this plan's coverage report). These are a separate data-acquisition subsystem.

## Reference — existing interfaces (do not change)

- `DataSource` protocol (`backend/app/data/sources/base.py`): `get_ohlc(symbol, exchange, interval, start, end) -> pd.DataFrame` with a `close` column.
- `UniverseProvider` protocol (`backend/app/data/universe/provider.py`): `members_as_of(index, on) -> set[str]`, `union_over(index, start, end) -> set[str]`.
- `BacktestEngine.run(cfg) -> BacktestResult` (`backend/app/backtest/engine.py`) already gates selection on `members_as_of` and fetches the panel via `union_over` + `get_ohlc`. `BacktestResult` fields: `equity`, `benchmark`, `metrics`, `holdings`, `warnings`.
- `BacktestConfig` (`backend/app/strategies/base.py`): `index, start, end, starting_capital, lookback, top_pct, top_n, nominal_size, cost_bps`.
- `load_provider_from_seed(seed_dir, anchor) -> IndexMembershipProvider` reads `*_current.csv` (column `symbol`) and `*_changelog.csv` (`effective_date, added, removed`, `;`-separated symbols).

## market-hunt Mongo price-doc schema (the adapter must match)

Collections `prices_YYYY_YYYY` (5-yr partitions: `prices_2005_2009` … `prices_2025_2029`). Each doc (from `asdict(PriceData)`):
`_id` = `"{symbol}_{YYYYMMDD}"`, `scrip_code:int`, `symbol:str`, `date:datetime`, `open_price:float`, `high_price:float`, `low_price:float`, `close_price:float`, `volume:int`, `value:float`, `year_partition:int`, `last_updated:datetime`. Index symbols are stored under their display name (e.g. `symbol == "Nifty 50"`).

## File structure

- Create `backend/app/data/sources/mongo_source.py` — `MongoDataSource`, `INDEX_SYMBOL_MAP`.
- Create `backend/tests/data/test_mongo_source.py`.
- Modify `backend/app/core/config.py` — add `mongo_uri`, `mongo_db`.
- Modify `backend/pyproject.toml` — add a `mongo` optional-dependency group with `pymongo`.
- Modify `backend/app/data/universe/provider.py` — add `BiasedUniverseProvider`, `load_biased_provider_from_seed`.
- Modify `backend/tests/data/test_provider.py` — biased provider tests.
- Create `backend/app/backtest/coverage.py` — `CoverageReport`, `coverage_report`.
- Create `backend/tests/backtest/test_coverage.py`.
- Create `backend/app/backtest/compare.py` — `ComparisonResult`, `run_comparison`.
- Create `backend/tests/backtest/test_compare.py`.
- Create `backend/app/run_compare.py` — CLI.
- Create `backend/data/seed/nifty50_current.csv` (verified) and overwrite `backend/data/seed/nifty50_changelog.csv` (verified `source_url`s).

---

### Task 1: `MongoDataSource` price adapter

**Files:**
- Create: `backend/app/data/sources/mongo_source.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/data/test_mongo_source.py`

**Interfaces:**
- Consumes: `DataSource` protocol shape from `app.data.sources.base`.
- Produces: `MongoDataSource(uri: str, db_name: str, index_symbol_map: Mapping[str,str] | None = None, client=None)` with `get_ohlc(symbol, exchange, interval, start, end) -> pd.DataFrame` (columns `open,high,low,close,volume`; tz-naive `DatetimeIndex`); module constant `INDEX_SYMBOL_MAP = {"NIFTY": "Nifty 50"}`.

- [ ] **Step 1: Add pymongo dependency**

In `backend/pyproject.toml`, under `[project.optional-dependencies]`, add:

```toml
mongo = ["pymongo>=4.7"]
```

Then install into the backend venv:

Run (from `backend/`): `pip install -e ".[mongo,dev]"`
Expected: pymongo installs successfully.

- [ ] **Step 2: Add Mongo settings**

In `backend/app/core/config.py`, add two fields to `Settings` (after `tv_password`):

```python
    mongo_uri: str = "mongodb://localhost:27017/"
    mongo_db: str = "market_hunt"
```

- [ ] **Step 3: Write the failing test**

Create `backend/tests/data/test_mongo_source.py`:

```python
from datetime import date, datetime
import pytest
from pymongo import MongoClient
from app.data.sources.mongo_source import MongoDataSource, INDEX_SYMBOL_MAP

TEST_DB = "market_hunt_test"


def _doc(symbol, d: datetime, close: float, scrip=1):
    return {
        "_id": f"{symbol}_{d.strftime('%Y%m%d')}",
        "scrip_code": scrip, "symbol": symbol, "date": d,
        "open_price": close - 1, "high_price": close + 1,
        "low_price": close - 2, "close_price": close,
        "volume": 1000, "value": close * 1000,
        "year_partition": d.year, "last_updated": d,
    }


@pytest.fixture
def mongo():
    client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2000)
    client.admin.command("ping")  # fail fast if Mongo is down
    db = client[TEST_DB]
    db["prices_2020_2024"].insert_many([
        _doc("RELIANCE", datetime(2021, 6, 1), 100.0),
        _doc("RELIANCE", datetime(2021, 6, 2), 101.0),
        _doc("TCS", datetime(2021, 6, 1), 50.0),
    ])
    db["prices_2025_2029"].insert_many([
        _doc("RELIANCE", datetime(2025, 1, 2), 200.0),
    ])
    db["prices_2020_2024"].insert_one(
        {**_doc("Nifty 50", datetime(2021, 6, 1), 15000.0)}
    )
    yield client
    client.drop_database(TEST_DB)
    client.close()


def test_get_ohlc_maps_columns_and_filters_range(mongo):
    src = MongoDataSource("mongodb://localhost:27017/", TEST_DB)
    df = src.get_ohlc("RELIANCE", "NSE", "daily", date(2021, 1, 1), date(2021, 12, 31))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [100.0, 101.0]   # 2025 row excluded by range
    assert str(df.index.tz) == "None"               # tz-naive
    assert df.index[0] == __import__("pandas").Timestamp("2021-06-01")


def test_get_ohlc_spans_partitions(mongo):
    src = MongoDataSource("mongodb://localhost:27017/", TEST_DB)
    df = src.get_ohlc("RELIANCE", "NSE", "daily", date(2021, 1, 1), date(2025, 12, 31))
    assert df["close"].tolist() == [100.0, 101.0, 200.0]


def test_get_ohlc_missing_symbol_returns_empty(mongo):
    src = MongoDataSource("mongodb://localhost:27017/", TEST_DB)
    df = src.get_ohlc("NOSUCH", "NSE", "daily", date(2021, 1, 1), date(2021, 12, 31))
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_index_symbol_mapping(mongo):
    src = MongoDataSource("mongodb://localhost:27017/", TEST_DB, INDEX_SYMBOL_MAP)
    df = src.get_ohlc("NIFTY", "NSE", "daily", date(2021, 1, 1), date(2021, 12, 31))
    assert df["close"].tolist() == [15000.0]
```

- [ ] **Step 4: Run the test to verify it fails**

Run (from `backend/`): `pytest tests/data/test_mongo_source.py -v`
Expected: FAIL — `ModuleNotFoundError: app.data.sources.mongo_source` (or import error). If it fails with a Mongo connection error instead, run `docker start mh-mongo` first.

- [ ] **Step 5: Implement `MongoDataSource`**

Create `backend/app/data/sources/mongo_source.py`:

```python
from datetime import date, datetime
from typing import Mapping
import pandas as pd
from pymongo import MongoClient

INDEX_SYMBOL_MAP: dict[str, str] = {"NIFTY": "Nifty 50"}

_COL_MAP = {
    "open_price": "open",
    "high_price": "high",
    "low_price": "low",
    "close_price": "close",
}
_OUT_COLS = ["open", "high", "low", "close", "volume"]


class MongoDataSource:
    """Read-only DataSource over market-hunt's MongoDB price store."""

    def __init__(self, uri: str, db_name: str,
                 index_symbol_map: Mapping[str, str] | None = None,
                 client: MongoClient | None = None) -> None:
        self._client = client or MongoClient(uri)
        self._db = self._client[db_name]
        self._index_map = dict(index_symbol_map or {})

    def _price_collections(self) -> list[str]:
        return sorted(c for c in self._db.list_collection_names()
                      if c.startswith("prices_"))

    def get_ohlc(self, symbol: str, exchange: str, interval: str,
                 start: date, end: date) -> pd.DataFrame:
        resolved = self._index_map.get(symbol, symbol)
        start_dt = datetime(start.year, start.month, start.day)
        end_dt = datetime(end.year, end.month, end.day, 23, 59, 59)
        rows: list[dict] = []
        for coll in self._price_collections():
            rows.extend(self._db[coll].find(
                {"symbol": resolved, "date": {"$gte": start_dt, "$lte": end_dt}}
            ))
        if not rows:
            return pd.DataFrame(columns=_OUT_COLS)
        df = pd.DataFrame(rows).rename(columns=_COL_MAP)
        # market-hunt stores bars as UTC datetimes with an intraday time (e.g. 09:15);
        # pymongo returns them tz-naive. Normalize to a clean tz-naive daily index.
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None).dt.normalize()
        df = df.set_index("date").sort_index()
        return df[_OUT_COLS]
```

- [ ] **Step 6: Run the test to verify it passes**

Run (from `backend/`): `pytest tests/data/test_mongo_source.py -v`
Expected: 4 passed. (If connection error: `docker start mh-mongo`, then re-run.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/data/sources/mongo_source.py backend/tests/data/test_mongo_source.py backend/app/core/config.py backend/pyproject.toml
git commit -m "feat(data): read-only MongoDataSource over market-hunt price store"
```

---

### Task 2: `BiasedUniverseProvider`

**Files:**
- Modify: `backend/app/data/universe/provider.py`
- Test: `backend/tests/data/test_provider.py`

**Interfaces:**
- Consumes: `UniverseProvider` protocol; `*_current.csv` seed format (column `symbol`).
- Produces: `BiasedUniverseProvider(members_by_index: dict[str, set[str]])` (both methods return today's list regardless of date); `load_biased_provider_from_seed(seed_dir) -> BiasedUniverseProvider`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/data/test_provider.py`:

```python
from datetime import date
from app.data.universe.provider import BiasedUniverseProvider


def test_biased_provider_returns_current_list_at_every_date():
    p = BiasedUniverseProvider({"NIFTY50": {"A", "B"}})
    assert p.members_as_of("NIFTY50", date(2019, 1, 1)) == {"A", "B"}
    assert p.members_as_of("NIFTY50", date(2026, 1, 1)) == {"A", "B"}
    assert p.union_over("NIFTY50", date(2019, 1, 1), date(2026, 1, 1)) == {"A", "B"}


def test_biased_provider_returns_independent_copies():
    p = BiasedUniverseProvider({"NIFTY50": {"A"}})
    got = p.members_as_of("NIFTY50", date(2020, 1, 1))
    got.add("Z")
    assert p.members_as_of("NIFTY50", date(2020, 1, 1)) == {"A"}  # not mutated
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `backend/`): `pytest tests/data/test_provider.py -k biased -v`
Expected: FAIL — `ImportError: cannot import name 'BiasedUniverseProvider'`.

- [ ] **Step 3: Implement the provider**

Add to `backend/app/data/universe/provider.py` (after `IndexMembershipProvider`):

```python
class BiasedUniverseProvider:
    """Survivorship-biased provider: returns today's constituents at every date."""

    def __init__(self, members_by_index: dict[str, set[str]]) -> None:
        self._by_index = {k: set(v) for k, v in members_by_index.items()}

    def members_as_of(self, index: str, on: date) -> set[str]:
        return set(self._by_index.get(index, set()))

    def union_over(self, index: str, start: date, end: date) -> set[str]:
        return set(self._by_index.get(index, set()))


def load_biased_provider_from_seed(seed_dir: Path) -> BiasedUniverseProvider:
    seed_dir = Path(seed_dir)
    by_index: dict[str, set[str]] = {}
    for current_csv in seed_dir.glob("*_current.csv"):
        index_key = current_csv.name[: -len("_current.csv")].upper()
        raw = pd.read_csv(current_csv)["symbol"].dropna().astype(str).str.strip()
        by_index[index_key] = {s for s in raw if s and s.lower() != "nan"}
    return BiasedUniverseProvider(by_index)
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `backend/`): `pytest tests/data/test_provider.py -k biased -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/data/universe/provider.py backend/tests/data/test_provider.py
git commit -m "feat(universe): BiasedUniverseProvider (today's list at every date) for bias comparison"
```

---

### Task 3: Price-coverage report

**Files:**
- Create: `backend/app/backtest/coverage.py`
- Test: `backend/tests/backtest/test_coverage.py`

**Interfaces:**
- Consumes: any `DataSource` (`get_ohlc`).
- Produces: `@dataclass CoverageReport(total:int, covered:list[str], missing:list[str])`; `coverage_report(source, symbols: set[str], exchange: str, start: date, end: date) -> CoverageReport` — a symbol is "covered" iff `get_ohlc` returns a non-empty frame over the window.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/backtest/test_coverage.py`:

```python
from datetime import date
import pandas as pd
from app.backtest.coverage import coverage_report, CoverageReport


class FakeSource:
    def __init__(self, available: set[str]):
        self._available = available

    def get_ohlc(self, symbol, exchange, interval, start, end):
        if symbol in self._available:
            return pd.DataFrame({"close": [1.0, 2.0]},
                                index=pd.to_datetime(["2021-01-01", "2021-01-02"]))
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def test_coverage_splits_covered_and_missing():
    src = FakeSource({"A", "C"})
    rep = coverage_report(src, {"A", "B", "C"}, "NSE", date(2021, 1, 1), date(2021, 12, 31))
    assert isinstance(rep, CoverageReport)
    assert rep.total == 3
    assert rep.covered == ["A", "C"]   # sorted
    assert rep.missing == ["B"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `backend/`): `pytest tests/backtest/test_coverage.py -v`
Expected: FAIL — `ModuleNotFoundError: app.backtest.coverage`.

- [ ] **Step 3: Implement coverage**

Create `backend/app/backtest/coverage.py`:

```python
from dataclasses import dataclass
from datetime import date


@dataclass
class CoverageReport:
    total: int
    covered: list[str]
    missing: list[str]


def coverage_report(source, symbols: set[str], exchange: str,
                    start: date, end: date) -> CoverageReport:
    covered, missing = [], []
    for s in sorted(symbols):
        df = source.get_ohlc(s, exchange, "daily", start, end)
        (covered if not df.empty else missing).append(s)
    return CoverageReport(total=len(symbols), covered=covered, missing=missing)
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `backend/`): `pytest tests/backtest/test_coverage.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/backtest/coverage.py backend/tests/backtest/test_coverage.py
git commit -m "feat(backtest): price-coverage report over the ever-a-member symbol set"
```

---

### Task 4: Comparison harness + CLI

**Files:**
- Create: `backend/app/backtest/compare.py`
- Create: `backend/app/run_compare.py`
- Test: `backend/tests/backtest/test_compare.py`

**Interfaces:**
- Consumes: `BacktestEngine`, `BacktestConfig`, `RSMomentumStrategy`, `coverage_report`, both providers, a `DataSource`.
- Produces: `@dataclass ComparisonResult(biased, point_in_time, delta:dict, coverage:CoverageReport)`; `run_comparison(source, pit_provider, biased_provider, strategy, cfg, index_symbol="NIFTY", exchange="NSE") -> ComparisonResult`. `delta` keys: `biased_final`, `pit_final`, `final_abs`, `final_pct` (= `(biased_final - pit_final) / pit_final`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/backtest/test_compare.py`. This builds a case where a future-added stock `LATE` is point-in-time excluded before its membership date, so the biased run can pick it and the PIT run cannot — producing different selections:

```python
from datetime import date
import numpy as np
import pandas as pd
from app.strategies.rs_momentum import RSMomentumStrategy
from app.data.universe.provider import IndexMembershipProvider, BiasedUniverseProvider
from app.data.universe.membership import MembershipInterval
from app.backtest.compare import run_comparison, ComparisonResult
from app.strategies.base import BacktestConfig


def _series(idx, start, slope):
    return pd.Series(start + slope * np.arange(len(idx)), index=idx)


class DictSource:
    def __init__(self, panel: pd.DataFrame, nifty: pd.Series):
        self._panel, self._nifty = panel, nifty

    def get_ohlc(self, symbol, exchange, interval, start, end):
        if symbol == "NIFTY":
            s = self._nifty
        elif symbol in self._panel.columns:
            s = self._panel[symbol]
        else:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return pd.DataFrame({"open": s, "high": s, "low": s, "close": s, "volume": 1.0})


def test_comparison_excludes_future_member_in_pit_run():
    idx = pd.bdate_range("2021-01-01", "2021-12-31")
    # STEADY rises modestly; LATE rises fastest (a momentum magnet) but joins late.
    panel = pd.DataFrame({
        "STEADY": _series(idx, 100, 0.1),
        "OLD": _series(idx, 100, 0.05),
        "LATE": _series(idx, 100, 1.0),
    }, index=idx)
    nifty = _series(idx, 100, 0.02)
    src = DictSource(panel, nifty)

    current = {"STEADY", "OLD", "LATE"}
    pit = IndexMembershipProvider({"NIFTY50": [
        MembershipInterval("STEADY", None, None),
        MembershipInterval("OLD", None, None),
        MembershipInterval("LATE", date(2021, 11, 1), None),  # added late
    ]})
    biased = BiasedUniverseProvider({"NIFTY50": current})
    cfg = BacktestConfig(index="NIFTY50", start=date(2021, 1, 1), end=date(2021, 12, 31),
                         top_pct=1.0, top_n=1)

    res = run_comparison(src, pit, biased, RSMomentumStrategy(), cfg)
    assert isinstance(res, ComparisonResult)
    # Biased picks LATE from the first rebalance; PIT cannot until Nov.
    biased_early = res.biased.holdings[0][1]
    pit_early = res.point_in_time.holdings[0][1]
    assert "LATE" in biased_early
    assert "LATE" not in pit_early
    assert res.coverage.total == 3 and res.coverage.missing == []
    assert set(res.delta) == {"biased_final", "pit_final", "final_abs", "final_pct"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `backend/`): `pytest tests/backtest/test_compare.py -v`
Expected: FAIL — `ModuleNotFoundError: app.backtest.compare`.

- [ ] **Step 3: Implement the harness**

Create `backend/app/backtest/compare.py`:

```python
from dataclasses import dataclass
from app.backtest.engine import BacktestEngine, BacktestResult
from app.backtest.coverage import coverage_report, CoverageReport
from app.strategies.base import BacktestConfig, Strategy
from app.data.sources.base import DataSource
from app.data.universe.provider import UniverseProvider


@dataclass
class ComparisonResult:
    biased: BacktestResult
    point_in_time: BacktestResult
    delta: dict
    coverage: CoverageReport


def run_comparison(source: DataSource, pit_provider: UniverseProvider,
                   biased_provider: UniverseProvider, strategy: Strategy,
                   cfg: BacktestConfig, index_symbol: str = "NIFTY",
                   exchange: str = "NSE") -> ComparisonResult:
    pit = BacktestEngine(source, pit_provider, strategy, index_symbol, exchange).run(cfg)
    biased = BacktestEngine(source, biased_provider, strategy, index_symbol, exchange).run(cfg)

    symbols = pit_provider.union_over(cfg.index, cfg.start, cfg.end)
    cov = coverage_report(source, symbols, exchange, cfg.start, cfg.end)

    b_final = float(biased.equity.iloc[-1]) if len(biased.equity) else 0.0
    p_final = float(pit.equity.iloc[-1]) if len(pit.equity) else 0.0
    delta = {
        "biased_final": b_final,
        "pit_final": p_final,
        "final_abs": b_final - p_final,
        "final_pct": (b_final - p_final) / p_final if p_final else float("nan"),
    }
    return ComparisonResult(biased=biased, point_in_time=pit, delta=delta, coverage=cov)
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `backend/`): `pytest tests/backtest/test_compare.py -v`
Expected: 1 passed.

- [ ] **Step 5: Add the CLI**

Create `backend/app/run_compare.py`:

```python
import argparse
from datetime import date, timedelta
from app.core.config import settings
from app.data.sources.mongo_source import MongoDataSource, INDEX_SYMBOL_MAP
from app.data.universe.provider import (
    load_provider_from_seed, load_biased_provider_from_seed,
)
from app.strategies.base import BacktestConfig
from app.strategies.rs_momentum import RSMomentumStrategy
from app.backtest.compare import run_comparison, ComparisonResult


def main(argv: list[str] | None = None) -> ComparisonResult:
    parser = argparse.ArgumentParser(description="Biased vs point-in-time RS-Momentum")
    parser.add_argument("--index", default="NIFTY50")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--cost-bps", type=float, default=0.0)
    args = parser.parse_args(argv)

    end = date.today()
    start = end - timedelta(days=365 * args.years)
    cfg = BacktestConfig(index=args.index, start=start, end=end,
                         starting_capital=args.capital, cost_bps=args.cost_bps)

    source = MongoDataSource(settings.mongo_uri, settings.mongo_db, INDEX_SYMBOL_MAP)
    pit = load_provider_from_seed(settings.seed_dir, anchor=end)
    biased = load_biased_provider_from_seed(settings.seed_dir)
    res = run_comparison(source, pit, biased, RSMomentumStrategy(), cfg)

    def show(name, r):
        print(f"\n=== {name} ===")
        for k, v in r.metrics.items():
            print(f"{k:>24}: {v:,.4f}" if isinstance(v, float) else f"{k:>24}: {v}")
        print(f"{'warnings':>24}: {len(r.warnings)}")

    show("BIASED (today's list)", res.biased)
    show("POINT-IN-TIME", res.point_in_time)
    print("\n=== DELTA ===")
    for k, v in res.delta.items():
        print(f"{k:>24}: {v:,.4f}")
    print(f"\nCoverage: {len(res.coverage.covered)}/{res.coverage.total} covered, "
          f"{len(res.coverage.missing)} missing")
    if res.coverage.missing:
        print("Missing (no prices in Mongo): " + ", ".join(res.coverage.missing))
    return res


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/backtest/compare.py backend/app/run_compare.py backend/tests/backtest/test_compare.py
git commit -m "feat(backtest): biased-vs-point-in-time comparison harness + run_compare CLI"
```

---

### Task 5: Verified NIFTY50 membership data

This task curates real data. **Do not fabricate** URLs or membership facts — gather them from niftyindices.com at execution time. The draft `nifty50_changelog.csv` already lists 8 events with dates; this task verifies each and fills real `source_url`s, then populates the current list.

**Files:**
- Create: `backend/data/seed/nifty50_current.csv`
- Overwrite: `backend/data/seed/nifty50_changelog.csv`
- Test: `backend/tests/data/test_nifty50_seed.py`

**Interfaces:**
- Consumes: `load_provider_from_seed` (already built).
- Produces: a verified seed that yields a non-empty NIFTY50 universe with correct point-in-time intervals.

- [ ] **Step 1: Build the current list**

Download the authoritative current constituents (URL recorded in `backend/data/seed/README.md`):
`https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv`.
Reduce to a single `symbol` column of tvdatafeed NSE trading symbols (the CSV's `Symbol` column is already the NSE ticker). Write `backend/data/seed/nifty50_current.csv` with header `symbol` and 50 rows. Do not hand-type tickers — derive from the downloaded file.

If the URL is blocked at execution, fall back to market-hunt's already-loaded NIFTY50 membership in Mongo (`index_meta`) as the current list, and record that provenance in the commit message.

- [ ] **Step 2: Verify the changelog**

For each of the 8 rows already in `backend/data/seed/nifty50_changelog.csv` (effective dates 2021-03-31 … 2025-09-30), confirm the add/remove against the niftyindices.com press release / index-maintenance announcement for that reconstitution, and replace the `VERIFY` token with the exact announcement URL. Map every company name in `added`/`removed` to its tvdatafeed NSE symbol using the NSE scrip master (e.g. Tata Consumer Products→`TATACONSUM`, Apollo Hospitals→`APOLLOHOSP`, Adani Enterprises→`ADANIENT`, LTIMindtree→`LTIM`, Shriram Finance→`SHRIRAMFIN`, Bharat Electronics→`BEL`, Trent→`TRENT`, Divi's Laboratories→`DIVISLAB`, Jio Financial Services→`JIOFIN`, Eternal→`ETERNAL`, InterGlobe Aviation→`INDIGO`, Max Healthcare→`MAXHEALTH`). Keep the `added`/`removed` cells `;`-separated symbols. Add any reconstitution events you find in the window that the draft missed.

Write the verified file with columns `effective_date,added,removed,source_url` and no `VERIFY` tokens remaining.

- [ ] **Step 3: Write the verification test**

Create `backend/tests/data/test_nifty50_seed.py`. Pick ONE verified event from Step 2 and assert the reconstruction around it. Example shape (fill `ADDED_SYMBOL` / `EVENT_DATE` from a verified row):

```python
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
from app.data.universe.provider import load_provider_from_seed

SEED = Path(__file__).resolve().parents[2] / "data" / "seed"


def test_nifty50_current_is_populated():
    rows = pd.read_csv(SEED / "nifty50_current.csv")["symbol"].dropna()
    assert len(rows) >= 45  # ~50 constituents


def test_changelog_has_no_unverified_rows():
    df = pd.read_csv(SEED / "nifty50_changelog.csv")
    assert (df["source_url"].astype(str).str.upper() != "VERIFY").all()
    assert df["source_url"].astype(str).str.startswith("http").all()


def test_added_stock_is_absent_before_and_present_after():
    # Fill these two constants from a single VERIFIED changelog row (Step 2):
    ADDED_SYMBOL = "JIOFIN"        # example — use a real verified add
    EVENT_DATE = date(2025, 3, 28) # its effective_date
    provider = load_provider_from_seed(SEED, anchor=date(2026, 6, 20))
    before = provider.members_as_of("NIFTY50", EVENT_DATE - timedelta(days=1))
    after = provider.members_as_of("NIFTY50", EVENT_DATE + timedelta(days=1))
    assert ADDED_SYMBOL not in before
    assert ADDED_SYMBOL in after
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `backend/`): `pytest tests/data/test_nifty50_seed.py -v`
Expected: 3 passed. If `test_added_stock...` fails, the changelog event or the chosen constants are wrong — re-check Step 2 against the source, do not weaken the assertion.

- [ ] **Step 5: Commit**

```bash
git add backend/data/seed/nifty50_current.csv backend/data/seed/nifty50_changelog.csv backend/tests/data/test_nifty50_seed.py
git commit -m "data(seed): verified NIFTY50 current list + reconstitution changelog"
```

---

### Task 6: End-to-end NIFTY50 comparison run (evidence)

**Files:** none created — this produces the deliverable and a recorded result.

- [ ] **Step 1: Ensure Mongo is up**

Run: `docker start mh-mongo`
Expected: container starts; `docker ps` shows it on :27017.

- [ ] **Step 2: Run the full suite**

Run (from `backend/`): `pytest -q`
Expected: all tests pass (existing + new). If any fail, stop and fix before running the comparison.

- [ ] **Step 3: Run the comparison on real data**

Run (from `backend/`): `python -m app.run_compare --index NIFTY50 --years 5`
Expected: prints BIASED metrics, POINT-IN-TIME metrics, the DELTA block, and a coverage line. Capture the output.

- [ ] **Step 4: Record the result**

Append a short "Results" section to the spec at
`docs/superpowers/specs/2026-06-20-point-in-time-rs-momentum-backtest-design.md` with: the biased vs PIT CAGR/Sharpe/maxDD, `final_pct` delta, and the coverage line (how many NIFTY50 ever-members lacked prices in Mongo). State honestly whether the bias is material for this strategy.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-06-20-point-in-time-rs-momentum-backtest-design.md
git commit -m "docs(spec): record NIFTY50 biased-vs-point-in-time results"
```

---

## Self-Review

**Spec coverage:**
- MongoDataSource → Task 1. ✓
- Reuse IndexMembershipProvider + verified membership → Task 5. ✓
- Coverage report → Task 3. ✓
- Removed-name backfill → out of scope (follow-on plan), stated. ✓ (gated on Task 6's coverage output)
- Dual-run harness + delta → Task 4. ✓
- Integrity rules (read-only, no VERIFY, no guessed symbols) → Global Constraints + Task 5 steps + `test_changelog_has_no_unverified_rows`. ✓
- Testing (real Mongo, known-date membership, future-member exclusion) → Tasks 1, 4, 5. ✓
- Success criteria (one command emits both curves + metrics + delta + coverage) → Task 4 CLI + Task 6 run. ✓ (NIFTY50; NIFTY500 deferred)
- Sequencing checkpoints → task order. ✓

**Placeholder scan:** No TBD/TODO. Task 5 intentionally requires execution-time data gathering (real URLs/symbols) — this is data acquisition, not a code placeholder; the test `ADDED_SYMBOL`/`EVENT_DATE` constants are explicitly filled from a verified row in the same task.

**Type consistency:** `get_ohlc` signature identical across base/Mongo/fakes. `BacktestResult.equity/holdings/metrics/warnings` used as defined in engine.py. `CoverageReport(total, covered, missing)` consistent across Tasks 3–4. `ComparisonResult(biased, point_in_time, delta, coverage)` consistent across Task 4 impl/test/CLI. `delta` keys identical in impl and test. Providers implement the `UniverseProvider` protocol (`members_as_of`, `union_over`).
