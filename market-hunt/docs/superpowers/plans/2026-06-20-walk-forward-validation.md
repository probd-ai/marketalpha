# Walk-Forward Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a rolling walk-forward validator that tunes the RS-Momentum sim's `(max_holdings, momentum_ranking)` on past windows and measures the chosen config on the next, unseen window — answering whether the in-sample ~28% CAGR edge is robust out-of-sample.

**Architecture:** A small `walkforward/` package on top of market-hunt's existing simulator. Pure, unit-tested logic (fold generation, config selection, equity stitching, curve metrics, verdict) is isolated from the one live adapter that drives `run_strategy_simulation` in-process. A CLI wires the locked defaults and runs the 6-fold validation.

**Tech Stack:** Python 3.13, numpy, pandas, pymongo, matplotlib, pytest — all already in `market-hunt/.venv`. The simulator is `api_server.run_strategy_simulation` (async) + `IndicatorDataManager` + the `membership` module.

## Global Constraints

- **Run everything with the project venv:** `market-hunt/.venv/Scripts/python.exe`. Run pytest as `.venv/Scripts/python.exe -m pytest` from the `market-hunt/` directory.
- **Engine driven in-process**, never over HTTP: `from api_server import run_strategy_simulation, SimulationParams`.
- **Tuned grid (exactly):** `max_holdings ∈ {10, 15, 20, 25, 30}` × `momentum_ranking ∈ {"20_day_return", "price_roc_66d", "price_roc_222d"}` = 15 configs.
- **Fixed sim params (every run):** `portfolio_base_value=100000`, `rebalance_frequency="monthly"`, `rebalance_date="first"`, `rebalance_type="equal_weight"`, `universe="NIFTY500"`, `benchmark_symbol="Nifty 50"`, `point_in_time_membership=True`, `include_brokerage=True`, `strategy_id="strategy_1781939795919"`.
- **Scheme:** rolling, 3-year train / 1-year test / 1-year step, data 2018-01-01 .. 2026-06-30 → 6 folds.
- **Selection objective:** Sharpe ratio (CAGR reported alongside).
- **Mongo:** `mongodb://localhost:27017/`, database `market_hunt`. Mongo must be running.
- **Tests live in** `market-hunt/tests/` (matches the existing `tests/test_membership.py` convention; this is the proven pytest location and overrides the spec's tentative `walkforward/tests/` path).
- **No look-ahead, no survivorship:** PIT membership only; never fall back to the current snapshot.
- Commits authored "Arpitha B", no Co-Authored-By. Do not push.

---

### Task 1: Fold generation with leakage guard

**Files:**
- Create: `walkforward/__init__.py` (empty)
- Create: `walkforward/folds.py`
- Test: `tests/test_walkforward_folds.py`

**Interfaces:**
- Produces: `Fold` dataclass with `date` fields `train_start, train_end, test_start, test_end`; `make_folds(data_start: date, data_end: date, train_years: int = 3, test_years: int = 1, step_years: int = 1) -> list[Fold]`. Raises `ValueError` if any fold has `test_start <= train_end`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_walkforward_folds.py
from datetime import date
import pytest
from walkforward.folds import make_folds, Fold


def test_locked_scheme_produces_six_folds_with_exact_dates():
    folds = make_folds(date(2018, 1, 1), date(2026, 6, 30))
    assert len(folds) == 6
    assert folds[0] == Fold(date(2018, 1, 1), date(2020, 12, 31), date(2021, 1, 1), date(2021, 12, 31))
    assert folds[5] == Fold(date(2023, 1, 1), date(2025, 12, 31), date(2026, 1, 1), date(2026, 6, 30))


def test_every_test_window_starts_after_its_train_window():
    for f in make_folds(date(2018, 1, 1), date(2026, 6, 30)):
        assert f.test_start > f.train_end


def test_step_advances_by_one_year():
    folds = make_folds(date(2018, 1, 1), date(2026, 6, 30))
    assert folds[1].train_start == date(2019, 1, 1)
    assert folds[1].test_start == date(2022, 1, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_walkforward_folds.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'walkforward'`

- [ ] **Step 3: Write minimal implementation**

```python
# walkforward/folds.py
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Fold:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def make_folds(data_start: date, data_end: date,
               train_years: int = 3, test_years: int = 1, step_years: int = 1) -> list[Fold]:
    """Rolling walk-forward folds. Train spans `train_years`, the test window is the
    next `test_years`, and the whole window slides forward by `step_years`. The last
    test window is clipped to `data_end`."""
    folds: list[Fold] = []
    train_start = data_start
    while True:
        train_end = date(train_start.year + train_years, 1, 1) - _one_day()
        test_start = date(train_start.year + train_years, 1, 1)
        if test_start > data_end:
            break
        test_end = min(date(train_start.year + train_years + test_years, 1, 1) - _one_day(), data_end)
        if test_start <= train_end:
            raise ValueError(f"leakage: test_start {test_start} <= train_end {train_end}")
        folds.append(Fold(train_start, train_end, test_start, test_end))
        train_start = date(train_start.year + step_years, 1, 1)
    return folds


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_walkforward_folds.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add walkforward/__init__.py walkforward/folds.py tests/test_walkforward_folds.py
git commit -m "feat(walkforward): rolling fold generation with leakage guard"
```

---

### Task 2: Sim adapter — RunResult extraction + in-process SimRunner

**Files:**
- Create: `walkforward/sim_runner.py`
- Test: `tests/test_walkforward_sim_runner.py`

**Interfaces:**
- Produces:
  - `RunResult` dataclass: `cagr: float, sharpe: float, max_drawdown: float, alpha: float, total_return: float, equity: list[tuple[str, float]], n_points: int`.
  - `build_run_result(sim_result: dict) -> RunResult` — pure; reads `sim_result["summary"]` keys `total_return, sharpe_ratio, max_drawdown, alpha` and the daily series `sim_result["results"][i]["date"]` / `["portfolio_value"]`. CAGR is computed from first/last `portfolio_value` and their dates.
  - `class SimRunner` with `async setup()`, `async run(start: date, end: date, max_holdings: int, momentum_ranking: str) -> RunResult | None` (None when the run has < 2 daily points), and `load_close_series(symbol: str, start: date, end: date) -> list[tuple[str, float]]`.
- Consumes: `walkforward.folds` (none directly here); the live path consumes `api_server.run_strategy_simulation`, `api_server.SimulationParams`, `indicator_data_manager.IndicatorDataManager`, `membership.load_membership`/`union_over`.

- [ ] **Step 1: Write the failing test** (pure extraction only — no Mongo needed)

```python
# tests/test_walkforward_sim_runner.py
from walkforward.sim_runner import build_run_result, RunResult


def _sample():
    # ~1 year, doubling: 100000 -> 200000
    return {
        "summary": {"total_return": 100.0, "sharpe_ratio": 1.5,
                    "max_drawdown": -20.0, "alpha": 60.0},
        "results": [
            {"date": "2021-01-01", "portfolio_value": 100000.0},
            {"date": "2021-07-01", "portfolio_value": 150000.0},
            {"date": "2022-01-01", "portfolio_value": 200000.0},
        ],
    }


def test_build_run_result_maps_summary_and_equity():
    r = build_run_result(_sample())
    assert isinstance(r, RunResult)
    assert r.sharpe == 1.5
    assert r.alpha == 60.0
    assert r.total_return == 100.0
    assert r.n_points == 3
    assert r.equity[0] == ("2021-01-01", 100000.0)
    assert r.equity[-1] == ("2022-01-01", 200000.0)


def test_build_run_result_cagr_is_annualized_from_dates():
    r = build_run_result(_sample())
    # doubled over ~1 year -> CAGR ~ 100%
    assert 0.95 < r.cagr < 1.05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_walkforward_sim_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'walkforward.sim_runner'`

- [ ] **Step 3: Write minimal implementation**

```python
# walkforward/sim_runner.py
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime

# market-hunt repo root on path so `api_server`, `membership` import cleanly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GRID_WINDOWS = ("20_day_return", "price_roc_66d", "price_roc_222d")
GRID_SIZES = (10, 15, 20, 25, 30)
STRATEGY_ID = "strategy_1781939795919"


@dataclass
class RunResult:
    cagr: float
    sharpe: float
    max_drawdown: float
    alpha: float
    total_return: float
    equity: list[tuple[str, float]]
    n_points: int


def build_run_result(sim_result: dict) -> RunResult:
    s = sim_result["summary"]
    rows = sim_result["results"]
    equity = [(r["date"], float(r["portfolio_value"])) for r in rows]
    if len(equity) >= 2:
        d0 = datetime.strptime(equity[0][0], "%Y-%m-%d")
        d1 = datetime.strptime(equity[-1][0], "%Y-%m-%d")
        years = max((d1 - d0).days / 365.25, 1e-9)
        cagr = (equity[-1][1] / equity[0][1]) ** (1 / years) - 1
    else:
        cagr = float("nan")
    return RunResult(
        cagr=cagr,
        sharpe=float(s["sharpe_ratio"]),
        max_drawdown=float(s["max_drawdown"]),
        alpha=float(s["alpha"]),
        total_return=float(s["total_return"]),
        equity=equity,
        n_points=len(equity),
    )


class SimRunner:
    """Drives market-hunt's run_strategy_simulation in-process. Loads membership,
    the strategy doc, and the IndicatorDataManager ONCE; reuses them across runs."""

    def __init__(self, universe: str = "NIFTY500", mongo_uri: str = "mongodb://localhost:27017/"):
        self.universe = universe
        self.mongo_uri = mongo_uri
        self._dm = None
        self._dm_ctx = None
        self._strategy = None
        self._membership = None

    async def setup(self):
        from pymongo import MongoClient
        from membership import load_membership
        from indicator_data_manager import IndicatorDataManager

        db = MongoClient(self.mongo_uri)["market_hunt"]
        self._strategy = db["simulation_strategies"].find_one({"id": STRATEGY_ID})
        if not self._strategy:
            raise ValueError(f"strategy {STRATEGY_ID} not found")
        self._membership = load_membership(db, self.universe, datetime.now().date())
        self._db = db
        self._dm_ctx = IndicatorDataManager()
        self._dm = await self._dm_ctx.__aenter__()

    async def aclose(self):
        if self._dm_ctx is not None:
            await self._dm_ctx.__aexit__(None, None, None)

    async def run(self, start: date, end: date, max_holdings: int,
                  momentum_ranking: str) -> "RunResult | None":
        from api_server import run_strategy_simulation, SimulationParams
        from membership import union_over

        universe_symbols = sorted(union_over(self._membership, start, end))
        params = SimulationParams(
            strategy_id=STRATEGY_ID,
            portfolio_base_value=100000,
            rebalance_frequency="monthly",
            rebalance_date="first",
            rebalance_type="equal_weight",
            universe=self.universe,
            benchmark_symbol="Nifty 50",
            max_holdings=max_holdings,
            momentum_ranking=momentum_ranking,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            point_in_time_membership=True,
            include_brokerage=True,
        )
        sim = await run_strategy_simulation(
            self._dm, self._strategy, universe_symbols, params, membership=self._membership
        )
        if not sim or len(sim.get("results", [])) < 2:
            return None
        return build_run_result(sim)

    def load_close_series(self, symbol: str, start: date, end: date) -> list[tuple[str, float]]:
        out = []
        for coll in ("prices_2015_2019", "prices_2020_2024", "prices_2025_2029"):
            for r in self._db[coll].find(
                {"symbol": symbol,
                 "date": {"$gte": datetime(start.year, start.month, start.day),
                          "$lte": datetime(end.year, end.month, end.day)}},
                {"_id": 0, "date": 1, "close_price": 1},
            ):
                if r.get("close_price") is not None:
                    out.append((r["date"].strftime("%Y-%m-%d"), float(r["close_price"])))
        out.sort(key=lambda t: t[0])
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_walkforward_sim_runner.py -v`
Expected: PASS (2 passed). (`build_run_result` is pure; `SimRunner`'s live path is exercised in Task 5.)

- [ ] **Step 5: Commit**

```bash
git add walkforward/sim_runner.py tests/test_walkforward_sim_runner.py
git commit -m "feat(walkforward): in-process sim adapter + RunResult extraction"
```

---

### Task 3: Selection, stitching, curve metrics, orchestration

**Files:**
- Create: `walkforward/walk_forward.py`
- Test: `tests/test_walkforward_core.py`

**Interfaces:**
- Consumes: `walkforward.folds.Fold`, `walkforward.sim_runner.RunResult`.
- Produces:
  - `select_best(results: dict[tuple[int, str], RunResult], objective: str = "sharpe") -> tuple[int, str]` — key with max `getattr(result, objective)`, skipping `None`.
  - `curve_metrics(equity: list[tuple[str, float]]) -> dict` → `{"cagr", "sharpe", "max_drawdown", "total_return"}` (Sharpe annualized ×√252 from daily returns; CAGR from calendar dates).
  - `stitch(segments: list[list[tuple[str, float]]], base: float = 100000.0) -> list[tuple[str, float]]` — chains test-window equity segments by compounding each segment's return path onto the running level.
  - `FoldResult` dataclass: `fold: Fold, best_config: tuple[int, str], train: RunResult, oos: RunResult, baseline_oos: RunResult`.
  - `async run_walk_forward(runner, folds, sizes, windows, baseline, objective="sharpe") -> list[FoldResult]` — `runner` is any object with an async `run(start, end, n, window)`; `baseline=(n, window)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_walkforward_core.py
import asyncio
from datetime import date
from walkforward.folds import Fold
from walkforward.sim_runner import RunResult
from walkforward.walk_forward import select_best, curve_metrics, stitch, run_walk_forward


def _rr(sharpe, equity=None, **kw):
    return RunResult(cagr=kw.get("cagr", 0.0), sharpe=sharpe,
                     max_drawdown=kw.get("mdd", -10.0), alpha=kw.get("alpha", 0.0),
                     total_return=kw.get("tr", 0.0), equity=equity or [], n_points=len(equity or []))


def test_select_best_picks_max_objective_and_skips_none():
    results = {(10, "20_day_return"): _rr(0.8), (20, "price_roc_66d"): _rr(1.3),
               (30, "price_roc_222d"): None}
    assert select_best(results, "sharpe") == (20, "price_roc_66d")


def test_curve_metrics_on_known_doubling_curve():
    eq = [("2021-01-01", 100000.0), ("2022-01-01", 200000.0)]
    m = curve_metrics(eq)
    assert abs(m["total_return"] - 100.0) < 1e-6
    assert 0.95 < m["cagr"] < 1.05


def test_stitch_compounds_segments():
    seg1 = [("2021-01-01", 100000.0), ("2021-12-31", 120000.0)]  # +20%
    seg2 = [("2022-01-01", 100000.0), ("2022-12-31", 110000.0)]  # +10%
    out = stitch([seg1, seg2])
    assert out[0] == ("2021-01-01", 100000.0)
    # final level = 100000 * 1.2 * 1.1 = 132000
    assert abs(out[-1][1] - 132000.0) < 1e-6


def test_run_walk_forward_selects_on_train_then_runs_oos():
    fold = Fold(date(2018, 1, 1), date(2020, 12, 31), date(2021, 1, 1), date(2021, 12, 31))

    class StubRunner:
        def __init__(self):
            self.calls = []
        async def run(self, start, end, n, window):
            self.calls.append((start, end, n, window))
            # train: favor n=20; oos/baseline: return a tagged sharpe
            if end == date(2020, 12, 31):
                return _rr(1.0 if n == 20 else 0.5)
            return _rr(2.0, equity=[("2021-01-01", 100000.0), ("2021-12-31", 130000.0)])

    runner = StubRunner()
    res = asyncio.run(run_walk_forward(runner, [fold], sizes=(10, 20), windows=("price_roc_66d",),
                                       baseline=(20, "price_roc_66d"), objective="sharpe"))
    assert len(res) == 1
    assert res[0].best_config == (20, "price_roc_66d")
    assert res[0].oos.sharpe == 2.0
    assert res[0].baseline_oos.sharpe == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_walkforward_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'walkforward.walk_forward'`

- [ ] **Step 3: Write minimal implementation**

```python
# walkforward/walk_forward.py
from dataclasses import dataclass
from datetime import datetime
import math
from walkforward.folds import Fold
from walkforward.sim_runner import RunResult


@dataclass
class FoldResult:
    fold: Fold
    best_config: tuple[int, str]
    train: RunResult
    oos: RunResult
    baseline_oos: RunResult


def select_best(results: dict[tuple[int, str], RunResult], objective: str = "sharpe") -> tuple[int, str]:
    valid = {k: v for k, v in results.items() if v is not None}
    if not valid:
        raise ValueError("no valid configs to select from")
    return max(valid, key=lambda k: getattr(valid[k], objective))


def curve_metrics(equity: list[tuple[str, float]]) -> dict:
    vals = [v for _, v in equity]
    dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in equity]
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    if rets:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        std = math.sqrt(var)
        sharpe = (mean / std * math.sqrt(252)) if std > 0 else float("nan")
    else:
        sharpe = float("nan")
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9) if len(dates) >= 2 else 1e-9
    cagr = (vals[-1] / vals[0]) ** (1 / years) - 1 if len(vals) >= 2 else float("nan")
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"cagr": cagr, "sharpe": sharpe, "max_drawdown": mdd * 100,
            "total_return": (vals[-1] / vals[0] - 1) * 100 if len(vals) >= 2 else float("nan")}


def stitch(segments: list[list[tuple[str, float]]], base: float = 100000.0) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    level = base
    for seg in segments:
        if not seg:
            continue
        seg_start = seg[0][1]
        for d, v in seg:
            out.append((d, level * v / seg_start))
        level = level * seg[-1][1] / seg_start
    return out


async def run_walk_forward(runner, folds, sizes, windows, baseline, objective="sharpe") -> list[FoldResult]:
    results: list[FoldResult] = []
    for fold in folds:
        grid: dict[tuple[int, str], RunResult] = {}
        for n in sizes:
            for w in windows:
                grid[(n, w)] = await runner.run(fold.train_start, fold.train_end, n, w)
        best = select_best(grid, objective)
        train_best = grid[best]
        oos = await runner.run(fold.test_start, fold.test_end, best[0], best[1])
        base_oos = await runner.run(fold.test_start, fold.test_end, baseline[0], baseline[1])
        results.append(FoldResult(fold, best, train_best, oos, base_oos))
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_walkforward_core.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add walkforward/walk_forward.py tests/test_walkforward_core.py
git commit -m "feat(walkforward): selection, stitching, curve metrics, orchestration"
```

---

### Task 4: Report — verdict + table + benchmark curve + plot

**Files:**
- Create: `walkforward/report.py`
- Test: `tests/test_walkforward_report.py`

**Interfaces:**
- Consumes: `walkforward.walk_forward.FoldResult`, `curve_metrics`, `stitch`.
- Produces:
  - `verdict(adaptive: dict, baseline: dict, benchmark: dict) -> str` where each arg is a `curve_metrics`-style dict. Returns one of `"ROBUST_OPTIMIZED"` (adaptive Sharpe ≥ baseline Sharpe AND baseline CAGR > benchmark CAGR), `"ROBUST_FIXED"` (adaptive < baseline but baseline CAGR > benchmark CAGR → fix N, don't optimize), or `"NO_EDGE"` (baseline CAGR ≤ benchmark CAGR).
  - `format_fold_table(fold_results: list[FoldResult]) -> str` — per-fold lines: fold index, best `(N, window)`, train Sharpe, OOS Sharpe, OOS CAGR, OOS alpha.
  - `stability_flag(fold_results) -> str` — `"STABLE"` if ≤2 distinct best-configs across folds, else `"THRASHING"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_walkforward_report.py
from datetime import date
from walkforward.folds import Fold
from walkforward.sim_runner import RunResult
from walkforward.walk_forward import FoldResult
from walkforward.report import verdict, stability_flag


def test_verdict_robust_optimized():
    assert verdict({"sharpe": 1.2, "cagr": 0.25}, {"sharpe": 1.0, "cagr": 0.22},
                   {"sharpe": 0.6, "cagr": 0.12}) == "ROBUST_OPTIMIZED"


def test_verdict_robust_fixed_when_adaptive_loses_to_baseline():
    assert verdict({"sharpe": 0.9, "cagr": 0.20}, {"sharpe": 1.1, "cagr": 0.23},
                   {"sharpe": 0.6, "cagr": 0.12}) == "ROBUST_FIXED"


def test_verdict_no_edge_when_baseline_trails_benchmark():
    assert verdict({"sharpe": 1.2, "cagr": 0.10}, {"sharpe": 1.0, "cagr": 0.11},
                   {"sharpe": 0.9, "cagr": 0.13}) == "NO_EDGE"


def _fr(idx, cfg):
    f = Fold(date(2018 + idx, 1, 1), date(2020 + idx, 12, 31),
             date(2021 + idx, 1, 1), date(2021 + idx, 12, 31))
    rr = RunResult(0.2, 1.0, -10.0, 5.0, 20.0, [], 0)
    return FoldResult(f, cfg, rr, rr, rr)


def test_stability_flag():
    same = [_fr(i, (20, "price_roc_66d")) for i in range(4)]
    assert stability_flag(same) == "STABLE"
    mixed = [_fr(i, (n, "price_roc_66d")) for i, n in enumerate([10, 20, 30, 25])]
    assert stability_flag(mixed) == "THRASHING"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_walkforward_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'walkforward.report'`

- [ ] **Step 3: Write minimal implementation**

```python
# walkforward/report.py
from walkforward.walk_forward import curve_metrics, stitch


def verdict(adaptive: dict, baseline: dict, benchmark: dict) -> str:
    if baseline["cagr"] <= benchmark["cagr"]:
        return "NO_EDGE"
    if adaptive["sharpe"] >= baseline["sharpe"]:
        return "ROBUST_OPTIMIZED"
    return "ROBUST_FIXED"


def stability_flag(fold_results) -> str:
    distinct = {fr.best_config for fr in fold_results}
    return "STABLE" if len(distinct) <= 2 else "THRASHING"


def format_fold_table(fold_results) -> str:
    lines = [f"{'fold':>4} {'best (N, window)':>26} {'trainSharpe':>11} "
             f"{'oosSharpe':>9} {'oosCAGR%':>9} {'oosAlpha':>9}"]
    for i, fr in enumerate(fold_results, 1):
        cfg = f"{fr.best_config[0]}, {fr.best_config[1]}"
        oos_cagr = fr.oos.cagr * 100 if fr.oos else float("nan")
        lines.append(f"{i:>4} {cfg:>26} {fr.train.sharpe:>11.2f} "
                     f"{(fr.oos.sharpe if fr.oos else float('nan')):>9.2f} "
                     f"{oos_cagr:>9.1f} {(fr.oos.alpha if fr.oos else float('nan')):>9.1f}")
    return "\n".join(lines)


def build_curves(fold_results, runner):
    """Stitch adaptive + baseline OOS equity, and load the Nifty 50 benchmark over
    the same stitched span. Returns (adaptive_metrics, baseline_metrics, benchmark_metrics,
    adaptive_curve, baseline_curve, benchmark_curve)."""
    adaptive_curve = stitch([fr.oos.equity for fr in fold_results if fr.oos])
    baseline_curve = stitch([fr.baseline_oos.equity for fr in fold_results if fr.baseline_oos])
    first = fold_results[0].fold.test_start
    last = fold_results[-1].fold.test_end
    bench_raw = runner.load_close_series("Nifty 50", first, last)
    bench_curve = stitch([bench_raw]) if bench_raw else []
    return (curve_metrics(adaptive_curve), curve_metrics(baseline_curve),
            curve_metrics(bench_curve) if bench_curve else {"cagr": float("nan"), "sharpe": float("nan")},
            adaptive_curve, baseline_curve, bench_curve)


def save_plot(adaptive_curve, baseline_curve, bench_curve, path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 6), dpi=130)
    for curve, label in [(adaptive_curve, "adaptive (walk-forward)"),
                         (baseline_curve, "fixed N=20, 3m"),
                         (bench_curve, "Nifty 50")]:
        if curve:
            ax.plot([d for d, _ in curve], [v for _, v in curve], label=label)
    ax.set_title("Out-of-sample equity — adaptive vs fixed vs benchmark")
    ax.legend(); ax.set_xticks(ax.get_xticks()[::max(1, len(ax.get_xticks()) // 8)])
    fig.autofmt_xdate(); fig.savefig(path, bbox_inches="tight")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_walkforward_report.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add walkforward/report.py tests/test_walkforward_report.py
git commit -m "feat(walkforward): verdict logic, fold table, stitched curves, plot"
```

---

### Task 5: CLI entrypoint + run the validation (the answer)

**Files:**
- Create: `walkforward/run_walkforward.py`
- Test: `tests/test_walkforward_smoke.py` (single-config, single-fold smoke against live Mongo)

**Interfaces:**
- Consumes: everything above; `walkforward.sim_runner.SimRunner`, `make_folds`, `run_walk_forward`, `report.*`.
- Produces: `async main(argv=None)` — wires the locked defaults, runs the folds, prints the fold table + verdict + stability flag, saves the plot to the scratchpad path passed via `--plot`.

- [ ] **Step 1: Write the failing test** (a real-data smoke test — Mongo must be up; one fold, one config, kept tiny)

```python
# tests/test_walkforward_smoke.py
import asyncio
from datetime import date
import pytest
from walkforward.sim_runner import SimRunner


@pytest.mark.integration
def test_single_run_against_live_sim_produces_valid_result():
    async def go():
        runner = SimRunner()
        await runner.setup()
        try:
            r = await runner.run(date(2020, 1, 1), date(2020, 12, 31), 20, "price_roc_66d")
        finally:
            await runner.aclose()
        return r
    r = asyncio.run(go())
    assert r is not None
    assert r.n_points > 50            # a year of trading days
    assert -100 < r.max_drawdown <= 0
    # warmup correctness: first rebalance has real signals -> portfolio moved off base
    assert r.equity[0][1] == pytest.approx(100000.0, rel=0.05)
    assert r.total_return != 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_walkforward_smoke.py -v`
Expected: FAIL — `SimRunner` exists but the run errors only if Mongo/data is missing; if the module wiring is incomplete it fails at import/attribute. (If it already passes because Task 2 is complete, that is acceptable evidence the adapter works; proceed.)

- [ ] **Step 3: Write minimal implementation**

```python
# walkforward/run_walkforward.py
import argparse
import asyncio
from datetime import date, datetime
from walkforward.folds import make_folds
from walkforward.sim_runner import SimRunner, GRID_SIZES, GRID_WINDOWS
from walkforward.walk_forward import run_walk_forward
from walkforward import report


async def main(argv=None):
    p = argparse.ArgumentParser(description="Walk-forward validation of RS-Momentum")
    p.add_argument("--data-start", default="2018-01-01")
    p.add_argument("--data-end", default="2026-06-30")
    p.add_argument("--objective", default="sharpe", choices=["sharpe", "cagr"])
    p.add_argument("--baseline-n", type=int, default=20)
    p.add_argument("--baseline-window", default="price_roc_66d")
    p.add_argument("--plot", default=None)
    args = p.parse_args(argv)

    ds = datetime.strptime(args.data_start, "%Y-%m-%d").date()
    de = datetime.strptime(args.data_end, "%Y-%m-%d").date()
    folds = make_folds(ds, de)
    print(f"folds: {len(folds)}  grid: {len(GRID_SIZES)}x{len(GRID_WINDOWS)} "
          f"= {len(GRID_SIZES) * len(GRID_WINDOWS)} configs/fold")

    runner = SimRunner()
    await runner.setup()
    try:
        results = await run_walk_forward(
            runner, folds, GRID_SIZES, GRID_WINDOWS,
            baseline=(args.baseline_n, args.baseline_window), objective=args.objective)
        print("\n" + report.format_fold_table(results))
        adapt, base, bench, ac, bc, nc = report.build_curves(results, runner)
        print(f"\nstitched OOS  adaptive: CAGR {adapt['cagr']*100:5.1f}%  Sharpe {adapt['sharpe']:.2f}")
        print(f"stitched OOS  fixed   : CAGR {base['cagr']*100:5.1f}%  Sharpe {base['sharpe']:.2f}")
        print(f"stitched OOS  Nifty50 : CAGR {bench['cagr']*100:5.1f}%  Sharpe {bench['sharpe']:.2f}")
        print(f"\nstability: {report.stability_flag(results)}")
        print(f"VERDICT:   {report.verdict(adapt, base, bench)}")
        if args.plot:
            report.save_plot(ac, bc, nc, args.plot)
            print(f"plot -> {args.plot}")
    finally:
        await runner.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run the smoke test + the full validation**

Run: `.venv/Scripts/python.exe -m pytest tests/test_walkforward_smoke.py -v`
Expected: PASS (1 passed) — confirms the live adapter works end-to-end.

Then run the real thing (this is the deliverable answer; ~96 sim runs, allow time):
Run: `.venv/Scripts/python.exe -m walkforward.run_walkforward --plot "$TEMP/wf_oos.png"`
Expected: a 6-row fold table, three stitched-OOS lines, a stability flag, and a `VERDICT:` line.

- [ ] **Step 5: Commit**

```bash
git add walkforward/run_walkforward.py tests/test_walkforward_smoke.py
git commit -m "feat(walkforward): CLI entrypoint + live smoke test"
```

---

## Self-Review

**Spec coverage:** rolling scheme (Task 1) · in-process engine + reuse setup (Task 2) · 5×3 grid + fixed params + Sharpe selection (Tasks 2–3, Global Constraints) · adaptive-vs-fixed-vs-Nifty verdict (Task 4) · stability + degradation reporting (Task 4 table + `stability_flag`) · leakage guard (Task 1) · warmup assertion (Task 5 smoke) · CLI + the real run (Task 5). All spec sections map to a task.

**Placeholder scan:** no TBD/TODO/"handle errors"/vague steps; every code step is complete and runnable.

**Type consistency:** `RunResult` fields (`cagr, sharpe, max_drawdown, alpha, total_return, equity, n_points`) are produced in Task 2 and consumed unchanged in Tasks 3–4. `select_best`/`run_walk_forward`/`FoldResult` signatures match across Tasks 3–4–5. `curve_metrics` dict keys (`cagr, sharpe, max_drawdown, total_return`) match `verdict`'s reads (`sharpe, cagr`). Grid constants `GRID_SIZES`/`GRID_WINDOWS` defined once in `sim_runner` and imported by the CLI.

**Real-data assumptions — VERIFIED against live Mongo (2026-06-20):**
- `"Nifty 50"` price series exists with that exact casing (1,243 docs in `prices_2020_2024`) → `load_close_series("Nifty 50", ...)` will return a non-empty benchmark curve; no spurious `NO_EDGE`.
- Earliest stored TrueVX date is `2018-01-01` → the `data_start=2018-01-01` train floor is valid.
- `prices_2015_2019` exists (e.g. RELIANCE from 2015-01-01) → the 222-day warmup for the 2018 fold's first rebalance has real prior history; the Task 5 smoke test's warmup assertion will hold.

No outstanding real-data risks for the implementer to chase.
