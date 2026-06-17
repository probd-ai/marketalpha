# RS-Momentum Backend Core — Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-grade, fully-tested backtest engine for the RS-Momentum strategy — data ingestion (tvdatafeed) → point-in-time universe → strategy signals → portfolio simulation → performance metrics → CLI runner.

**Architecture:** Layered, interface-first. Each layer (`DataSource`, `CacheStore`, `UniverseProvider`, `Strategy`, `BacktestEngine`, `MetricsCalculator`) is an abstraction with one v1 implementation, independently unit-tested with synthetic fixtures. No external services required to test the core: OHLC is cached to Parquet, membership is file-backed (committed verified CSV), tvdatafeed is mocked in tests. Plan 2 swaps file-backed membership for Postgres behind the unchanged `UniverseProvider` interface.

**Tech Stack:** Python 3.12 · pandas · numpy · scipy · pyarrow (Parquet) · tvdatafeed (rongardF fork) · pydantic + pydantic-settings · pytest.

## Global Constraints

- **Python 3.12+**; all code type-annotated.
- **Index series for RS denominator & benchmark:** NIFTY 50 index level (`NIFTY`, exchange `NSE`) via tvdatafeed.
- **Universe is point-in-time** (survivorship-bias-free). `UniverseProvider` is multi-index by parameter (Nifty50, Nifty100, Nifty500, NiftySmallCap) — the index is an argument, never a swapped implementation.
- **Strategy spec (exact):** RS line = `close_stock / close_NIFTY`; per-stock trailing **22 trading-day** OLS slope + R²; **gate** `slope > 0`; rank-blend `0.5·rank(slope) + 0.5·rank(R²)` (average ranks, higher = stronger); keep top `round(nominal_size · 0.5)` (25 for Nifty50); then **22-day stock-price ROC** = `close[t]/close[t-22] − 1`, select top 10; equal-weight 10% each.
- **Rebalance:** first trading day of each calendar month; signals computed on the prior available close; held until next rebalance.
- **Defaults:** starting capital ₹1,000,000; transaction cost configurable, **default 0**, expressed in basis points per side and plumbed through the simulator; lookback 22; window 5 years from run date.
- **Secrets:** tvdatafeed credentials via environment variables only (`TV_USERNAME`, `TV_PASSWORD`); never committed; sample env uses placeholders.
- **Test data:** synthetic/anonymized fixtures only — no real PII, no secrets, no confidential data.
- **Naming:** the index is **NIFTY 50** / **Nifty50**; never substitute other product names.
- **Frequent commits:** every task ends with a commit. Author is the repo default; do not add any `Co-Authored-By` trailer.

---

## File Structure

```
tvagent/
  backend/
    pyproject.toml
    .env.example
    app/
      __init__.py
      core/
        __init__.py
        config.py              # Settings (pydantic-settings)
      data/
        __init__.py
        sources/
          __init__.py
          base.py              # DataSource Protocol
          tvdatafeed_source.py # TvDatafeedSource
        cache/
          __init__.py
          parquet_cache.py     # ParquetCache + CachedDataSource
        universe/
          __init__.py
          membership.py        # MembershipInterval, ChangeEvent, build_membership_intervals
          provider.py          # UniverseProvider Protocol + IndexMembershipProvider + loader
      strategies/
        __init__.py
        base.py                # Strategy Protocol, BacktestConfig
        signals.py             # pure functions: rs_line, slope_r2, rank_blend, roc
        rs_momentum.py         # RSMomentumStrategy
      backtest/
        __init__.py
        portfolio.py           # simulate()
        engine.py              # BacktestEngine, BacktestResult
        metrics.py             # MetricsCalculator
      run_backtest.py          # CLI entrypoint
    data/
      seed/
        nifty50_current.csv    # verified current constituents (downloaded)
        nifty50_changelog.csv  # verified reconstitution change-log
    tests/
      conftest.py
      data/
        test_parquet_cache.py
        test_membership.py
        test_provider.py
      strategies/
        test_signals.py
        test_rs_momentum.py
      backtest/
        test_portfolio.py
        test_metrics.py
        test_engine.py
```

---

### Task 0: Project scaffold

**Files:**
- Create: `backend/pyproject.toml`, `backend/.env.example`, `backend/app/__init__.py`, `backend/app/core/__init__.py`, `backend/app/core/config.py`, `backend/tests/conftest.py`
- Create empty `__init__.py` in every `app/` subpackage listed in the File Structure.

**Interfaces:**
- Produces: `app.core.config.Settings` with attributes `data_dir: Path`, `cache_dir: Path`, `seed_dir: Path`, `tv_username: str | None`, `tv_password: str | None`. Importable `settings = Settings()`.

- [ ] **Step 1: Write `backend/pyproject.toml`**

```toml
[project]
name = "tvagent-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pandas>=2.2",
    "numpy>=1.26",
    "scipy>=1.13",
    "pyarrow>=16",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "tvdatafeed @ git+https://github.com/rongardF/tvdatafeed.git",
]

[project.optional-dependencies]
dev = ["pytest>=8.2", "pytest-cov>=5.0"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

- [ ] **Step 2: Write `backend/.env.example` and `backend/app/core/config.py`**

`.env.example`:
```
TV_USERNAME=your_tradingview_username
TV_PASSWORD=your_tradingview_password
```

`app/core/config.py`:
```python
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    tv_username: str | None = None
    tv_password: str | None = None

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def seed_dir(self) -> Path:
        return self.data_dir / "seed"


settings = Settings()
```

- [ ] **Step 3: Create all package `__init__.py` files and `tests/conftest.py`**

Create empty `__init__.py` for: `app`, `app/core`, `app/data`, `app/data/sources`, `app/data/cache`, `app/data/universe`, `app/strategies`, `app/backtest`.

`tests/conftest.py`:
```python
import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def trading_days():
    # 60 business days starting 2024-01-01
    return pd.bdate_range("2024-01-01", periods=60)


@pytest.fixture
def linear_series(trading_days):
    # deterministic upward line, slope 1.0/day, perfect fit (R^2 == 1)
    return pd.Series(np.arange(len(trading_days), dtype=float) + 100.0, index=trading_days)
```

- [ ] **Step 4: Verify the project installs and pytest collects**

Run: `cd backend && python -m venv .venv && .venv/Scripts/pip install -e ".[dev]" && .venv/Scripts/python -m pytest -q`
Expected: install succeeds; pytest reports "no tests ran" (exit 5) — acceptable at this stage.

- [ ] **Step 5: Commit**

```bash
cd backend && git init && printf ".venv/\n__pycache__/\n*.pyc\ndata/cache/\n.env\n" > .gitignore
git add .
git commit -m "chore: scaffold backend project (config, pyproject, test harness)"
```

---

### Task 1: DataSource interface + TvDatafeedSource

**Files:**
- Create: `app/data/sources/base.py`, `app/data/sources/tvdatafeed_source.py`
- Test: `tests/data/test_tvdatafeed_source.py`

**Interfaces:**
- Produces: `DataSource` Protocol with `get_ohlc(symbol: str, exchange: str, interval: str, start: date, end: date) -> pd.DataFrame`. Returned frame has a `DatetimeIndex` (naive, ascending) and lowercase columns including `close` (float). Rows are restricted to `start <= index.date() <= end`.
- Produces: `TvDatafeedSource(username=None, password=None)` implementing `DataSource`. Uses `tvDatafeed`. `interval="daily"` maps to `Interval.in_daily`.

- [ ] **Step 1: Write the failing test** (`tests/data/test_tvdatafeed_source.py`)

```python
from datetime import date
import pandas as pd
from app.data.sources.tvdatafeed_source import TvDatafeedSource


class _FakeTv:
    def get_hist(self, symbol, exchange, interval, n_bars):
        idx = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
        return pd.DataFrame(
            {"symbol": symbol, "open": 1.0, "high": 2.0, "low": 0.5,
             "close": [10.0, 11.0, 12.0], "volume": 100},
            index=idx,
        )


def test_get_ohlc_normalizes_and_slices():
    src = TvDatafeedSource(client=_FakeTv())
    df = src.get_ohlc("INFY", "NSE", "daily", date(2024, 1, 2), date(2024, 1, 3))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.is_monotonic_increasing
    assert df.index.min().date() == date(2024, 1, 2)
    assert df["close"].tolist() == [11.0, 12.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/data/test_tvdatafeed_source.py -v`
Expected: FAIL — `ModuleNotFoundError` / `TvDatafeedSource` not defined.

- [ ] **Step 3: Write minimal implementation**

`app/data/sources/base.py`:
```python
from datetime import date
from typing import Protocol
import pandas as pd


class DataSource(Protocol):
    def get_ohlc(
        self, symbol: str, exchange: str, interval: str, start: date, end: date
    ) -> pd.DataFrame: ...
```

`app/data/sources/tvdatafeed_source.py`:
```python
from datetime import date
import pandas as pd

_COLUMNS = ["open", "high", "low", "close", "volume"]


class TvDatafeedSource:
    """DataSource backed by the rongardF tvdatafeed fork."""

    def __init__(self, username=None, password=None, client=None):
        if client is not None:
            self._client = client
        else:
            from tvDatafeed import TvDatafeed
            self._client = TvDatafeed(username=username, password=password)

    def get_ohlc(self, symbol, exchange, interval, start, end):
        from tvDatafeed import Interval
        interval_map = {"daily": Interval.in_daily}
        n_bars = max((end - start).days * 2, 100)
        raw = self._client.get_hist(
            symbol=symbol, exchange=exchange,
            interval=interval_map[interval], n_bars=n_bars,
        )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=_COLUMNS)
        df = raw.copy()
        df.columns = [c.lower() for c in df.columns]
        df = df[[c for c in _COLUMNS if c in df.columns]]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df.sort_index()
        mask = (df.index.date >= start) & (df.index.date <= end)
        return df.loc[mask]
```

Note: `Interval` is imported lazily inside the method so the fake client path in tests needs no tvdatafeed install — but the import still runs. If tvdatafeed is unavailable in CI, guard the test with `pytest.importorskip("tvDatafeed")` at the top of the test module.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/data/test_tvdatafeed_source.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/data/sources tests/data/test_tvdatafeed_source.py
git commit -m "feat(data): DataSource interface and TvDatafeedSource adapter"
```

---

### Task 2: ParquetCache + CachedDataSource (read-through)

**Files:**
- Create: `app/data/cache/parquet_cache.py`
- Test: `tests/data/test_parquet_cache.py`

**Interfaces:**
- Consumes: `DataSource` (Task 1).
- Produces: `ParquetCache(cache_dir: Path)` with `get(key: str) -> pd.DataFrame | None` and `put(key: str, df: pd.DataFrame) -> None`. Keys are sanitized to filenames.
- Produces: `CachedDataSource(source: DataSource, cache: ParquetCache)` implementing `DataSource`. On `get_ohlc`, returns cached full series sliced to `[start, end]`; on cache miss fetches a wide window from `source`, stores it, then slices. Cache key = `f"{exchange}_{symbol}_{interval}"`.

- [ ] **Step 1: Write the failing test** (`tests/data/test_parquet_cache.py`)

```python
from datetime import date
import pandas as pd
from app.data.cache.parquet_cache import ParquetCache, CachedDataSource


class _CountingSource:
    def __init__(self):
        self.calls = 0

    def get_ohlc(self, symbol, exchange, interval, start, end):
        self.calls += 1
        idx = pd.bdate_range("2024-01-01", periods=10)
        return pd.DataFrame({"close": range(10)}, index=idx, dtype=float)


def test_cache_roundtrip(tmp_path):
    cache = ParquetCache(tmp_path)
    df = pd.DataFrame({"close": [1.0, 2.0]}, index=pd.bdate_range("2024-01-01", periods=2))
    cache.put("NSE_INFY_daily", df)
    out = cache.get("NSE_INFY_daily")
    assert out is not None and out["close"].tolist() == [1.0, 2.0]
    assert cache.get("missing") is None


def test_cached_source_fetches_once_then_serves_from_cache(tmp_path):
    src = _CountingSource()
    cached = CachedDataSource(src, ParquetCache(tmp_path))
    a = cached.get_ohlc("INFY", "NSE", "daily", date(2024, 1, 1), date(2024, 1, 5))
    b = cached.get_ohlc("INFY", "NSE", "daily", date(2024, 1, 2), date(2024, 1, 3))
    assert src.calls == 1                       # second call served from cache
    assert a.index.min().date() >= date(2024, 1, 1)
    assert b["close"].tolist() == [1.0, 2.0]    # sliced to requested window
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/data/test_parquet_cache.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation** (`app/data/cache/parquet_cache.py`)

```python
import re
from datetime import date
from pathlib import Path
import pandas as pd


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)


class ParquetCache:
    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        return self.cache_dir / f"{_safe(key)}.parquet"

    def get(self, key):
        p = self._path(key)
        if not p.exists():
            return None
        return pd.read_parquet(p)

    def put(self, key, df):
        df.to_parquet(self._path(key))


class CachedDataSource:
    def __init__(self, source, cache):
        self._source = source
        self._cache = cache

    def get_ohlc(self, symbol, exchange, interval, start, end):
        key = f"{exchange}_{symbol}_{interval}"
        df = self._cache.get(key)
        if df is None:
            df = self._source.get_ohlc(symbol, exchange, interval, start, end)
            if not df.empty:
                self._cache.put(key, df)
        if df.empty:
            return df
        mask = (df.index.date >= start) & (df.index.date <= end)
        return df.loc[mask]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/data/test_parquet_cache.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/data/cache tests/data/test_parquet_cache.py
git commit -m "feat(data): Parquet read-through cache and CachedDataSource"
```

---

### Task 3: Membership reconstruction (point-in-time, bias-free)

**Files:**
- Create: `app/data/universe/membership.py`
- Test: `tests/data/test_membership.py`

**Interfaces:**
- Produces: dataclasses `ChangeEvent(on: date, added: frozenset[str], removed: frozenset[str])` and `MembershipInterval(symbol: str, effective_from: date | None, effective_to: date | None)` (`effective_from=None` ⇒ open at the start of history; `effective_to=None` ⇒ still a member).
- Produces: `build_membership_intervals(current_members: set[str], changelog: list[ChangeEvent], anchor: date) -> list[MembershipInterval]`. Replays the changelog backward from `anchor` (where membership == `current_members`) to produce one or more intervals per symbol. A stock added on date D has `effective_from=D`; a stock removed on date D has `effective_to=D`.

- [ ] **Step 1: Write the failing test** (`tests/data/test_membership.py`)

```python
from datetime import date
from app.data.universe.membership import (
    ChangeEvent, MembershipInterval, build_membership_intervals,
)


def _interval(intervals, symbol):
    return [i for i in intervals if i.symbol == symbol]


def test_survivor_has_open_interval():
    anchor = date(2026, 1, 1)
    out = build_membership_intervals({"AAA", "BBB"}, [], anchor)
    aaa = _interval(out, "AAA")
    assert len(aaa) == 1
    assert aaa[0].effective_from is None and aaa[0].effective_to is None


def test_added_stock_starts_at_event_date():
    anchor = date(2026, 1, 1)
    log = [ChangeEvent(date(2022, 3, 31), frozenset({"AAA"}), frozenset({"OLD"}))]
    out = build_membership_intervals({"AAA", "BBB"}, log, anchor)
    aaa = _interval(out, "AAA")[0]
    assert aaa.effective_from == date(2022, 3, 31) and aaa.effective_to is None


def test_removed_stock_ends_at_event_date():
    anchor = date(2026, 1, 1)
    log = [ChangeEvent(date(2022, 3, 31), frozenset({"AAA"}), frozenset({"OLD"}))]
    out = build_membership_intervals({"AAA", "BBB"}, log, anchor)
    old = _interval(out, "OLD")[0]
    assert old.effective_from is None and old.effective_to == date(2022, 3, 31)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/data/test_membership.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation** (`app/data/universe/membership.py`)

```python
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ChangeEvent:
    on: date
    added: frozenset[str]
    removed: frozenset[str]


@dataclass(frozen=True)
class MembershipInterval:
    symbol: str
    effective_from: date | None
    effective_to: date | None


def build_membership_intervals(current_members, changelog, anchor):
    """Replay changelog backward from anchor to build membership intervals.

    At anchor, members == current_members (each with an open-ended interval).
    Walking events newest->oldest: an 'added on D' stock had its open interval
    begin at D (and was absent before D); a 'removed on D' stock had a new
    interval ending at D (open backward from D)."""
    current = set(current_members)
    open_to = {s: None for s in current}     # symbol -> effective_to of its open interval
    intervals: list[MembershipInterval] = []

    for ev in sorted(changelog, key=lambda e: e.on, reverse=True):
        for s in ev.added:                    # present after D, absent before D
            if s in current:
                intervals.append(MembershipInterval(s, ev.on, open_to.get(s)))
                current.discard(s)
                open_to.pop(s, None)
        for s in ev.removed:                  # absent after D, present before D
            open_to[s] = ev.on
            current.add(s)

    for s in current:                         # members extending to start of history
        intervals.append(MembershipInterval(s, None, open_to.get(s)))
    return intervals
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/data/test_membership.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/data/universe/membership.py tests/data/test_membership.py
git commit -m "feat(universe): point-in-time membership interval reconstruction"
```

---

### Task 4: IndexMembershipProvider + verified seed data

**Files:**
- Create: `app/data/universe/provider.py`
- Create: `data/seed/nifty50_changelog.csv`, `data/seed/nifty50_current.csv`
- Test: `tests/data/test_provider.py`

**Interfaces:**
- Consumes: `build_membership_intervals`, `ChangeEvent`, `MembershipInterval` (Task 3).
- Produces: `UniverseProvider` Protocol with `members_as_of(index: str, on: date) -> set[str]` and `union_over(index: str, start: date, end: date) -> set[str]`.
- Produces: `IndexMembershipProvider(intervals_by_index: dict[str, list[MembershipInterval]])` implementing it.
- Produces: `load_provider_from_seed(seed_dir: Path, anchor: date) -> IndexMembershipProvider`. Reads `<index>_current.csv` (one column `symbol`) and `<index>_changelog.csv` (columns `effective_date,added,removed,source_url`; `added`/`removed` are `;`-separated symbol lists, blank allowed) for each known index file present. Index key derived from filename prefix uppercased (e.g. `nifty50` -> `NIFTY50`).

A membership interval `[from, to)` covers a date `d` when `(from is None or from <= d)` and `(to is None or d < to)`.

- [ ] **Step 1: Write the failing test** (`tests/data/test_provider.py`)

```python
from datetime import date
from app.data.universe.membership import MembershipInterval
from app.data.universe.provider import IndexMembershipProvider, load_provider_from_seed


def test_members_as_of_respects_intervals():
    intervals = [
        MembershipInterval("AAA", None, None),                       # always
        MembershipInterval("OLD", None, date(2022, 3, 31)),          # left at 2022-03-31
        MembershipInterval("NEW", date(2022, 3, 31), None),          # joined 2022-03-31
    ]
    p = IndexMembershipProvider({"NIFTY50": intervals})
    assert p.members_as_of("NIFTY50", date(2021, 1, 1)) == {"AAA", "OLD"}
    assert p.members_as_of("NIFTY50", date(2023, 1, 1)) == {"AAA", "NEW"}


def test_union_over_window_includes_dropped_names():
    intervals = [
        MembershipInterval("AAA", None, None),
        MembershipInterval("OLD", None, date(2022, 3, 31)),
        MembershipInterval("NEW", date(2022, 3, 31), None),
    ]
    p = IndexMembershipProvider({"NIFTY50": intervals})
    assert p.union_over("NIFTY50", date(2021, 1, 1), date(2023, 1, 1)) == {"AAA", "OLD", "NEW"}


def test_load_from_seed(tmp_path):
    (tmp_path / "nifty50_current.csv").write_text("symbol\nAAA\nNEW\n")
    (tmp_path / "nifty50_changelog.csv").write_text(
        "effective_date,added,removed,source_url\n"
        "2022-03-31,NEW,OLD,https://example.com\n"
    )
    p = load_provider_from_seed(tmp_path, anchor=date(2026, 1, 1))
    assert p.members_as_of("NIFTY50", date(2021, 1, 1)) == {"AAA", "OLD"}
    assert "NEW" in p.members_as_of("NIFTY50", date(2023, 1, 1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/data/test_provider.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation** (`app/data/universe/provider.py`)

```python
from datetime import date
from pathlib import Path
from typing import Protocol
import pandas as pd
from app.data.universe.membership import (
    ChangeEvent, MembershipInterval, build_membership_intervals,
)


class UniverseProvider(Protocol):
    def members_as_of(self, index: str, on: date) -> set[str]: ...
    def union_over(self, index: str, start: date, end: date) -> set[str]: ...


def _covers(iv: MembershipInterval, d: date) -> bool:
    if iv.effective_from is not None and iv.effective_from > d:
        return False
    if iv.effective_to is not None and d >= iv.effective_to:
        return False
    return True


def _overlaps(iv: MembershipInterval, start: date, end: date) -> bool:
    if iv.effective_to is not None and iv.effective_to <= start:
        return False
    if iv.effective_from is not None and iv.effective_from > end:
        return False
    return True


class IndexMembershipProvider:
    def __init__(self, intervals_by_index):
        self._by_index = intervals_by_index

    def members_as_of(self, index, on):
        return {iv.symbol for iv in self._by_index[index] if _covers(iv, on)}

    def union_over(self, index, start, end):
        return {iv.symbol for iv in self._by_index[index] if _overlaps(iv, start, end)}


def _parse_symbols(cell) -> frozenset[str]:
    if cell is None or (isinstance(cell, float) and pd.isna(cell)) or str(cell).strip() == "":
        return frozenset()
    return frozenset(s.strip() for s in str(cell).split(";") if s.strip())


def load_provider_from_seed(seed_dir, anchor):
    seed_dir = Path(seed_dir)
    by_index = {}
    for current_csv in seed_dir.glob("*_current.csv"):
        prefix = current_csv.name[: -len("_current.csv")]
        index_key = prefix.upper()
        current = set(pd.read_csv(current_csv)["symbol"].astype(str).str.strip())
        changelog = []
        log_csv = seed_dir / f"{prefix}_changelog.csv"
        if log_csv.exists():
            df = pd.read_csv(log_csv)
            for _, row in df.iterrows():
                changelog.append(ChangeEvent(
                    on=pd.to_datetime(row["effective_date"]).date(),
                    added=_parse_symbols(row.get("added")),
                    removed=_parse_symbols(row.get("removed")),
                ))
        by_index[index_key] = build_membership_intervals(current, changelog, anchor)
    return IndexMembershipProvider(by_index)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/data/test_provider.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Create verified seed data**

`data/seed/nifty50_changelog.csv` — populate from the draft in the spec, **but verify every row against `https://www.niftyindices.com/` press releases before committing** (header + rows; `added`/`removed` are `;`-separated):
```csv
effective_date,added,removed,source_url
2021-03-31,Tata Consumer Products,GAIL,VERIFY
2022-03-31,Apollo Hospitals,Indian Oil Corporation,VERIFY
2022-09-30,Adani Enterprises,Shree Cement,VERIFY
2023-07-13,LTIMindtree,HDFC Ltd,VERIFY
2024-03-28,Shriram Finance,UPL,VERIFY
2024-09-30,Bharat Electronics;Trent,Divi's Laboratories;LTIMindtree,VERIFY
2025-03-28,Jio Financial Services;Eternal,Bharat Petroleum;Britannia Industries,VERIFY
2025-09-30,InterGlobe Aviation;Max Healthcare,Hero MotoCorp;IndusInd Bank,VERIFY
```
Replace each `VERIFY` with the authoritative NSE press-release URL once confirmed. Use the exact tvdatafeed NSE trading symbols (e.g. `TATACONSUM`, `APOLLOHOSP`, `ADANIENT`, `LTIM`, `SHRIRAMFIN`, `BEL`, `TRENT`, `DIVISLAB`, `JIOFIN`, `ETERNAL`, `INDIGO`, `MAXHEALTH`) — symbol-map during verification; do not guess.

`data/seed/nifty50_current.csv` — download the authoritative current constituents from `https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv`, reduce to a single `symbol` column of tvdatafeed NSE symbols. Do **not** hand-type the 50 tickers; derive from the official file. Header: `symbol`.

- [ ] **Step 6: Commit**

```bash
git add app/data/universe/provider.py tests/data/test_provider.py data/seed
git commit -m "feat(universe): IndexMembershipProvider with verified Nifty50 seed"
```

---

### Task 5: Strategy signal functions (pure)

**Files:**
- Create: `app/strategies/signals.py`
- Test: `tests/strategies/test_signals.py`

**Interfaces:**
- Produces (all pure, operate on pandas):
  - `rs_line(close: pd.Series, nifty: pd.Series) -> pd.Series` — aligned `close / nifty` on shared index.
  - `slope_r2(values: np.ndarray) -> tuple[float, float]` — OLS of `values` vs `0..n-1`; returns `(slope, r2)`; returns `(nan, nan)` if `len(values) < 2` or zero variance.
  - `trailing_slope_r2(series: pd.Series, as_of: date, lookback: int) -> tuple[float, float]` — last `lookback` points with index `<= as_of`; `(nan, nan)` if fewer than `lookback`.
  - `rank_blend(slopes: pd.Series, r2s: pd.Series) -> pd.Series` — over the shared (positive-slope) index, `0.5 * slopes.rank() + 0.5 * r2s.rank()` (average method, ascending so higher value → higher rank); index = input index.
  - `roc(series: pd.Series, as_of: date, window: int) -> float` — `v[as_of] / v[window-bars-before] - 1`; `nan` if insufficient history.

- [ ] **Step 1: Write the failing test** (`tests/strategies/test_signals.py`)

```python
from datetime import date
import numpy as np
import pandas as pd
from app.strategies.signals import rs_line, slope_r2, trailing_slope_r2, rank_blend, roc


def test_rs_line_divides_aligned():
    idx = pd.bdate_range("2024-01-01", periods=3)
    close = pd.Series([10.0, 20.0, 30.0], index=idx)
    nifty = pd.Series([10.0, 10.0, 10.0], index=idx)
    assert rs_line(close, nifty).tolist() == [1.0, 2.0, 3.0]


def test_slope_r2_perfect_line():
    slope, r2 = slope_r2(np.array([1.0, 2.0, 3.0, 4.0]))
    assert round(slope, 6) == 1.0 and round(r2, 6) == 1.0


def test_slope_r2_insufficient():
    s, r = slope_r2(np.array([5.0]))
    assert np.isnan(s) and np.isnan(r)


def test_trailing_slope_r2_uses_last_lookback(linear_series):
    as_of = linear_series.index[40].date()
    slope, r2 = trailing_slope_r2(linear_series, as_of, lookback=22)
    assert round(slope, 6) == 1.0 and round(r2, 6) == 1.0


def test_rank_blend_orders_by_average_rank():
    slopes = pd.Series({"A": 0.1, "B": 0.3, "C": 0.2})
    r2s = pd.Series({"A": 0.9, "B": 0.5, "C": 0.7})
    blended = rank_blend(slopes, r2s)
    # A: 0.5*1 + 0.5*3 = 2.0 ; B: 0.5*3 + 0.5*1 = 2.0 ; C: 0.5*2 + 0.5*2 = 2.0
    assert round(blended["C"], 6) == 2.0
    assert set(blended.index) == {"A", "B", "C"}


def test_roc_22_bars(linear_series):
    as_of = linear_series.index[30].date()
    expected = linear_series.iloc[30] / linear_series.iloc[8] - 1
    assert round(roc(linear_series, as_of, 22), 9) == round(expected, 9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/strategies/test_signals.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation** (`app/strategies/signals.py`)

```python
from datetime import date
import numpy as np
import pandas as pd
from scipy import stats


def rs_line(close: pd.Series, nifty: pd.Series) -> pd.Series:
    aligned_close, aligned_nifty = close.align(nifty, join="inner")
    return aligned_close / aligned_nifty


def slope_r2(values: np.ndarray) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    if len(values) < 2 or np.allclose(values, values[0]):
        x = np.arange(len(values))
        if len(values) < 2:
            return float("nan"), float("nan")
    x = np.arange(len(values), dtype=float)
    result = stats.linregress(x, values)
    return float(result.slope), float(result.rvalue ** 2)


def trailing_slope_r2(series: pd.Series, as_of: date, lookback: int) -> tuple[float, float]:
    window = series[series.index.date <= as_of].tail(lookback)
    if len(window) < lookback:
        return float("nan"), float("nan")
    return slope_r2(window.to_numpy())


def rank_blend(slopes: pd.Series, r2s: pd.Series) -> pd.Series:
    idx = slopes.index.intersection(r2s.index)
    s_rank = slopes.loc[idx].rank(method="average")
    r_rank = r2s.loc[idx].rank(method="average")
    return 0.5 * s_rank + 0.5 * r_rank


def roc(series: pd.Series, as_of: date, window: int) -> float:
    hist = series[series.index.date <= as_of]
    if len(hist) < window + 1:
        return float("nan")
    return float(hist.iloc[-1] / hist.iloc[-1 - window] - 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/strategies/test_signals.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/strategies/signals.py tests/strategies/test_signals.py
git commit -m "feat(strategy): RS/slope/R2/rank-blend/ROC signal functions"
```

---

### Task 6: RSMomentumStrategy + BacktestConfig

**Files:**
- Create: `app/strategies/base.py`, `app/strategies/rs_momentum.py`
- Test: `tests/strategies/test_rs_momentum.py`

**Interfaces:**
- Consumes: `signals.*` (Task 5).
- Produces: `BacktestConfig` (pydantic `BaseModel`) with fields `index: str = "NIFTY50"`, `start: date`, `end: date`, `starting_capital: float = 1_000_000`, `lookback: int = 22`, `top_pct: float = 0.5`, `top_n: int = 10`, `nominal_size: int = 50`, `cost_bps: float = 0.0`.
- Produces: `Strategy` Protocol with `select(close_panel: pd.DataFrame, nifty: pd.Series, members: set[str], as_of: date, cfg: BacktestConfig) -> dict[str, float]` (symbol → weight, summing to ≤ 1.0).
- Produces: `RSMomentumStrategy` implementing it. `close_panel` is dates×symbols. Pipeline: eligible = members with ≥ `lookback`+1 non-NaN closes ≤ `as_of` → trailing slope/R² per eligible → gate `slope > 0` → `rank_blend` → top `round(nominal_size*top_pct)` by score → 22-day ROC → top `top_n` → equal weight `1/len`.

- [ ] **Step 1: Write the failing test** (`tests/strategies/test_rs_momentum.py`)

```python
from datetime import date
import numpy as np
import pandas as pd
from app.strategies.base import BacktestConfig
from app.strategies.rs_momentum import RSMomentumStrategy


def _panel():
    idx = pd.bdate_range("2024-01-01", periods=40)
    # 6 symbols with increasing steepness; NIFTY flat at 100
    data = {f"S{i}": 100.0 + i * np.arange(len(idx)) for i in range(6)}
    return pd.DataFrame(data, index=idx), pd.Series(100.0, index=idx)


def test_select_picks_equal_weighted_top_n():
    panel, nifty = _panel()
    cfg = BacktestConfig(start=date(2024, 1, 1), end=date(2024, 3, 1),
                         lookback=22, top_pct=0.5, top_n=2, nominal_size=6)
    strat = RSMomentumStrategy()
    weights = strat.select(panel, nifty, set(panel.columns), panel.index[39].date(), cfg)
    assert len(weights) == 2
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert all(abs(w - 0.5) < 1e-9 for w in weights.values())
    # steepest movers (highest slope & ROC) are S5, S4
    assert set(weights) == {"S5", "S4"}


def test_select_excludes_insufficient_history():
    panel, nifty = _panel()
    panel = panel.copy()
    panel.loc[panel.index[:38], "S5"] = np.nan   # only 2 valid points
    cfg = BacktestConfig(start=date(2024, 1, 1), end=date(2024, 3, 1),
                         lookback=22, top_pct=0.5, top_n=2, nominal_size=6)
    weights = RSMomentumStrategy().select(panel, nifty, set(panel.columns),
                                          panel.index[39].date(), cfg)
    assert "S5" not in weights
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/strategies/test_rs_momentum.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

`app/strategies/base.py`:
```python
from datetime import date
from typing import Protocol
import pandas as pd
from pydantic import BaseModel


class BacktestConfig(BaseModel):
    index: str = "NIFTY50"
    start: date
    end: date
    starting_capital: float = 1_000_000
    lookback: int = 22
    top_pct: float = 0.5
    top_n: int = 10
    nominal_size: int = 50
    cost_bps: float = 0.0


class Strategy(Protocol):
    def select(self, close_panel: pd.DataFrame, nifty: pd.Series,
               members: set[str], as_of: date, cfg: BacktestConfig) -> dict[str, float]: ...
```

`app/strategies/rs_momentum.py`:
```python
from datetime import date
import pandas as pd
from app.strategies.base import BacktestConfig
from app.strategies.signals import rs_line, trailing_slope_r2, rank_blend, roc


class RSMomentumStrategy:
    def select(self, close_panel, nifty, members, as_of, cfg):
        cols = [s for s in close_panel.columns if s in members]
        hist = close_panel.loc[close_panel.index.date <= as_of, cols]

        slopes, r2s = {}, {}
        for s in cols:
            series = hist[s].dropna()
            if len(series) < cfg.lookback + 1:
                continue
            rs = rs_line(series, nifty)
            slope, r2 = trailing_slope_r2(rs, as_of, cfg.lookback)
            if pd.notna(slope) and slope > 0:           # slope > 0 gate
                slopes[s], r2s[s] = slope, r2

        if not slopes:
            return {}
        scores = rank_blend(pd.Series(slopes), pd.Series(r2s))
        n_keep = max(1, round(cfg.nominal_size * cfg.top_pct))
        top = scores.sort_values(ascending=False).head(n_keep).index

        rocs = {s: roc(hist[s].dropna(), as_of, cfg.lookback) for s in top}
        roc_series = pd.Series(rocs).dropna().sort_values(ascending=False)
        selected = list(roc_series.head(cfg.top_n).index)
        if not selected:
            return {}
        w = 1.0 / len(selected)
        return {s: w for s in selected}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/strategies/test_rs_momentum.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/strategies/base.py app/strategies/rs_momentum.py tests/strategies/test_rs_momentum.py
git commit -m "feat(strategy): RSMomentumStrategy selection pipeline + BacktestConfig"
```

---

### Task 7: Portfolio simulation

**Files:**
- Create: `app/backtest/portfolio.py`
- Test: `tests/backtest/test_portfolio.py`

**Interfaces:**
- Produces: `simulate(close_panel: pd.DataFrame, schedule: list[tuple[date, dict[str, float]]], start_capital: float, cost_bps: float) -> SimResult` where `SimResult` is a dataclass with `equity: pd.Series` (daily, indexed by trading date from first rebalance to panel end), `turnover: float` (sum of traded notional / mean equity), and `holdings: list[tuple[date, dict[str, float]]]` (echo of applied weights).
- Behavior: at each rebalance date, target value per symbol = `weight * portfolio_value`; shares = `target_value / close`; transaction cost = `cost_bps/1e4 * abs(traded_notional)` deducted from cash. Between rebalances, value = `cash + Σ shares*close`. Symbols missing a close on a given day carry their last valid price forward for valuation.

- [ ] **Step 1: Write the failing test** (`tests/backtest/test_portfolio.py`)

```python
from datetime import date
import pandas as pd
from app.backtest.portfolio import simulate


def test_single_holding_tracks_price():
    idx = pd.bdate_range("2024-01-01", periods=4)
    panel = pd.DataFrame({"A": [100.0, 110.0, 121.0, 121.0]}, index=idx)
    res = simulate(panel, [(idx[0].date(), {"A": 1.0})], start_capital=1000.0, cost_bps=0.0)
    # fully invested in A from day 0; +10% then +10%
    assert round(res.equity.iloc[0], 6) == 1000.0
    assert round(res.equity.iloc[1], 6) == 1100.0
    assert round(res.equity.iloc[2], 6) == 1210.0


def test_costs_reduce_equity():
    idx = pd.bdate_range("2024-01-01", periods=2)
    panel = pd.DataFrame({"A": [100.0, 100.0]}, index=idx)
    no_cost = simulate(panel, [(idx[0].date(), {"A": 1.0})], 1000.0, 0.0)
    with_cost = simulate(panel, [(idx[0].date(), {"A": 1.0})], 1000.0, 10.0)  # 10 bps
    assert with_cost.equity.iloc[-1] < no_cost.equity.iloc[-1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/backtest/test_portfolio.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation** (`app/backtest/portfolio.py`)

```python
from dataclasses import dataclass
from datetime import date
import pandas as pd


@dataclass
class SimResult:
    equity: pd.Series
    turnover: float
    holdings: list


def simulate(close_panel, schedule, start_capital, cost_bps):
    schedule = sorted(schedule, key=lambda x: x[0])
    if not schedule:
        return SimResult(pd.Series(dtype=float), 0.0, [])

    start = schedule[0][0]
    prices = close_panel[close_panel.index.date >= start].ffill()
    rebal = {d: w for d, w in schedule}

    cash = float(start_capital)
    shares: dict[str, float] = {}
    traded_notional = 0.0
    equity_points = {}

    for ts in prices.index:
        d = ts.date()
        row = prices.loc[ts]
        if d in rebal:
            value = cash + sum(sh * row.get(s, 0.0) for s, sh in shares.items())
            target = {s: w * value for s, w in rebal[d].items()}
            new_shares = {}
            for s, tv in target.items():
                px = row.get(s)
                if px and px > 0:
                    new_shares[s] = tv / px
            for s in set(shares) | set(new_shares):
                px = row.get(s, 0.0) or 0.0
                delta = (new_shares.get(s, 0.0) - shares.get(s, 0.0)) * px
                traded_notional += abs(delta)
                cost = abs(delta) * cost_bps / 1e4
                cash -= delta + cost
            shares = new_shares
        equity_points[ts] = cash + sum(sh * row.get(s, 0.0) for s, sh in shares.items())

    equity = pd.Series(equity_points)
    turnover = traded_notional / equity.mean() if len(equity) else 0.0
    return SimResult(equity, float(turnover), list(schedule))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/backtest/test_portfolio.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/backtest/portfolio.py tests/backtest/test_portfolio.py
git commit -m "feat(backtest): daily portfolio simulation with transaction costs"
```

---

### Task 8: MetricsCalculator

**Files:**
- Create: `app/backtest/metrics.py`
- Test: `tests/backtest/test_metrics.py`

**Interfaces:**
- Produces: `compute_metrics(equity: pd.Series, benchmark: pd.Series, turnover: float, periods_per_year: int = 252) -> dict`. Keys: `total_return`, `cagr`, `volatility`, `sharpe`, `sortino`, `max_drawdown`, `turnover`, `benchmark_total_return`, `alpha` (= `total_return − benchmark_total_return`). Risk-free rate assumed 0. Returns 0.0 (not NaN) for degenerate inputs (single point).

- [ ] **Step 1: Write the failing test** (`tests/backtest/test_metrics.py`)

```python
import numpy as np
import pandas as pd
from app.backtest.metrics import compute_metrics


def test_total_return_and_drawdown():
    idx = pd.bdate_range("2024-01-01", periods=5)
    equity = pd.Series([100, 110, 105, 120, 130], index=idx, dtype=float)
    bench = pd.Series([100, 100, 100, 100, 110], index=idx, dtype=float)
    m = compute_metrics(equity, bench, turnover=1.0)
    assert round(m["total_return"], 6) == 0.30
    assert round(m["benchmark_total_return"], 6) == 0.10
    assert round(m["alpha"], 6) == 0.20
    # max drawdown: 110 -> 105 = -4.5454...%
    assert round(m["max_drawdown"], 4) == -0.0455


def test_degenerate_single_point():
    idx = pd.bdate_range("2024-01-01", periods=1)
    s = pd.Series([100.0], index=idx)
    m = compute_metrics(s, s, turnover=0.0)
    assert m["total_return"] == 0.0 and m["sharpe"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/backtest/test_metrics.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation** (`app/backtest/metrics.py`)

```python
import numpy as np
import pandas as pd


def _total_return(equity):
    if len(equity) < 2 or equity.iloc[0] == 0:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1)


def compute_metrics(equity, benchmark, turnover, periods_per_year=252):
    tr = _total_return(equity)
    btr = _total_return(benchmark)
    if len(equity) < 2:
        return {"total_return": tr, "cagr": 0.0, "volatility": 0.0, "sharpe": 0.0,
                "sortino": 0.0, "max_drawdown": 0.0, "turnover": float(turnover),
                "benchmark_total_return": btr, "alpha": tr - btr}

    rets = equity.pct_change().dropna()
    years = len(equity) / periods_per_year
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    vol = rets.std(ddof=0) * np.sqrt(periods_per_year)
    sharpe = (rets.mean() / rets.std(ddof=0) * np.sqrt(periods_per_year)) if rets.std(ddof=0) else 0.0
    downside = rets[rets < 0].std(ddof=0)
    sortino = (rets.mean() / downside * np.sqrt(periods_per_year)) if downside else 0.0
    cummax = equity.cummax()
    max_dd = float(((equity - cummax) / cummax).min())

    return {
        "total_return": tr,
        "cagr": float(cagr),
        "volatility": float(vol),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown": max_dd,
        "turnover": float(turnover),
        "benchmark_total_return": btr,
        "alpha": tr - btr,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/backtest/test_metrics.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/backtest/metrics.py tests/backtest/test_metrics.py
git commit -m "feat(backtest): performance metrics calculator"
```

---

### Task 9: BacktestEngine (wire it together)

**Files:**
- Create: `app/backtest/engine.py`
- Test: `tests/backtest/test_engine.py`

**Interfaces:**
- Consumes: `UniverseProvider`, `DataSource` (cached), `RSMomentumStrategy`, `simulate`, `compute_metrics`, `BacktestConfig`.
- Produces: `BacktestResult` dataclass: `equity: pd.Series`, `benchmark: pd.Series`, `metrics: dict`, `holdings: list[tuple[date, dict]]`, `warnings: list[str]`.
- Produces: `BacktestEngine(source: DataSource, provider: UniverseProvider, strategy, index_symbol="NIFTY", exchange="NSE")` with `run(cfg: BacktestConfig) -> BacktestResult`.
- Produces module helper `monthly_rebalance_dates(trading_index: pd.DatetimeIndex) -> list[date]` — first available trading day of each calendar month.

- [ ] **Step 1: Write the failing test** (`tests/backtest/test_engine.py`)

```python
from datetime import date
import numpy as np
import pandas as pd
from app.strategies.base import BacktestConfig
from app.strategies.rs_momentum import RSMomentumStrategy
from app.backtest.engine import BacktestEngine, monthly_rebalance_dates


def test_monthly_rebalance_dates_first_per_month():
    idx = pd.bdate_range("2024-01-01", "2024-03-15")
    dates = monthly_rebalance_dates(idx)
    months = sorted({(d.year, d.month) for d in dates})
    assert months == [(2024, 1), (2024, 2), (2024, 3)]
    assert dates[0] == idx[0].date()


class _StubSource:
    def __init__(self, panel, nifty):
        self._panel, self._nifty = panel, nifty

    def get_ohlc(self, symbol, exchange, interval, start, end):
        s = self._nifty if symbol == "NIFTY" else self._panel[symbol]
        return pd.DataFrame({"close": s})


class _StubProvider:
    def __init__(self, symbols):
        self._symbols = set(symbols)

    def members_as_of(self, index, on):
        return self._symbols

    def union_over(self, index, start, end):
        return self._symbols


def test_engine_runs_end_to_end():
    idx = pd.bdate_range("2024-01-01", periods=70)
    panel = pd.DataFrame({f"S{i}": 100.0 + i * np.arange(len(idx)) for i in range(6)}, index=idx)
    nifty = pd.Series(100.0, index=idx)
    cfg = BacktestConfig(start=idx[0].date(), end=idx[-1].date(),
                         lookback=22, top_pct=0.5, top_n=2, nominal_size=6)
    engine = BacktestEngine(_StubSource(panel, nifty), _StubProvider(panel.columns),
                            RSMomentumStrategy())
    result = engine.run(cfg)
    assert len(result.equity) > 0
    assert "sharpe" in result.metrics
    assert result.equity.iloc[0] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/backtest/test_engine.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation** (`app/backtest/engine.py`)

```python
from dataclasses import dataclass
from datetime import date
import pandas as pd
from app.backtest.portfolio import simulate
from app.backtest.metrics import compute_metrics


def monthly_rebalance_dates(trading_index):
    df = pd.DataFrame(index=trading_index)
    df["ym"] = [(d.year, d.month) for d in trading_index]
    firsts = df.groupby("ym").apply(lambda g: g.index.min())
    return sorted(ts.date() for ts in firsts)


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series
    metrics: dict
    holdings: list
    warnings: list


class BacktestEngine:
    def __init__(self, source, provider, strategy, index_symbol="NIFTY", exchange="NSE"):
        self._source = source
        self._provider = provider
        self._strategy = strategy
        self._index_symbol = index_symbol
        self._exchange = exchange

    def _close(self, symbol, cfg):
        df = self._source.get_ohlc(symbol, self._exchange, "daily", cfg.start, cfg.end)
        return df["close"] if "close" in df else pd.Series(dtype=float)

    def run(self, cfg):
        warnings = []
        nifty = self._close(self._index_symbol, cfg)
        symbols = sorted(self._provider.union_over(cfg.index, cfg.start, cfg.end))
        series = {}
        for s in symbols:
            close = self._close(s, cfg)
            if close.empty:
                warnings.append(f"no data for {s}")
                continue
            series[s] = close
        panel = pd.DataFrame(series)

        rebal_dates = monthly_rebalance_dates(panel.index)
        schedule = []
        for d in rebal_dates:
            members = self._provider.members_as_of(cfg.index, d)
            weights = self._strategy.select(panel, nifty, members, d, cfg)
            if weights:
                schedule.append((d, weights))
            else:
                warnings.append(f"no selection on {d}")

        sim = simulate(panel, schedule, cfg.starting_capital, cfg.cost_bps)
        bench = nifty[nifty.index >= pd.Timestamp(schedule[0][0])] if schedule else nifty
        bench = bench / bench.iloc[0] * cfg.starting_capital if len(bench) else bench
        metrics = compute_metrics(sim.equity, bench, sim.turnover)
        return BacktestResult(sim.equity, bench, metrics, sim.holdings, warnings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/backtest/test_engine.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/backtest/engine.py tests/backtest/test_engine.py
git commit -m "feat(backtest): BacktestEngine wiring universe+data+strategy+sim+metrics"
```

---

### Task 10: CLI runner + full-suite gate

**Files:**
- Create: `app/run_backtest.py`

**Interfaces:**
- Consumes: everything above; builds `TvDatafeedSource` → `CachedDataSource`, `load_provider_from_seed`, `RSMomentumStrategy`, `BacktestEngine`.
- Produces: `python -m app.run_backtest` runs a 5-year-from-today Nifty50 backtest and prints the metrics dict + warnings count. `main(argv=None)` is importable and returns the `BacktestResult`.

- [ ] **Step 1: Write `app/run_backtest.py`**

```python
import argparse
from datetime import date, timedelta
from app.core.config import settings
from app.data.sources.tvdatafeed_source import TvDatafeedSource
from app.data.cache.parquet_cache import ParquetCache, CachedDataSource
from app.data.universe.provider import load_provider_from_seed
from app.strategies.base import BacktestConfig
from app.strategies.rs_momentum import RSMomentumStrategy
from app.backtest.engine import BacktestEngine


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run RS-Momentum backtest")
    parser.add_argument("--index", default="NIFTY50")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--cost-bps", type=float, default=0.0)
    args = parser.parse_args(argv)

    end = date.today()
    start = end - timedelta(days=365 * args.years)
    cfg = BacktestConfig(index=args.index, start=start, end=end,
                         starting_capital=args.capital, cost_bps=args.cost_bps)

    source = CachedDataSource(
        TvDatafeedSource(settings.tv_username, settings.tv_password),
        ParquetCache(settings.cache_dir),
    )
    provider = load_provider_from_seed(settings.seed_dir, anchor=end)
    engine = BacktestEngine(source, provider, RSMomentumStrategy())
    result = engine.run(cfg)

    print("=== RS-Momentum backtest ===")
    for k, v in result.metrics.items():
        print(f"{k:>24}: {v:,.4f}" if isinstance(v, float) else f"{k:>24}: {v}")
    print(f"{'warnings':>24}: {len(result.warnings)}")
    return result


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full test suite (gate)**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS — all tests from Tasks 1–9 green.

- [ ] **Step 3: Manual smoke (requires tvdatafeed credentials in `.env` and verified seed)**

Run: `.venv/Scripts/python -m app.run_backtest --years 5`
Expected: prints a metrics block (`total_return`, `cagr`, `sharpe`, `max_drawdown`, `alpha`, …) and a warnings count. If tvdatafeed credentials are absent, this step is expected to surface a clear auth/data warning rather than crash — note the outcome, do not fake success.

- [ ] **Step 4: Commit**

```bash
git add app/run_backtest.py
git commit -m "feat(cli): run_backtest entrypoint for end-to-end RS-Momentum backtest"
```

---

## Self-Review

**Spec coverage:**
- §3 strategy (RS, slope+R², gate, rank-blend, top-50%, ROC top-10, equal weight) → Tasks 5, 6 ✓
- §4 defaults (capital, costs, lookback, monthly rebalance, NIFTY index) → BacktestConfig (T6), engine rebalance dates (T9), CLI window (T10) ✓
- §5 survivorship / point-in-time multi-index universe → Tasks 3, 4 ✓ (file-backed; Postgres deferred to Plan 2 by design)
- §7 interfaces (DataSource, CacheStore, UniverseProvider, Strategy, BacktestEngine, MetricsCalculator) → Tasks 1, 2, 4, 6, 9, 8 ✓
- §8 data flow → Task 9 ✓
- §12 metrics → Task 8 ✓
- §13 error handling (data gaps → warnings, insufficient lookback → exclude, missing creds) → Tasks 6, 9, 10 ✓
- §14 testing (unit/integration, synthetic only) → every task ✓
- §15 secrets via env → Task 0 config, Task 10 ✓
- **Deferred to Plan 2/3 (out of scope here, by the 3-plan split):** §9 Postgres persistence, §10 API, §11 dashboard. Not gaps — sequenced.

**Placeholder scan:** No "TBD/TODO" in code. The seed CSV `VERIFY` tokens are intentional, explicit acceptance criteria (Task 4 Step 5 requires replacing them with authoritative URLs before commit) — not code placeholders.

**Type consistency:** `get_ohlc(symbol, exchange, interval, start, end)` identical across `DataSource`, `TvDatafeedSource`, `CachedDataSource`, stubs. `select(close_panel, nifty, members, as_of, cfg)` identical in `Strategy`, `RSMomentumStrategy`, and engine call site. `BacktestConfig` field names match all consumers. `MembershipInterval(symbol, effective_from, effective_to)` consistent across membership/provider/tests. `compute_metrics(equity, benchmark, turnover, periods_per_year)` matches engine call (positional `equity, bench, sim.turnover`). ✓

---

## Next plans (sequenced, written after Plan 1 executes)

- **Plan 2 — API + Postgres persistence:** FastAPI routers (`POST /backtests`, `GET /backtests/{id}/{equity,holdings,metrics}`, `GET /universe/{index}`), SQLAlchemy models + Alembic, migrate file-backed membership into `index_membership` behind the unchanged `UniverseProvider`, background run execution.
- **Plan 3 — Glass dashboard:** Next.js + Tailwind frosted-glass UI on the API — run-config panel, equity-vs-NIFTY chart, metric cards, holdings timeline.
