# API + Postgres Persistence — Implementation Plan (Plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the Plan 1 backtest engine over an async FastAPI HTTP API backed by Postgres — persist runs/results, serve them, and migrate the file-backed universe into the `index_membership` table behind the unchanged `UniverseProvider` interface.

**Architecture:** Async end-to-end. SQLAlchemy 2.0 async ORM (asyncpg) with Alembic migrations; Postgres holds runs, equity curve, positions, metrics, and index membership (OHLC stays in Plan 1's Parquet cache). FastAPI app with a session-per-request dependency; backtests execute via `BackgroundTasks` (queue-ready later) and persist through a repository. The engine and strategy from Plan 1 are reused unchanged — only the universe *loader* moves from CSV to DB. Tests run against a real Postgres via testcontainers (no SQLite masking).

**Tech Stack:** Python 3.12+ · FastAPI · SQLAlchemy 2.0 async + asyncpg · Alembic · Pydantic v2 · pytest-asyncio · httpx (ASGITransport) · testcontainers[postgresql].

## Global Constraints

- **Python 3.12+**; all code fully type-annotated (real types, not `object`/`Any`).
- **Async throughout:** async SQLAlchemy (`create_async_engine`, `async_sessionmaker`, `AsyncSession`), async FastAPI handlers. No sync DB calls in request paths.
- **Tests against REAL Postgres** via `testcontainers[postgresql]` — **Docker daemon required at execution time** (pre-flight check before Task 0). No SQLite fallback.
- **`UniverseProvider` interface is unchanged** — Plan 1's `IndexMembershipProvider` (`members_as_of`, `union_over`, both sync) is reused; only a new async *loader* reads intervals from Postgres instead of CSV.
- **OHLC is NOT stored in Postgres** — Parquet remains the OHLC store (spec §17).
- **Backtest runs execute via FastAPI `BackgroundTasks`**; `POST /backtests` returns a `run_id` immediately with status `pending`.
- **Run status lifecycle:** `pending → running → completed | failed`.
- **Secrets via env only** (`DATABASE_URL`, `TV_USERNAME`, `TV_PASSWORD`); never committed.
- **Pristine test output** — zero warnings.
- **Commits** authored "Arpitha B", empty email, no `Co-Authored-By`. Commit from repo root `D:\gs\workspace\tvagent` with explicit `backend/...` paths. Branch off `main`.
- **API surface (spec §10):** `POST /backtests`, `GET /backtests/{id}`, `GET /backtests/{id}/equity`, `GET /backtests/{id}/holdings`, `GET /backtests/{id}/metrics`, `GET /universe/{index}?as_of=`, `POST /data/sync`.

---

## File Structure

```
backend/
  pyproject.toml                       # MODIFY: add async-db + api + test deps
  alembic.ini                          # NEW
  alembic/
    env.py                             # NEW (async migrations)
    versions/0001_initial.py           # NEW
  app/
    core/config.py                     # MODIFY: add database_url
    db/
      __init__.py                      # NEW
      base.py                          # NEW: Base, make_engine, make_session_factory
      models.py                        # NEW: ORM models
    data/universe/
      provider.py                      # MODIFY: add intervals_by_index accessor
      db_loader.py                     # NEW: load_provider_from_db, load_seed_into_db
    repository/
      __init__.py                      # NEW
      runs.py                          # NEW: BacktestRunRepository
    api/
      __init__.py                      # NEW
      app.py                           # NEW: create_app() factory + lifespan
      deps.py                          # NEW: get_session, get_settings, AppState
      schemas.py                       # NEW: Pydantic request/response DTOs
      runner.py                        # NEW: execute_backtest background fn
      routers/
        __init__.py                    # NEW
        backtests.py                   # NEW
        universe.py                    # NEW
        data.py                        # NEW
  tests/
    conftest.py                        # MODIFY: add postgres container + session fixtures
    db/__init__.py                     # NEW
    db/test_models.py                  # NEW
    data/test_db_loader.py             # NEW
    repository/__init__.py             # NEW
    repository/test_runs.py            # NEW
    api/__init__.py                    # NEW
    api/test_health.py                 # NEW
    api/test_backtests.py              # NEW
    api/test_universe.py               # NEW
    api/test_data_sync.py              # NEW
```

---

### Task 0: Async DB foundation + Postgres test fixtures

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/core/config.py`
- Create: `backend/app/db/__init__.py`, `backend/app/db/base.py`
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/db/__init__.py`, `backend/tests/db/test_models.py` (connectivity test only here; models in Task 1)

**Interfaces:**
- Produces: `app.db.base.Base` (a `DeclarativeBase` subclass), `make_engine(database_url: str) -> AsyncEngine`, `make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]`.
- Produces (test fixtures in `conftest.py`): `pg_url` (session-scoped `str`, asyncpg URL to a live container), `engine` (function-scoped `AsyncEngine`), `session` (function-scoped `AsyncSession` with tables created/dropped per test).
- Produces: `Settings.database_url: str` (default `postgresql+asyncpg://tvagent:tvagent@localhost:5432/tvagent`, env `DATABASE_URL`).

- [ ] **Step 1: Add dependencies to `backend/pyproject.toml`**

In `[project].dependencies` add:
```toml
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
```
In `[project.optional-dependencies].dev` add:
```toml
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    "testcontainers[postgresql]>=4.0",
    "psycopg2-binary>=2.9",
```
In `[tool.pytest.ini_options]` add the line:
```toml
asyncio_mode = "auto"
```

- [ ] **Step 2: Install and confirm Docker is available**

Run: `.venv/Scripts/pip install -e ".[dev]"` then `docker info`
Expected: install succeeds; `docker info` prints server info (Docker daemon running). If `docker info` fails, STOP and report BLOCKED — testcontainers cannot run without Docker.

- [ ] **Step 3: Add `database_url` to `app/core/config.py`**

Add this field to `Settings` (keep existing fields/properties):
```python
    database_url: str = "postgresql+asyncpg://tvagent:tvagent@localhost:5432/tvagent"
```

- [ ] **Step 4: Write `app/db/base.py`**

```python
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, future=True, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 5: Add Postgres fixtures to `tests/conftest.py`**

Append (keep existing fixtures):
```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.postgres import PostgresContainer
from app.db.base import Base, make_engine, make_session_factory


@pytest.fixture(scope="session")
def pg_url() -> str:
    with PostgresContainer("postgres:16-alpine") as pg:
        raw = pg.get_connection_url()  # e.g. postgresql+psycopg2://...
        yield raw.replace("postgresql+psycopg2", "postgresql+asyncpg")


@pytest_asyncio.fixture
async def engine(pg_url: str) -> AsyncEngine:
    eng = make_engine(pg_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncSession:
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
```

- [ ] **Step 6: Write the connectivity test** (`tests/db/test_models.py`)

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_database_roundtrip(session: AsyncSession):
    result = await session.execute(text("select 1"))
    assert result.scalar_one() == 1
```

- [ ] **Step 7: Run the test**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/db/test_models.py -v`
Expected: PASS (a Postgres container starts, `select 1` returns 1). First run is slow (image pull).

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/app/core/config.py backend/app/db backend/tests/conftest.py backend/tests/db
git commit -m "feat(db): async SQLAlchemy base + Postgres testcontainer fixtures"
```

---

### Task 1: ORM models + Alembic migration

**Files:**
- Create: `backend/app/db/models.py`
- Create: `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/0001_initial.py`
- Modify: `backend/tests/db/test_models.py` (add model persistence test)

**Interfaces:**
- Produces ORM models (all on `app.db.base.Base`):
  - `IndexMembershipRow`: `id:int pk`, `index_name:str`, `symbol:str`, `effective_from:date|None`, `effective_to:date|None`.
  - `BacktestRun`: `id:int pk`, `status:str` (default `"pending"`), `config:dict` (JSON), `created_at:datetime` (server default now), `error:str|None`, `warnings:list` (JSON, default list).
  - `EquityPoint`: `id:int pk`, `run_id:int fk→backtest_runs.id ondelete CASCADE`, `on_date:date`, `equity:float`, `benchmark:float|None`.
  - `RunPosition`: `id:int pk`, `run_id:int fk CASCADE`, `rebalance_date:date`, `symbol:str`, `weight:float`.
  - `RunMetrics`: `id:int pk`, `run_id:int fk CASCADE unique`, `data:dict` (JSON).
  - `BacktestRun` has relationships: `metrics` (one-to-one), `equity_points` (one-to-many), `positions` (one-to-many), all `cascade="all, delete-orphan"`.

- [ ] **Step 1: Write the failing test** (append to `tests/db/test_models.py`)

```python
from datetime import date
from sqlalchemy import select
from app.db.models import BacktestRun, EquityPoint, RunMetrics, RunPosition, IndexMembershipRow


async def test_run_with_children_persists_and_cascades(session: AsyncSession):
    run = BacktestRun(status="completed", config={"index": "NIFTY50"}, warnings=["w1"])
    run.equity_points.append(EquityPoint(on_date=date(2024, 1, 1), equity=1000.0, benchmark=1000.0))
    run.positions.append(RunPosition(rebalance_date=date(2024, 1, 1), symbol="AAA", weight=0.5))
    run.metrics = RunMetrics(data={"sharpe": 1.2})
    session.add(run)
    await session.commit()

    fetched = (await session.execute(select(BacktestRun).where(BacktestRun.id == run.id))).scalar_one()
    assert fetched.status == "completed"
    assert fetched.config["index"] == "NIFTY50"

    await session.delete(fetched)
    await session.commit()
    assert (await session.execute(select(EquityPoint))).first() is None  # cascade deleted


async def test_membership_row_persists(session: AsyncSession):
    session.add(IndexMembershipRow(index_name="NIFTY50", symbol="AAA",
                                   effective_from=None, effective_to=date(2022, 3, 31)))
    await session.commit()
    row = (await session.execute(select(IndexMembershipRow))).scalar_one()
    assert row.index_name == "NIFTY50" and row.effective_to == date(2022, 3, 31)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/db/test_models.py -v`
Expected: FAIL — `app.db.models` not found.

- [ ] **Step 3: Write `app/db/models.py`**

```python
from datetime import date, datetime
from sqlalchemy import Date, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class IndexMembershipRow(Base):
    __tablename__ = "index_membership"
    id: Mapped[int] = mapped_column(primary_key=True)
    index_name: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    config: Mapped[dict] = mapped_column(type_=__import__("sqlalchemy").JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    warnings: Mapped[list] = mapped_column(type_=__import__("sqlalchemy").JSON, default=list)

    metrics: Mapped["RunMetrics | None"] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan")
    equity_points: Mapped[list["EquityPoint"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")
    positions: Mapped[list["RunPosition"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")


class EquityPoint(Base):
    __tablename__ = "equity_curve"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True)
    on_date: Mapped[date] = mapped_column(Date)
    equity: Mapped[float] = mapped_column(Float)
    benchmark: Mapped[float | None] = mapped_column(Float, nullable=True)
    run: Mapped[BacktestRun] = relationship(back_populates="equity_points")


class RunPosition(Base):
    __tablename__ = "backtest_positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True)
    rebalance_date: Mapped[date] = mapped_column(Date)
    symbol: Mapped[str] = mapped_column(String(64))
    weight: Mapped[float] = mapped_column(Float)
    run: Mapped[BacktestRun] = relationship(back_populates="positions")


class RunMetrics(Base):
    __tablename__ = "metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), unique=True)
    data: Mapped[dict] = mapped_column(type_=__import__("sqlalchemy").JSON)
    run: Mapped[BacktestRun] = relationship(back_populates="metrics")
```
Note: use a clean top import `from sqlalchemy import JSON` and reference `JSON` directly instead of the `__import__` shim — the shim is shown only to be explicit about the type; replace it with a proper `JSON` import in the final code.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/db/test_models.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Create Alembic config for async migrations**

`backend/alembic.ini` (minimal):
```ini
[alembic]
script_location = alembic
sqlalchemy.url =

[loggers]
keys = root
[handlers]
keys =
[formatters]
keys =
[logger_root]
level = WARN
handlers =
qualname =
```

`backend/alembic/env.py`:
```python
import asyncio
from logging.config import fileConfig
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings
from app.db.base import Base
import app.db.models  # noqa: F401 — register models on Base.metadata

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

`backend/alembic/versions/0001_initial.py` — generate it: run `.venv/Scripts/python -m alembic revision --autogenerate -m "initial"` against a running Postgres (use the container or a local one via `DATABASE_URL`). Commit the generated file. If autogenerate cannot connect in this environment, hand-write the `upgrade()`/`downgrade()` creating the five tables matching the models above (columns, types, FKs with `ondelete="CASCADE"`, the `metrics.run_id` unique constraint, indexes on `index_membership.index_name`/`symbol` and the `run_id` FKs). Verify it imports: `.venv/Scripts/python -c "import alembic.versions"` is not valid — instead verify with `.venv/Scripts/python -m alembic check` or by importing the version module path directly.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db/models.py backend/alembic.ini backend/alembic backend/tests/db/test_models.py
git commit -m "feat(db): ORM models for runs/equity/positions/metrics/membership + alembic"
```

---

### Task 2: Universe DB loader (CSV → Postgres, DB → provider)

**Files:**
- Modify: `backend/app/data/universe/provider.py` (add accessor)
- Create: `backend/app/data/universe/db_loader.py`
- Create: `backend/tests/data/test_db_loader.py`

**Interfaces:**
- Consumes: `IndexMembershipProvider`, `MembershipInterval` (Plan 1), `load_provider_from_seed` (Plan 1), `IndexMembershipRow` (Task 1).
- Produces: `IndexMembershipProvider.intervals_by_index() -> dict[str, list[MembershipInterval]]` (public read accessor returning the internal mapping).
- Produces: `async def load_seed_into_db(session: AsyncSession, seed_dir: Path, anchor: date) -> int` — reads CSV seed via `load_provider_from_seed`, inserts one `IndexMembershipRow` per interval, returns the row count inserted. Idempotency is NOT required (caller controls); it appends.
- Produces: `async def load_provider_from_db(session: AsyncSession) -> IndexMembershipProvider` — reads all `IndexMembershipRow` rows, groups into `MembershipInterval` lists per `index_name`, returns an `IndexMembershipProvider`. Same sync `members_as_of`/`union_over` interface — engine code is unchanged.

- [ ] **Step 1: Write the failing test** (`tests/data/test_db_loader.py`)

```python
from datetime import date
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from app.data.universe.db_loader import load_seed_into_db, load_provider_from_db


async def test_seed_roundtrip_through_db(session: AsyncSession, tmp_path: Path):
    (tmp_path / "nifty50_current.csv").write_text("symbol\nAAA\nNEW\n")
    (tmp_path / "nifty50_changelog.csv").write_text(
        "effective_date,added,removed,source_url\n2022-03-31,NEW,OLD,https://x\n")

    count = await load_seed_into_db(session, tmp_path, anchor=date(2026, 1, 1))
    assert count >= 3  # AAA (open), NEW (from 2022-03-31), OLD (to 2022-03-31)

    provider = await load_provider_from_db(session)
    assert provider.members_as_of("NIFTY50", date(2021, 1, 1)) == {"AAA", "OLD"}
    assert "NEW" in provider.members_as_of("NIFTY50", date(2023, 1, 1))
    assert provider.union_over("NIFTY50", date(2021, 1, 1), date(2023, 1, 1)) == {"AAA", "OLD", "NEW"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/data/test_db_loader.py -v`
Expected: FAIL — `app.data.universe.db_loader` not found.

- [ ] **Step 3: Add the accessor to `provider.py`**

Add this method to `IndexMembershipProvider`:
```python
    def intervals_by_index(self) -> dict[str, list[MembershipInterval]]:
        return self._by_index
```

- [ ] **Step 4: Write `app/data/universe/db_loader.py`**

```python
from datetime import date
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.data.universe.membership import MembershipInterval
from app.data.universe.provider import IndexMembershipProvider, load_provider_from_seed
from app.db.models import IndexMembershipRow


async def load_seed_into_db(session: AsyncSession, seed_dir: Path, anchor: date) -> int:
    provider = load_provider_from_seed(seed_dir, anchor)
    count = 0
    for index_name, intervals in provider.intervals_by_index().items():
        for iv in intervals:
            session.add(IndexMembershipRow(
                index_name=index_name, symbol=iv.symbol,
                effective_from=iv.effective_from, effective_to=iv.effective_to))
            count += 1
    await session.commit()
    return count


async def load_provider_from_db(session: AsyncSession) -> IndexMembershipProvider:
    rows = (await session.execute(select(IndexMembershipRow))).scalars().all()
    by_index: dict[str, list[MembershipInterval]] = {}
    for r in rows:
        by_index.setdefault(r.index_name, []).append(
            MembershipInterval(r.symbol, r.effective_from, r.effective_to))
    return IndexMembershipProvider(by_index)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/data/test_db_loader.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/data/universe/provider.py backend/app/data/universe/db_loader.py backend/tests/data/test_db_loader.py
git commit -m "feat(universe): migrate membership CSV seed into Postgres behind unchanged provider"
```

---

### Task 3: BacktestRunRepository

**Files:**
- Create: `backend/app/repository/__init__.py`, `backend/app/repository/runs.py`
- Create: `backend/tests/repository/__init__.py`, `backend/tests/repository/test_runs.py`

**Interfaces:**
- Consumes: ORM models (Task 1).
- Produces: `BacktestRunRepository(session: AsyncSession)` with async methods:
  - `create(config: dict) -> int` — insert a `pending` run, return its id.
  - `mark_running(run_id: int) -> None`.
  - `save_results(run_id: int, equity: list[tuple[date, float, float | None]], positions: list[tuple[date, str, float]], metrics: dict, warnings: list[str]) -> None` — persist children, set status `completed`.
  - `mark_failed(run_id: int, error: str) -> None`.
  - `get(run_id: int) -> BacktestRun | None`.
  - `get_equity(run_id: int) -> list[EquityPoint]` (ordered by `on_date`).
  - `get_positions(run_id: int) -> list[RunPosition]` (ordered by `rebalance_date`, `symbol`).
  - `get_metrics(run_id: int) -> RunMetrics | None`.

- [ ] **Step 1: Write the failing test** (`tests/repository/test_runs.py`)

```python
from datetime import date
from sqlalchemy.ext.asyncio import AsyncSession
from app.repository.runs import BacktestRunRepository


async def test_create_then_complete_lifecycle(session: AsyncSession):
    repo = BacktestRunRepository(session)
    run_id = await repo.create({"index": "NIFTY50", "top_n": 10})
    assert isinstance(run_id, int)
    assert (await repo.get(run_id)).status == "pending"

    await repo.mark_running(run_id)
    assert (await repo.get(run_id)).status == "running"

    await repo.save_results(
        run_id,
        equity=[(date(2024, 1, 1), 1000.0, 1000.0), (date(2024, 1, 2), 1010.0, 1005.0)],
        positions=[(date(2024, 1, 1), "AAA", 0.5), (date(2024, 1, 1), "BBB", 0.5)],
        metrics={"sharpe": 1.5, "cagr": 0.2},
        warnings=["skipped X"],
    )
    run = await repo.get(run_id)
    assert run.status == "completed" and run.warnings == ["skipped X"]
    assert len(await repo.get_equity(run_id)) == 2
    assert len(await repo.get_positions(run_id)) == 2
    assert (await repo.get_metrics(run_id)).data["sharpe"] == 1.5


async def test_mark_failed(session: AsyncSession):
    repo = BacktestRunRepository(session)
    run_id = await repo.create({"index": "NIFTY50"})
    await repo.mark_failed(run_id, "boom")
    run = await repo.get(run_id)
    assert run.status == "failed" and run.error == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/repository/test_runs.py -v`
Expected: FAIL — `app.repository.runs` not found.

- [ ] **Step 3: Write `app/repository/runs.py`**

```python
from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import BacktestRun, EquityPoint, RunMetrics, RunPosition


class BacktestRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, config: dict) -> int:
        run = BacktestRun(status="pending", config=config, warnings=[])
        self._s.add(run)
        await self._s.commit()
        await self._s.refresh(run)
        return run.id

    async def mark_running(self, run_id: int) -> None:
        run = await self._s.get(BacktestRun, run_id)
        run.status = "running"
        await self._s.commit()

    async def save_results(
        self,
        run_id: int,
        equity: list[tuple[date, float, float | None]],
        positions: list[tuple[date, str, float]],
        metrics: dict,
        warnings: list[str],
    ) -> None:
        run = await self._s.get(BacktestRun, run_id)
        for d, eq, bench in equity:
            self._s.add(EquityPoint(run_id=run_id, on_date=d, equity=eq, benchmark=bench))
        for d, sym, w in positions:
            self._s.add(RunPosition(run_id=run_id, rebalance_date=d, symbol=sym, weight=w))
        self._s.add(RunMetrics(run_id=run_id, data=metrics))
        run.warnings = warnings
        run.status = "completed"
        await self._s.commit()

    async def mark_failed(self, run_id: int, error: str) -> None:
        run = await self._s.get(BacktestRun, run_id)
        run.status = "failed"
        run.error = error
        await self._s.commit()

    async def get(self, run_id: int) -> BacktestRun | None:
        return await self._s.get(BacktestRun, run_id)

    async def get_equity(self, run_id: int) -> list[EquityPoint]:
        stmt = select(EquityPoint).where(EquityPoint.run_id == run_id).order_by(EquityPoint.on_date)
        return list((await self._s.execute(stmt)).scalars().all())

    async def get_positions(self, run_id: int) -> list[RunPosition]:
        stmt = (select(RunPosition).where(RunPosition.run_id == run_id)
                .order_by(RunPosition.rebalance_date, RunPosition.symbol))
        return list((await self._s.execute(stmt)).scalars().all())

    async def get_metrics(self, run_id: int) -> RunMetrics | None:
        stmt = select(RunMetrics).where(RunMetrics.run_id == run_id)
        return (await self._s.execute(stmt)).scalar_one_or_none()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/repository/test_runs.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repository backend/tests/repository
git commit -m "feat(repository): async BacktestRunRepository for run persistence"
```

---

### Task 4: FastAPI app factory + session dependency + schemas + health

**Files:**
- Create: `backend/app/api/__init__.py`, `backend/app/api/app.py`, `backend/app/api/deps.py`, `backend/app/api/schemas.py`
- Create: `backend/app/api/routers/__init__.py`
- Create: `backend/tests/api/__init__.py`, `backend/tests/api/test_health.py`

**Interfaces:**
- Produces: `create_app(session_factory: async_sessionmaker[AsyncSession], *, source_factory: Callable[[], DataSource] | None = None) -> FastAPI`. `session_factory` and `source_factory` are stored on `app.state` so tests can inject a container-backed factory + a stub `DataSource`. A `GET /health` returns `{"status": "ok"}`.
- Produces: `app.api.deps.get_session(request: Request) -> AsyncIterator[AsyncSession]` (FastAPI dependency yielding a session from `app.state.session_factory`).
- Produces Pydantic schemas in `schemas.py`: `RunRequest` (mirrors `BacktestConfig` fields with the same defaults), `RunCreated` (`run_id: int`, `status: str`), `RunStatus` (`run_id, status, created_at, warnings, error`), `EquityResponse` (`points: list[EquityPointOut]` where `EquityPointOut` = `date, equity, benchmark`), `HoldingsResponse` (`positions: list[PositionOut]`), `MetricsResponse` (`run_id: int, metrics: dict`).

- [ ] **Step 1: Write the failing test** (`tests/api/test_health.py`)

```python
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from app.api.app import create_app
from app.db.base import make_session_factory


@pytest_asyncio.fixture
async def client(engine):
    app = create_app(make_session_factory(engine))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/api/test_health.py -v`
Expected: FAIL — `app.api.app` not found.

- [ ] **Step 3: Write `schemas.py`**

```python
from datetime import date, datetime
from pydantic import BaseModel


class RunRequest(BaseModel):
    index: str = "NIFTY50"
    start: date
    end: date
    starting_capital: float = 1_000_000
    lookback: int = 22
    top_pct: float = 0.5
    top_n: int = 10
    nominal_size: int = 50
    cost_bps: float = 0.0


class RunCreated(BaseModel):
    run_id: int
    status: str


class RunStatus(BaseModel):
    run_id: int
    status: str
    created_at: datetime
    warnings: list[str]
    error: str | None


class EquityPointOut(BaseModel):
    date: date
    equity: float
    benchmark: float | None


class EquityResponse(BaseModel):
    run_id: int
    points: list[EquityPointOut]


class PositionOut(BaseModel):
    rebalance_date: date
    symbol: str
    weight: float


class HoldingsResponse(BaseModel):
    run_id: int
    positions: list[PositionOut]


class MetricsResponse(BaseModel):
    run_id: int
    metrics: dict
```

- [ ] **Step 4: Write `deps.py`**

```python
from collections.abc import AsyncIterator
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session
```

- [ ] **Step 5: Write `app.py`**

```python
from collections.abc import Callable
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.data.sources.base import DataSource


def create_app(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    source_factory: Callable[[], DataSource] | None = None,
) -> FastAPI:
    app = FastAPI(title="tvagent API")
    app.state.session_factory = session_factory
    app.state.source_factory = source_factory

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/api/test_health.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api backend/tests/api/__init__.py backend/tests/api/test_health.py
git commit -m "feat(api): FastAPI app factory, session dependency, schemas, health"
```

---

### Task 5: Background runner + POST /backtests + GET /backtests/{id}

**Files:**
- Create: `backend/app/api/runner.py`
- Create: `backend/app/api/routers/backtests.py`
- Modify: `backend/app/api/app.py` (include the backtests router)
- Create: `backend/tests/api/test_backtests.py`

**Interfaces:**
- Consumes: `BacktestRunRepository` (Task 3), `load_provider_from_db` (Task 2), `BacktestEngine`/`BacktestConfig`/`RSMomentumStrategy` (Plan 1), `DataSource` (Plan 1), app state (`session_factory`, `source_factory`).
- Produces: `async def execute_backtest(session_factory, source_factory, run_id: int, cfg: BacktestConfig) -> None` — opens its OWN session (background tasks run after the request session closes), marks running, loads the DB provider, builds `BacktestEngine(source_factory(), provider, RSMomentumStrategy())`, runs (the sync `engine.run` is offloaded via `anyio.to_thread.run_sync` to avoid blocking the event loop), converts the `BacktestResult` to repository inputs, calls `save_results`; on exception calls `mark_failed`.
- Produces router: `POST /backtests` (body `RunRequest`) → creates a `pending` run, schedules `execute_backtest` via `BackgroundTasks`, returns `RunCreated` (201). `GET /backtests/{run_id}` → `RunStatus` (404 if missing).

- [ ] **Step 1: Write the failing test** (`tests/api/test_backtests.py`)

```python
import asyncio
from datetime import date
import numpy as np
import pandas as pd
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from app.api.app import create_app
from app.db.base import make_session_factory
from app.data.universe.db_loader import load_seed_into_db


class _StubSource:
    """Synthetic OHLC: 6 ramping stocks + flat NIFTY, daily for ~4 months."""
    def __init__(self) -> None:
        self._idx = pd.bdate_range("2024-01-01", periods=80)

    def get_ohlc(self, symbol, exchange, interval, start, end):
        if symbol == "NIFTY":
            close = pd.Series(100.0, index=self._idx)
        else:
            i = int(symbol[1:]) if symbol[1:].isdigit() else 1
            close = pd.Series(100.0 + i * np.arange(len(self._idx)), index=self._idx)
        return pd.DataFrame({"close": close})


@pytest_asyncio.fixture
async def client(engine, tmp_path):
    # seed membership: S0..S5 as current NIFTY50 members
    factory = make_session_factory(engine)
    (tmp_path / "nifty50_current.csv").write_text("symbol\n" + "\n".join(f"S{i}" for i in range(6)) + "\n")
    async with factory() as s:
        await load_seed_into_db(s, tmp_path, anchor=date(2026, 1, 1))
    app = create_app(factory, source_factory=lambda: _StubSource())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_create_and_poll_backtest(client: AsyncClient):
    body = {"index": "NIFTY50", "start": "2024-01-01", "end": "2024-04-19",
            "top_n": 2, "nominal_size": 6}
    resp = await client.post("/backtests", json=body)
    assert resp.status_code == 201
    run_id = resp.json()["run_id"]
    assert resp.json()["status"] == "pending"

    # poll until terminal
    for _ in range(50):
        status = (await client.get(f"/backtests/{run_id}")).json()["status"]
        if status in ("completed", "failed"):
            break
        await asyncio.sleep(0.05)
    assert status == "completed"


async def test_get_missing_run_404(client: AsyncClient):
    assert (await client.get("/backtests/99999")).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/api/test_backtests.py -v`
Expected: FAIL — router/runner not found.

- [ ] **Step 3: Write `app/api/runner.py`**

```python
import anyio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.backtest.engine import BacktestEngine
from app.data.universe.db_loader import load_provider_from_db
from app.repository.runs import BacktestRunRepository
from app.strategies.base import BacktestConfig
from app.strategies.rs_momentum import RSMomentumStrategy


async def execute_backtest(
    session_factory: async_sessionmaker[AsyncSession],
    source_factory,
    run_id: int,
    cfg: BacktestConfig,
) -> None:
    async with session_factory() as session:
        repo = BacktestRunRepository(session)
        try:
            await repo.mark_running(run_id)
            provider = await load_provider_from_db(session)
            engine = BacktestEngine(source_factory(), provider, RSMomentumStrategy())
            result = await anyio.to_thread.run_sync(engine.run, cfg)

            equity = [(ts.date(), float(v),
                       float(result.benchmark.get(ts)) if ts in result.benchmark.index else None)
                      for ts, v in result.equity.items()]
            positions = [(d, sym, float(w)) for d, weights in result.holdings
                         for sym, w in weights.items()]
            await repo.save_results(run_id, equity, positions, result.metrics, result.warnings)
        except Exception as exc:  # noqa: BLE001 — record failure, never crash the worker
            await repo.mark_failed(run_id, repr(exc))
```

- [ ] **Step 4: Write `app/api/routers/backtests.py`**

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_session
from app.api.runner import execute_backtest
from app.api.schemas import RunCreated, RunRequest, RunStatus
from app.repository.runs import BacktestRunRepository
from app.strategies.base import BacktestConfig

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("", response_model=RunCreated, status_code=status.HTTP_201_CREATED)
async def create_backtest(
    body: RunRequest,
    background: BackgroundTasks,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RunCreated:
    repo = BacktestRunRepository(session)
    run_id = await repo.create(body.model_dump(mode="json"))
    cfg = BacktestConfig(**body.model_dump())
    background.add_task(
        execute_backtest,
        request.app.state.session_factory,
        request.app.state.source_factory,
        run_id,
        cfg,
    )
    return RunCreated(run_id=run_id, status="pending")


@router.get("/{run_id}", response_model=RunStatus)
async def get_backtest(run_id: int, session: AsyncSession = Depends(get_session)) -> RunStatus:
    run = await BacktestRunRepository(session).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunStatus(run_id=run.id, status=run.status, created_at=run.created_at,
                     warnings=run.warnings, error=run.error)
```

- [ ] **Step 5: Include the router in `app.py`**

In `create_app`, after creating `app` and before returning, add:
```python
    from app.api.routers import backtests
    app.include_router(backtests.router)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/api/test_backtests.py -v`
Expected: PASS (2 tests). The background task completes the run; polling sees `completed`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/runner.py backend/app/api/routers/backtests.py backend/app/api/app.py backend/tests/api/test_backtests.py
git commit -m "feat(api): POST /backtests with background runner + GET status"
```

---

### Task 6: Result endpoints — equity, holdings, metrics

**Files:**
- Modify: `backend/app/api/routers/backtests.py`
- Create: `backend/tests/api/test_results.py`

**Interfaces:**
- Consumes: `BacktestRunRepository.get_equity/get_positions/get_metrics`, schemas `EquityResponse`, `HoldingsResponse`, `MetricsResponse`.
- Produces three routes (all 404 if the run is missing): `GET /backtests/{run_id}/equity` → `EquityResponse`; `GET /backtests/{run_id}/holdings` → `HoldingsResponse`; `GET /backtests/{run_id}/metrics` → `MetricsResponse`.

- [ ] **Step 1: Write the failing test** (`tests/api/test_results.py`)

```python
import asyncio
from httpx import AsyncClient
import pytest_asyncio
# reuse the client fixture pattern from test_backtests via a local import
from tests.api.test_backtests import client  # noqa: F401 — pytest fixture reuse


async def _completed_run(client: AsyncClient) -> int:
    body = {"index": "NIFTY50", "start": "2024-01-01", "end": "2024-04-19",
            "top_n": 2, "nominal_size": 6}
    run_id = (await client.post("/backtests", json=body)).json()["run_id"]
    for _ in range(50):
        if (await client.get(f"/backtests/{run_id}")).json()["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.05)
    return run_id


async def test_equity_holdings_metrics(client: AsyncClient):
    run_id = await _completed_run(client)

    eq = await client.get(f"/backtests/{run_id}/equity")
    assert eq.status_code == 200 and len(eq.json()["points"]) > 0
    assert {"date", "equity", "benchmark"} <= eq.json()["points"][0].keys()

    hold = await client.get(f"/backtests/{run_id}/holdings")
    assert hold.status_code == 200 and len(hold.json()["positions"]) > 0

    met = await client.get(f"/backtests/{run_id}/metrics")
    assert met.status_code == 200 and "sharpe" in met.json()["metrics"]


async def test_results_404_for_missing(client: AsyncClient):
    assert (await client.get("/backtests/99999/equity")).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/api/test_results.py -v`
Expected: FAIL — routes not defined (404 for equity on a real run, or AttributeError).

- [ ] **Step 3: Add the routes to `routers/backtests.py`**

Add imports at top: `from app.api.schemas import EquityResponse, EquityPointOut, HoldingsResponse, PositionOut, MetricsResponse`. Then append:
```python
async def _require_run(session: AsyncSession, run_id: int):
    run = await BacktestRunRepository(session).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get("/{run_id}/equity", response_model=EquityResponse)
async def get_equity(run_id: int, session: AsyncSession = Depends(get_session)) -> EquityResponse:
    await _require_run(session, run_id)
    pts = await BacktestRunRepository(session).get_equity(run_id)
    return EquityResponse(run_id=run_id, points=[
        EquityPointOut(date=p.on_date, equity=p.equity, benchmark=p.benchmark) for p in pts])


@router.get("/{run_id}/holdings", response_model=HoldingsResponse)
async def get_holdings(run_id: int, session: AsyncSession = Depends(get_session)) -> HoldingsResponse:
    await _require_run(session, run_id)
    pos = await BacktestRunRepository(session).get_positions(run_id)
    return HoldingsResponse(run_id=run_id, positions=[
        PositionOut(rebalance_date=p.rebalance_date, symbol=p.symbol, weight=p.weight) for p in pos])


@router.get("/{run_id}/metrics", response_model=MetricsResponse)
async def get_metrics(run_id: int, session: AsyncSession = Depends(get_session)) -> MetricsResponse:
    await _require_run(session, run_id)
    m = await BacktestRunRepository(session).get_metrics(run_id)
    return MetricsResponse(run_id=run_id, metrics=m.data if m else {})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/api/test_results.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routers/backtests.py backend/tests/api/test_results.py
git commit -m "feat(api): equity, holdings, and metrics result endpoints"
```

---

### Task 7: Universe endpoint + data-sync endpoint

**Files:**
- Create: `backend/app/api/routers/universe.py`, `backend/app/api/routers/data.py`
- Modify: `backend/app/api/app.py` (include routers), `backend/app/api/schemas.py` (add `UniverseResponse`, `SyncResponse`)
- Create: `backend/tests/api/test_universe.py`

**Interfaces:**
- Consumes: `load_provider_from_db` (Task 2), `load_seed_into_db` (Task 2), `settings.seed_dir`, app state.
- Produces schemas: `UniverseResponse` (`index: str, as_of: date, members: list[str]`), `SyncResponse` (`inserted: int`).
- Produces routes: `GET /universe/{index}?as_of=YYYY-MM-DD` → members as of that date (defaults to today when omitted) → `UniverseResponse`. `POST /data/sync` → loads the committed CSV seed (`settings.seed_dir`) into `index_membership`, returns `SyncResponse` with the inserted row count.

- [ ] **Step 1: Write the failing test** (`tests/api/test_universe.py`)

```python
from datetime import date
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from app.api.app import create_app
from app.db.base import make_session_factory
from app.data.universe.db_loader import load_seed_into_db


@pytest_asyncio.fixture
async def client(engine, tmp_path):
    factory = make_session_factory(engine)
    (tmp_path / "nifty50_current.csv").write_text("symbol\nAAA\nNEW\n")
    (tmp_path / "nifty50_changelog.csv").write_text(
        "effective_date,added,removed,source_url\n2022-03-31,NEW,OLD,https://x\n")
    async with factory() as s:
        await load_seed_into_db(s, tmp_path, anchor=date(2026, 1, 1))
    app = create_app(factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_universe_as_of(client: AsyncClient):
    resp = await client.get("/universe/NIFTY50", params={"as_of": "2021-01-01"})
    assert resp.status_code == 200
    assert set(resp.json()["members"]) == {"AAA", "OLD"}


async def test_universe_default_as_of_today(client: AsyncClient):
    resp = await client.get("/universe/NIFTY50")
    assert resp.status_code == 200
    assert "AAA" in resp.json()["members"]  # AAA is a current member
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/api/test_universe.py -v`
Expected: FAIL — route not found (404/AttributeError).

- [ ] **Step 3: Add schemas to `schemas.py`**

```python
class UniverseResponse(BaseModel):
    index: str
    as_of: date
    members: list[str]


class SyncResponse(BaseModel):
    inserted: int
```

- [ ] **Step 4: Write `routers/universe.py`**

```python
from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_session
from app.api.schemas import UniverseResponse
from app.data.universe.db_loader import load_provider_from_db

router = APIRouter(prefix="/universe", tags=["universe"])


@router.get("/{index}", response_model=UniverseResponse)
async def get_universe(
    index: str,
    request: Request,
    as_of: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> UniverseResponse:
    on = as_of or datetime.now(timezone.utc).date()
    provider = await load_provider_from_db(session)
    members = sorted(provider.members_as_of(index, on))
    return UniverseResponse(index=index, as_of=on, members=members)
```
Note: `datetime.now(timezone.utc).date()` is used (not a bare `date.today()`) for explicit UTC; this is production code, not a workflow script, so real clock access is fine here.

- [ ] **Step 5: Write `routers/data.py`**

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from app.api.deps import get_session
from app.api.schemas import SyncResponse
from app.core.config import settings
from app.data.universe.db_loader import load_seed_into_db

router = APIRouter(prefix="/data", tags=["data"])


@router.post("/sync", response_model=SyncResponse)
async def sync_seed(request: Request, session: AsyncSession = Depends(get_session)) -> SyncResponse:
    anchor = datetime.now(timezone.utc).date()
    inserted = await load_seed_into_db(session, settings.seed_dir, anchor)
    return SyncResponse(inserted=inserted)
```

- [ ] **Step 6: Include routers in `app.py`**

In `create_app`, alongside the backtests include:
```python
    from app.api.routers import backtests, data, universe
    app.include_router(backtests.router)
    app.include_router(universe.router)
    app.include_router(data.router)
```
(Replace the single backtests import line from Task 5 with this combined block.)

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/api/test_universe.py -v`
Expected: PASS (2 tests).

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/routers/universe.py backend/app/api/routers/data.py backend/app/api/app.py backend/app/api/schemas.py backend/tests/api/test_universe.py
git commit -m "feat(api): universe (point-in-time) and data-sync endpoints"
```

---

### Task 8: ASGI entrypoint + full-suite gate

**Files:**
- Create: `backend/app/api/asgi.py`
- Create: `backend/tests/api/test_data_sync.py`

**Interfaces:**
- Produces: `app.api.asgi:app` — a module-level `FastAPI` instance built from production settings (`make_engine(settings.database_url)` → `make_session_factory`, and a `source_factory` that builds `CachedDataSource(TvDatafeedSource(...), ParquetCache(settings.cache_dir))`). Suitable for `uvicorn app.api.asgi:app`. Importing this module must NOT construct the engine eagerly in a way that requires a live DB or tvdatafeed at import time — build the engine lazily inside a factory function `build_app()` and expose `app = build_app()` guarded so import works without Docker/tvdatafeed (the source_factory is a lambda, deferred).

- [ ] **Step 1: Write the failing test** (`tests/api/test_data_sync.py`)

```python
from httpx import ASGITransport, AsyncClient
import pytest_asyncio
from app.api.app import create_app
from app.db.base import make_session_factory


@pytest_asyncio.fixture
async def client(engine):
    async with AsyncClient(transport=ASGITransport(app=create_app(make_session_factory(engine))),
                           base_url="http://test") as c:
        yield c


async def test_data_sync_with_empty_seed_dir(client: AsyncClient, tmp_path, monkeypatch):
    # point settings.seed_dir at an empty dir → zero inserted, still 200
    from app.core import config
    monkeypatch.setattr(type(config.settings), "seed_dir", property(lambda self: tmp_path))
    resp = await client.post("/data/sync")
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/api/test_data_sync.py -v`
Expected: FAIL — `inserted` not 0 / seed_dir override path issue, or asgi module missing when imported elsewhere. (If the monkeypatch approach is brittle, instead create a `client` whose app uses a settings override; keep the assertion that an empty seed dir yields `inserted == 0` and HTTP 200.)

- [ ] **Step 3: Write `app/api/asgi.py`**

```python
from fastapi import FastAPI
from app.api.app import create_app
from app.core.config import settings
from app.data.cache.parquet_cache import CachedDataSource, ParquetCache
from app.data.sources.tvdatafeed_source import TvDatafeedSource
from app.db.base import make_engine, make_session_factory


def build_app() -> FastAPI:
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    def source_factory() -> CachedDataSource:
        return CachedDataSource(
            TvDatafeedSource(settings.tv_username, settings.tv_password),
            ParquetCache(settings.cache_dir),
        )

    return create_app(session_factory, source_factory=source_factory)


app = build_app()
```
This is import-safe: `make_engine` does not open a connection, and `source_factory` is deferred (tvdatafeed only imported when a backtest actually runs).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/api/test_data_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Run the FULL suite (gate)**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS — all Plan 1 tests (27) plus the new Plan 2 tests, zero warnings. Report the new total.

- [ ] **Step 6: Manual sanity (no DB/creds needed)**

Run: `.venv/Scripts/python -c "from app.api.asgi import app; print([r.path for r in app.routes])"`
Expected: prints the route paths including `/health`, `/backtests`, `/backtests/{run_id}`, `/backtests/{run_id}/equity`, `/backtests/{run_id}/holdings`, `/backtests/{run_id}/metrics`, `/universe/{index}`, `/data/sync` — confirming the app assembles without a live DB or tvdatafeed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/asgi.py backend/tests/api/test_data_sync.py
git commit -m "feat(api): production ASGI entrypoint wiring DB + cached data source"
```

---

## Self-Review

**Spec coverage (design doc §9, §10):**
- §9 persistence split (Postgres for runs/membership/results; Parquet keeps OHLC) → Tasks 1, 3; OHLC untouched ✓
- §10 API endpoints — `POST /backtests` (T5), `GET /backtests/{id}` (T5), `/equity` `/holdings` `/metrics` (T6), `GET /universe/{index}?as_of=` (T7), `POST /data/sync` (T7) ✓
- §10 async background execution → T5 runner via BackgroundTasks + `anyio.to_thread` ✓
- §5 migrate file-backed membership into `index_membership` behind unchanged `UniverseProvider` → T2 (`IndexMembershipProvider` reused; only loader changes) ✓
- §15 secrets via env (`DATABASE_URL`, TV creds) → T0 config, T8 asgi ✓
- §14 tests against real services (no SQLite masking) → testcontainers Postgres, T0 ✓
- Deferred to Plan 3 (out of scope): the glass dashboard frontend.

**Placeholder scan:** No "TBD/TODO". One deliberate note: the `__import__("sqlalchemy").JSON` shim in T1 Step 3 is explicitly flagged to be replaced with a clean `from sqlalchemy import JSON` import in final code — the implementer instruction is concrete, not a placeholder. The Alembic `0001_initial.py` is generated by `alembic revision --autogenerate` with a written hand-fallback if autogenerate can't connect — concrete either way.

**Type consistency:** `get_session` yields `AsyncSession` consumed identically across all routers. `BacktestRunRepository` method names (`create`, `mark_running`, `save_results`, `mark_failed`, `get`, `get_equity`, `get_positions`, `get_metrics`) match every call site (T5, T6). `create_app(session_factory, *, source_factory=None)` signature matches all three test fixtures and `asgi.build_app`. `RunRequest` fields mirror `BacktestConfig` exactly so `BacktestConfig(**body.model_dump())` (T5) is valid. `execute_backtest(session_factory, source_factory, run_id, cfg)` argument order matches the `background.add_task(...)` call (T5). Schemas (`EquityPointOut.date/equity/benchmark`, `PositionOut.rebalance_date/symbol/weight`) match the repository row attributes (`EquityPoint.on_date`→mapped in T6 handler; `RunPosition.rebalance_date/symbol/weight`).

**Cross-task note resolved:** T6 maps `EquityPoint.on_date` → `EquityPointOut.date` explicitly in the handler (attribute names intentionally differ: DB uses `on_date`, API uses `date`).

---

## Execution prerequisites (pre-flight before Task 0)

- **Docker daemon must be running** (testcontainers). Confirm with `docker info` before starting Task 0; if absent, this plan is BLOCKED until Docker is available (or the DB-test decision is revisited).
- No tvdatafeed credentials or verified seed are needed to build/test Plan 2 — the API tests inject a synthetic `DataSource` and seed synthetic membership. The live-data gate from Plan 1 still applies only to *real* backtests.

## Next plan

- **Plan 3 — Glass dashboard:** Next.js + React + Tailwind frosted-glass UI consuming this API — run-config panel, equity-vs-NIFTY chart, metric cards, holdings timeline.
