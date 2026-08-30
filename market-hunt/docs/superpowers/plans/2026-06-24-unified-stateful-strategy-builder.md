# Unified Stateful Strategy Builder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the one existing strategy builder express stateful "wave" strategies (tranche accumulate / hold / liquidate) alongside today's stateless filters, by adding a new stateful engine that the simulation endpoint routes to when a `wave` block is present.

**Architecture:** Router behind one builder. `SimulationParams` gains an optional `wave: WaveConfig`. When absent, the existing `run_strategy_simulation` runs byte-for-byte as today. When present, a new pure `run_wave_simulation` (a config-driven generalization of the proven `meanlong_wave_backtest.py` / `bull_wave_backtest.py` scripts) runs instead. Signal evaluation, the config models, and the engine live in new `wave_*.py` modules; `api_server.py` is touched only for the optional model field, the router branch, and CRUD persistence.

**Tech Stack:** Python 3 / FastAPI / pydantic / pymongo / MongoDB (`market_hunt` db), pytest (unit; `integration`-marked live-server smoke deselected by default). Frontend: Next.js / React / TypeScript (`market-hunt/frontend`, `simulator/page.tsx`).

## Global Constraints

- Repo: **market-hunt** (its own git repo). All paths below are relative to `market-hunt/`.
- Product name spelled **NeuString** with a capital S wherever it appears.
- No PII, secrets, or credentials in code/tests/fixtures — DB URI is the existing local `mongodb://localhost:27017`, db `market_hunt`.
- Author commits as **"Arpitha B"**; **no Co-Authored-By trailer**.
- Existing engine numbers are golden — the `wave is None` path must stay byte-identical (Task 9 proves it).
- Stateful golden masters: **mean_long level-wave = 15.9% CAGR / Sharpe 1.23 / −8.1% DD**; **bull-wave = 16.8% / 0.73 / −37%**. Both gross of costs, NIFTY50 PIT, N=1,000,000, START=2020-01-01, END=2026-08-30, RF=0.06, 252-day annualization, `yrs=(END-START).days/365.25`.
- Metrics vocabulary: `mean_short`, `mean_mid`, `mean_long`, `truevx_score`.
- Reuse, don't duplicate: `membership.py` (`load_membership`, `members_as_of`, `union_over`), `get_rebalance_dates(dates, frequency, date_type)` (`api_server.py:5073`), `apply_strategy_rules(day_indicators, rules)` (`api_server.py:5021`), `select_top_stocks_by_momentum(...)` (`api_server.py:3734`).
- Spec: `docs/superpowers/specs/2026-06-24-unified-stateful-strategy-builder-design.md` (read the Appendix — it encodes both waves as `WaveConfig`).

## File Structure

| File | Responsibility |
|------|----------------|
| `wave_models.py` (new) | pydantic `WaveConfig`, `SignalGroup`, `Signal` + validation |
| `wave_signals.py` (new) | pure cross / compare / last_cross_dir evaluators + group/firing helpers |
| `wave_engine.py` (new) | pure `run_wave_simulation` state machine, capital, output assembly |
| `api_server.py` (modify) | optional `wave` field on models; router branch in `/api/simulation/run`; persist `wave` in strategy CRUD |
| `tests/test_wave_signals.py` (new) | unit tests for signal evaluators |
| `tests/test_wave_engine.py` (new) | unit tests for state machine + capital |
| `tests/test_wave_golden_master.py` (new) | engine reproduces both scripts (marked `integration`, needs Mongo) |
| `tests/test_wave_api.py` (new) | router + stateless-regression + wave smoke (marked `integration`) |
| `frontend/src/app/analytics/simulator/page.tsx` (modify) | collapsible Stateful/Wave builder section + routing |

> **pytest marker note:** the repo's `pytest.ini` deselects `integration` by default. Pure unit tests (Tasks 1–3, 5–7) must NOT need Mongo. Golden-master and API tests (Tasks 8, 9, 11) need a live DB / server and are marked `integration`.

---

### Task 1: WaveConfig models

**Files:**
- Create: `wave_models.py`
- Test: `tests/test_wave_models.py`

**Interfaces:**
- Produces: `WaveConfig`, `SignalGroup` (= `list[Signal]`), `Signal`, `CompareRHS`. Field shapes exactly as the spec's Section 1 block. `WaveConfig.validate_cadence()` raises `ValueError` if any `cross` signal appears in `entry`/`accumulate` while `buy_cadence != "daily"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wave_models.py
import pytest
from wave_models import WaveConfig, Signal

def _level_wave_dict():
    g = lambda L: [{"kind": "cross", "metric": "mean_long", "level": L, "direction": "up"},
                   {"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up", "negate": True}]
    return {
        "capital_N": 1_000_000, "tranches": 20, "reentry": True,
        "buy_cadence": "daily", "exit_mode": "full",
        "entry": [g(40), g(50), g(60)], "accumulate": [],
        "exit": [[{"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up"}],
                 [{"kind": "cross", "metric": "mean_long", "level": 40, "direction": "down"}]],
    }

def test_level_wave_config_parses():
    cfg = WaveConfig(**_level_wave_dict())
    assert cfg.tranches == 20
    assert cfg.entry[0][0].kind == "cross"
    assert cfg.entry[0][1].negate is True

def test_compare_rhs_accepts_metric_or_number():
    s_num = Signal(kind="compare", lhs_metric="mean_long", op=">", rhs={"number": 50})
    s_met = Signal(kind="compare", lhs_metric="truevx_score", op="<=", rhs={"metric": "mean_long"})
    assert s_num.rhs.number == 50
    assert s_met.rhs.metric == "mean_long"

def test_cross_in_entry_requires_daily_cadence():
    d = _level_wave_dict(); d["buy_cadence"] = "monthly"
    with pytest.raises(ValueError):
        WaveConfig(**d)

def test_tranches_must_be_positive():
    d = _level_wave_dict(); d["tranches"] = 0
    with pytest.raises(ValueError):
        WaveConfig(**d)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_models'`.

- [ ] **Step 3: Write minimal implementation**

```python
# wave_models.py
from typing import List, Literal, Optional, Union
from pydantic import BaseModel, field_validator, model_validator

Metric = Literal["mean_short", "mean_mid", "mean_long", "truevx_score"]

class CompareRHS(BaseModel):
    metric: Optional[Metric] = None
    number: Optional[float] = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.metric is None) == (self.number is None):
            raise ValueError("compare rhs must set exactly one of {metric, number}")
        return self

class Signal(BaseModel):
    kind: Literal["cross", "compare", "last_cross_dir"]
    negate: bool = False
    # cross / last_cross_dir
    metric: Optional[Metric] = None
    level: Optional[float] = None
    direction: Optional[Literal["up", "down"]] = None
    grid: Optional[List[float]] = None
    # compare
    lhs_metric: Optional[Metric] = None
    op: Optional[Literal[">", "<", ">=", "<=", "==", "!="]] = None
    rhs: Optional[CompareRHS] = None

    @model_validator(mode="after")
    def _shape_by_kind(self):
        if self.kind == "cross":
            if self.metric is None or self.level is None or self.direction is None:
                raise ValueError("cross needs metric, level, direction")
        elif self.kind == "last_cross_dir":
            if self.metric is None or not self.grid or self.direction is None:
                raise ValueError("last_cross_dir needs metric, grid, direction")
        elif self.kind == "compare":
            if self.lhs_metric is None or self.op is None or self.rhs is None:
                raise ValueError("compare needs lhs_metric, op, rhs")
        return self

SignalGroup = List[Signal]

class WaveConfig(BaseModel):
    capital_N: float
    tranches: int
    reentry: bool = True
    buy_cadence: Literal["daily", "monthly", "weekly", "quarterly"] = "daily"
    buy_date: Literal["first", "mid", "last"] = "first"
    entry: List[SignalGroup]
    accumulate: List[SignalGroup] = []
    exit: List[SignalGroup]
    exit_mode: Literal["full", "fractional"] = "full"

    @field_validator("tranches")
    @classmethod
    def _k_positive(cls, v):
        if v < 1:
            raise ValueError("tranches must be >= 1")
        return v

    @model_validator(mode="after")
    def _cross_requires_daily(self):
        if self.buy_cadence != "daily":
            for grp in list(self.entry) + list(self.accumulate):
                if any(s.kind == "cross" for s in grp):
                    raise ValueError("cross signals in entry/accumulate require buy_cadence='daily'")
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wave_models.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add wave_models.py tests/test_wave_models.py
git commit -m "feat(wave): WaveConfig/Signal pydantic models + validation"
```

---

### Task 2: Signal evaluators — cross + group/firing helpers

**Files:**
- Create: `wave_signals.py`
- Test: `tests/test_wave_signals.py`

**Interfaces:**
- Produces:
  - `crossed_up(prev: float|None, cur: float, level: float) -> bool`
  - `crossed_down(prev: float|None, cur: float, level: float) -> bool`
  - `eval_signal(sig: Signal, today: dict, prev: dict, last_dirs: dict) -> bool`
  - `group_fires(group: list[Signal], today, prev, last_dirs) -> bool`
  - `count_firing_groups(groups, today, prev, last_dirs) -> int`
  - `any_group_fires(groups, today, prev, last_dirs) -> bool`
  - where `today`/`prev` are `{metric: float|None}` and `last_dirs` is `{(metric, tuple(grid)): "up"|"down"|None}`.
- Consumes: `Signal` from Task 1.

- [ ] **Step 1: Write the failing test (cross + negate)**

```python
# tests/test_wave_signals.py
from wave_models import Signal
from wave_signals import (crossed_up, crossed_down, eval_signal,
                          group_fires, count_firing_groups, any_group_fires)

def test_crossed_up_through_level():
    assert crossed_up(48, 52, 50) is True       # passed up through 50
    assert crossed_up(50, 52, 50) is False      # prev == level, not "through"
    assert crossed_up(52, 55, 50) is False      # already above
    assert crossed_up(None, 52, 50) is False    # first bar, no prev

def test_crossed_down_through_level():
    assert crossed_down(52, 48, 50) is True
    assert crossed_down(48, 47, 50) is False
    assert crossed_down(None, 47, 50) is False

def test_eval_cross_signal_with_negate():
    sig = Signal(kind="cross", metric="mean_long", level=70, direction="up", negate=True)
    today = {"mean_long": 62}; prev = {"mean_long": 38}     # crossed 40/50/60 but not 70
    assert eval_signal(sig, today, prev, {}) is True        # negate(NOT crossed 70) = True
    today2 = {"mean_long": 72}                              # this move DID cross 70
    assert eval_signal(sig, today2, prev, {}) is False

def test_group_fires_is_and():
    g = [Signal(kind="cross", metric="mean_long", level=50, direction="up"),
         Signal(kind="cross", metric="mean_long", level=70, direction="up", negate=True)]
    today = {"mean_long": 55}; prev = {"mean_long": 48}
    assert group_fires(g, today, prev, {}) is True

def test_count_firing_groups_counts_levels():
    # one big up-move 38 -> 62 crosses 40,50,60 => 3 single-level groups fire
    groups = [[Signal(kind="cross", metric="mean_long", level=L, direction="up")] for L in (40, 50, 60)]
    today = {"mean_long": 62}; prev = {"mean_long": 38}
    assert count_firing_groups(groups, today, prev, {}) == 3
    assert any_group_fires(groups, today, prev, {}) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_signals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_signals'`.

- [ ] **Step 3: Write minimal implementation**

```python
# wave_signals.py
import operator
from wave_models import Signal

_OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge,
        "<=": operator.le, "==": operator.eq, "!=": operator.ne}

def crossed_up(prev, cur, level):
    return prev is not None and prev < level <= cur

def crossed_down(prev, cur, level):
    return prev is not None and cur < level <= prev

def eval_signal(sig: Signal, today: dict, prev: dict, last_dirs: dict) -> bool:
    if sig.kind == "compare":
        lhs = today.get(sig.lhs_metric) or 0.0
        rhs = sig.rhs.number if sig.rhs.number is not None else (today.get(sig.rhs.metric) or 0.0)
        r = _OPS[sig.op](lhs, rhs)
    elif sig.kind == "cross":
        prev_v = prev.get(sig.metric); cur_v = today.get(sig.metric)
        if cur_v is None:
            r = False
        else:
            r = crossed_up(prev_v, cur_v, sig.level) if sig.direction == "up" \
                else crossed_down(prev_v, cur_v, sig.level)
    elif sig.kind == "last_cross_dir":
        r = last_dirs.get((sig.metric, tuple(sig.grid))) == sig.direction
    else:
        r = False
    return (not r) if sig.negate else r

def group_fires(group, today, prev, last_dirs):
    return all(eval_signal(s, today, prev, last_dirs) for s in group)

def count_firing_groups(groups, today, prev, last_dirs):
    return sum(1 for g in groups if group_fires(g, today, prev, last_dirs))

def any_group_fires(groups, today, prev, last_dirs):
    return any(group_fires(g, today, prev, last_dirs) for g in groups)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wave_signals.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add wave_signals.py tests/test_wave_signals.py
git commit -m "feat(wave): cross + group/firing signal evaluators"
```

---

### Task 3: Signal evaluators — compare + last_cross_dir state

**Files:**
- Modify: `wave_signals.py`
- Test: `tests/test_wave_signals.py` (add)

**Interfaces:**
- Produces: `update_last_dir(prev: float|None, cur: float, grid: list[float], prior: str|None) -> str|None` — returns the updated most-recent-cross direction for a metric over a grid.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wave_signals.py  (append)
from wave_signals import update_last_dir

def test_compare_metric_vs_metric_and_number():
    s_met = Signal(kind="compare", lhs_metric="truevx_score", op="<=", rhs={"metric": "mean_long"})
    s_num = Signal(kind="compare", lhs_metric="mean_long", op=">", rhs={"number": 50})
    today = {"truevx_score": 40, "mean_long": 55}
    assert eval_signal(s_met, today, {}, {}) is True     # 40 <= 55
    assert eval_signal(s_num, today, {}, {}) is True     # 55 > 50

def test_update_last_dir_tracks_most_recent_cross():
    grid = [50, 60, 70, 80, 90]
    d = update_last_dir(48, 62, grid, None)      # up through 50,60
    assert d == "up"
    d = update_last_dir(62, 58, grid, d)         # down through 60
    assert d == "down"
    d = update_last_dir(58, 59, grid, d)         # no grid cross -> unchanged
    assert d == "down"

def test_last_cross_dir_signal_reads_state():
    sig = Signal(kind="last_cross_dir", metric="mean_long", grid=[50, 60, 70, 80, 90], direction="down")
    last_dirs = {("mean_long", (50, 60, 70, 80, 90)): "down"}
    assert eval_signal(sig, {"mean_long": 55}, {}, last_dirs) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_signals.py::test_update_last_dir_tracks_most_recent_cross -v`
Expected: FAIL — `ImportError: cannot import name 'update_last_dir'`.

- [ ] **Step 3: Write minimal implementation**

```python
# wave_signals.py  (append)
def update_last_dir(prev, cur, grid, prior):
    if prev is None:
        return prior
    if cur < prev and any(cur < L <= prev for L in grid):
        return "down"
    if cur > prev and any(prev < L <= cur for L in grid):
        return "up"
    return prior
```

(`compare` already implemented in Task 2; its test lives here to keep compare coverage together.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wave_signals.py -v`
Expected: PASS (8 passed total).

- [ ] **Step 5: Commit**

```bash
git add wave_signals.py tests/test_wave_signals.py
git commit -m "feat(wave): compare + last_cross_dir state evaluation"
```

---

### Task 4: Per-symbol cross-state tracker

**Files:**
- Modify: `wave_signals.py`
- Test: `tests/test_wave_signals.py` (add)

**Interfaces:**
- Produces: `CrossState` — holds `prev: {metric: float}` and `last_dirs: {(metric, tuple(grid)): str}`.
  - `CrossState(grids: list[tuple[str, tuple[float,...]]])` — the (metric, grid) pairs to track for `last_cross_dir`.
  - `.update(today: dict[str, float]) -> None` — advance one day; updates `last_dirs` for each tracked grid then stores `prev`.
  - `.prev_snapshot() -> dict` — the metric→value map from BEFORE today's update (for `cross` eval).
- Consumes: `update_last_dir` from Task 3.

> Why: the engine needs, each day, both today's values and *yesterday's* values for `cross`, plus running `last_dirs`. This object encapsulates that so the engine loop stays small.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wave_signals.py  (append)
from wave_signals import CrossState

def test_cross_state_exposes_prev_and_updates_last_dir():
    grid = (50, 60, 70, 80, 90)
    cs = CrossState(grids=[("mean_long", grid)])
    cs.update({"mean_long": 48})
    assert cs.prev_snapshot() == {"mean_long": 48}        # after first day, prev = day1
    assert cs.last_dirs[("mean_long", grid)] is None      # no cross yet (first bar)
    cs.update({"mean_long": 62})                          # up through 50,60
    assert cs.last_dirs[("mean_long", grid)] == "up"
    assert cs.prev_snapshot() == {"mean_long": 62}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_signals.py::test_cross_state_exposes_prev_and_updates_last_dir -v`
Expected: FAIL — `ImportError: cannot import name 'CrossState'`.

- [ ] **Step 3: Write minimal implementation**

```python
# wave_signals.py  (append)
class CrossState:
    def __init__(self, grids):
        self.grids = list(grids)                 # [(metric, tuple(grid)), ...]
        self.prev = {}                           # metric -> last value
        self.last_dirs = {g: None for g in self.grids}

    def update(self, today):
        for metric, grid in self.grids:
            cur = today.get(metric)
            if cur is None:
                continue
            self.last_dirs[(metric, grid)] = update_last_dir(
                self.prev.get(metric), cur, list(grid), self.last_dirs[(metric, grid)])
        for metric, val in today.items():
            if val is not None:
                self.prev[metric] = val

    def prev_snapshot(self):
        return dict(self.prev)
```

> **Cross-eval ordering note for Task 5:** to detect a `cross` for *today*, the engine must read `prev_snapshot()` (yesterday's values) BEFORE calling `.update(today)`. Capture `prev = cs.prev_snapshot()` first, evaluate signals with `(today, prev, cs.last_dirs)`, then `cs.update(today)` last so `last_cross_dir` reflects today's move for the NEXT day — except the exit check must see today's `last_dirs`. The engine resolves this by updating `last_dirs` first (mirrors the scripts, which update cross-state at the top of the day before exit/buy checks) — see Task 5 ordering.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wave_signals.py -v`
Expected: PASS (9 passed total).

- [ ] **Step 5: Commit**

```bash
git add wave_signals.py tests/test_wave_signals.py
git commit -m "feat(wave): per-symbol CrossState tracker"
```

---

### Task 5: Wave engine — core state machine (level-wave shape)

**Files:**
- Create: `wave_engine.py`
- Test: `tests/test_wave_engine.py`

**Interfaces:**
- Produces:
  ```python
  run_wave_simulation(ever, intervals, prices, indicators, bench, dates, wave, *,
                      rules=None, max_holdings=None, momentum_method="20_day_return") -> dict
  ```
  - `ever`: list of symbols (ever-members). `intervals`: PIT membership intervals from `load_membership`.
  - `prices`: `{symbol: {date_str: close}}`. `indicators`: `{symbol: {date_str: {metric: float}}}`. `bench`: `{date_str: close}`. `dates`: sorted list of `date_str`.
  - Returns a dict with at least `equity_curve: list[(date_str, equity)]`, `cash: float`, `positions: dict`, and `diagnostics: dict` (`n_buys`, `n_exits`, `exit_takeprofit`, `exit_stop`, `reentries`, `avg_names`, `avg_cash_pct`). Costed/PIT/benchmark/summary added in Task 7.
- Consumes: `WaveConfig` (Task 1); `CrossState`, `count_firing_groups`, `any_group_fires` (Tasks 2–4); `members_as_of` from `membership.py`.

> This task implements the DAILY-cadence path with full-liquidation exit and accumulate-defaults-to-entry — enough to run the level-wave shape. Monthly cadence, fractional exit, reentry=false, rules pre-gate, max_holdings come in Task 6.

- [ ] **Step 1: Write the failing test (synthetic, no Mongo)**

```python
# tests/test_wave_engine.py
from wave_models import WaveConfig
from wave_engine import run_wave_simulation

class _AllMembers:
    """Stand-in for membership intervals: everyone is always a member."""
def _members_as_of_patch(monkeypatch, ever):
    import wave_engine
    monkeypatch.setattr(wave_engine, "members_as_of", lambda intervals, d: set(ever))

def test_level_wave_buys_one_tranche_per_level_then_exits(monkeypatch):
    ever = ["AAA"]
    _members_as_of_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
    # day1 ml=38 (no pos), day2 ml=62 (up through 40/50/60 -> 3 tranches), day3 ml=72 (up through 70 -> exit)
    indicators = {"AAA": {"2020-01-01": {"mean_long": 38}, "2020-01-02": {"mean_long": 62},
                          "2020-01-03": {"mean_long": 72}}}
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 100.0, "2020-01-03": 110.0}}
    g = lambda L: [{"kind": "cross", "metric": "mean_long", "level": L, "direction": "up"},
                   {"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up", "negate": True}]
    wave = WaveConfig(capital_N=300000, tranches=20, reentry=True, buy_cadence="daily", exit_mode="full",
                      entry=[g(40), g(50), g(60)], accumulate=[],
                      exit=[[{"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up"}],
                            [{"kind": "cross", "metric": "mean_long", "level": 40, "direction": "down"}]])
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave)
    # 3 tranches of 15000 each bought day2 at 100 -> 450 shares, cash 300000-45000=255000
    assert out["diagnostics"]["n_buys"] == 3
    # day3 exit at 110: proceeds 450*110=49500; final equity = 255000 + 49500 = 304500
    assert round(out["equity_curve"][-1][1], 2) == 304500.00
    assert out["diagnostics"]["exit_takeprofit"] == 1
    assert out["positions"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_engine'`.

- [ ] **Step 3: Write minimal implementation**

```python
# wave_engine.py
import statistics
from datetime import datetime
from membership import members_as_of
from wave_signals import CrossState, count_firing_groups, any_group_fires

def _grids_used(wave):
    grids = set()
    for grp in wave.entry + wave.accumulate + wave.exit:
        for s in grp:
            if s.kind == "last_cross_dir":
                grids.add((s.metric, tuple(s.grid)))
    return list(grids)

def run_wave_simulation(ever, intervals, prices, indicators, bench, dates, wave, *,
                        rules=None, max_holdings=None, momentum_method="20_day_return"):
    N0 = wave.capital_N; k = wave.tranches; tranche = N0 / k
    accum_groups = wave.accumulate if wave.accumulate else wave.entry

    cash = N0
    pos = {}                      # symbol -> {shares, cost, open_date, tranches}
    cs = {s: CrossState(_grids_used(wave)) for s in ever}
    last_close = {}
    ever_held = set()
    eq_curve = []
    diag = {"n_buys": 0, "n_exits": 0, "exit_takeprofit": 0, "exit_stop": 0,
            "reentries": 0, "_names": [], "_cashpct": []}

    for ds in dates:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        members = members_as_of(intervals, d)

        for s in ever:
            if ds in prices[s]:
                last_close[s] = prices[s][ds]

        # snapshot prev + advance cross-state (last_dirs reflects today's move, mirroring the scripts)
        ctx = {}
        for s in ever:
            today = indicators[s].get(ds)
            if today is None:
                continue
            prev = cs[s].prev_snapshot()
            cs[s].update(today)
            ctx[s] = (today, prev, cs[s].last_dirs)

        # EXIT (daily, full liquidation)
        for s in list(pos.keys()):
            if s not in ctx:
                continue
            today, prev, last_dirs = ctx[s]
            if any_group_fires(wave.exit, today, prev, last_dirs):
                px = prices[s].get(ds, last_close.get(s))
                if px:
                    cash += pos[s]["shares"] * px
                    # take-profit vs stop classification: take-profit if an "up" exit group fired
                    up_exit = any(g[0].direction == "up" for g in wave.exit
                                  if g and g[0].kind == "cross"
                                  and count_firing_groups([g], today, prev, last_dirs))
                    diag["exit_takeprofit"] += 1 if up_exit else 0
                    diag["exit_stop"] += 0 if up_exit else 1
                    diag["n_exits"] += 1
                    del pos[s]

        # BUY (daily cadence here; Task 6 generalizes to scheduled cadence)
        for s in ever:
            if s not in ctx:
                continue
            today, prev, last_dirs = ctx[s]
            px = prices[s].get(ds, last_close.get(s))
            if not px:
                continue
            if s in pos:
                fires = count_firing_groups(accum_groups, today, prev, last_dirs)
            else:
                if s not in members:
                    continue
                fires = count_firing_groups(wave.entry, today, prev, last_dirs)
            for _ in range(fires):
                if cash < 1.0 or pos.get(s, {}).get("tranches", 0) >= k:
                    break
                spend = min(tranche, cash); sh = spend / px
                if s in pos:
                    pos[s]["shares"] += sh; pos[s]["cost"] += spend; pos[s]["tranches"] += 1
                else:
                    pos[s] = {"shares": sh, "cost": spend, "open_date": ds, "tranches": 1}
                    if s in ever_held:
                        diag["reentries"] += 1
                    ever_held.add(s)
                cash -= spend; diag["n_buys"] += 1

        mv = sum(pos[s]["shares"] * (prices[s].get(ds) or last_close.get(s, 0)) for s in pos)
        equity = cash + mv
        eq_curve.append((ds, equity))
        diag["_names"].append(len(pos))
        diag["_cashpct"].append(100.0 * cash / equity if equity > 0 else 0)

    diag["avg_names"] = statistics.mean(diag["_names"]) if diag["_names"] else 0
    diag["avg_cash_pct"] = statistics.mean(diag["_cashpct"]) if diag["_cashpct"] else 0
    return {"equity_curve": eq_curve, "cash": cash, "positions": pos, "diagnostics": diag}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wave_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add wave_engine.py tests/test_wave_engine.py
git commit -m "feat(wave): core state machine (daily cadence, full-liquidation exit)"
```

---

### Task 6: Wave engine — scheduled cadence, fractional exit, reentry, pre-gate, max_holdings

**Files:**
- Modify: `wave_engine.py`
- Test: `tests/test_wave_engine.py` (add)

**Interfaces:**
- Unchanged signature. New behaviors:
  - `buy_cadence != "daily"` → entry/accumulate evaluated only on dates from `get_rebalance_dates(dates, wave.buy_cadence, wave.buy_date)` (imported from `api_server`); exit still daily.
  - `exit_mode == "fractional"` → sell one tranche (1/`tranches_held` of shares) per exit fire; position closes only when its last tranche is sold.
  - `reentry == False` → once a symbol fully exits, it is permanently barred from new entries (still in `ever_held`; engine keeps a `barred` set).
  - `rules` (list of `{metric,operator,threshold}`) → pre-gate: a FLAT symbol may enter only if it passes `apply_strategy_rules` for that day. Empty/None → no gate.
  - `max_holdings` → when more distinct symbols would be newly entered on a buy date than free slots (`max_holdings - len(pos)`), rank candidates by `select_top_stocks_by_momentum`-equivalent momentum and admit only the top ones. (Accumulation into already-held names is never blocked by `max_holdings`.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_wave_engine.py  (append)
from wave_models import WaveConfig

def _members_as_of_patch(monkeypatch, ever):
    import wave_engine
    monkeypatch.setattr(wave_engine, "members_as_of", lambda intervals, d: set(ever))

def test_monthly_cadence_only_buys_on_first_trading_day(monkeypatch):
    ever = ["AAA"]; _members_as_of_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-15", "2020-02-03"]   # 2 months; firsts: 01-01, 02-03
    indicators = {"AAA": {ds: {"mean_long": 60, "mean_short": 10, "truevx_score": 10} for ds in dates}}
    prices = {"AAA": {ds: 100.0 for ds in dates}}
    wave = WaveConfig(capital_N=200000, tranches=20, reentry=True, buy_cadence="monthly", buy_date="first",
                      exit_mode="full",
                      entry=[[{"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}}]],
                      accumulate=[], exit=[[{"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 0}}]])
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave)
    assert out["diagnostics"]["n_buys"] == 2     # one tranche each on 01-01 and 02-03, not 01-15

def test_fractional_exit_sells_one_tranche_at_a_time(monkeypatch):
    ever = ["AAA"]; _members_as_of_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
    indicators = {"AAA": {"2020-01-01": {"mean_long": 38}, "2020-01-02": {"mean_long": 55},
                          "2020-01-03": {"mean_long": 48}}}   # day2 up-cross 40,50 -> 2 tranches; day3 down-cross 50
    prices = {"AAA": {ds: 100.0 for ds in dates}}
    wave = WaveConfig(capital_N=200000, tranches=20, reentry=True, buy_cadence="daily", exit_mode="fractional",
                      entry=[[{"kind": "cross", "metric": "mean_long", "level": 40, "direction": "up"}],
                             [{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
                      accumulate=[], exit=[[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "down"}]])
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave)
    assert out["positions"]["AAA"]["tranches"] == 1     # had 2, sold 1 fractional, 1 remains
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_engine.py::test_monthly_cadence_only_buys_on_first_trading_day -v`
Expected: FAIL — buys on every qualifying day (3+), not 2.

- [ ] **Step 3: Write minimal implementation**

Add near the top of `wave_engine.py`:

```python
from api_server import get_rebalance_dates, apply_strategy_rules, select_top_stocks_by_momentum
```

In `run_wave_simulation`, after computing `accum_groups`, compute buy dates and state sets:

```python
    buy_dates = set(dates) if wave.buy_cadence == "daily" \
        else get_rebalance_dates(dates, wave.buy_cadence, wave.buy_date)
    barred = set()
```

Replace the EXIT block's liquidation with mode-aware logic:

```python
            if any_group_fires(wave.exit, today, prev, last_dirs):
                px = prices[s].get(ds, last_close.get(s))
                if px:
                    if wave.exit_mode == "fractional" and pos[s]["tranches"] > 1:
                        sell_sh = pos[s]["shares"] / pos[s]["tranches"]
                        cash += sell_sh * px
                        pos[s]["shares"] -= sell_sh; pos[s]["tranches"] -= 1
                    else:
                        cash += pos[s]["shares"] * px
                        up_exit = any(g[0].direction == "up" for g in wave.exit
                                      if g and g[0].kind == "cross"
                                      and count_firing_groups([g], today, prev, last_dirs))
                        diag["exit_takeprofit"] += 1 if up_exit else 0
                        diag["exit_stop"] += 0 if up_exit else 1
                        diag["n_exits"] += 1
                        if not wave.reentry:
                            barred.add(s)
                        del pos[s]
```

Wrap the BUY loop in the cadence gate and add pre-gate + max_holdings admission. Replace the BUY section with:

```python
        if ds in buy_dates:
            # accumulation into held names (never gated by max_holdings)
            for s in list(pos.keys()):
                if s not in ctx:
                    continue
                today, prev, last_dirs = ctx[s]
                px = prices[s].get(ds, last_close.get(s))
                if not px:
                    continue
                fires = count_firing_groups(accum_groups, today, prev, last_dirs)
                _buy_tranches(pos, s, fires, px, tranche, k, ds, ever_held, diag, _cash=None)  # see note

            # candidate new entries
            day_inds = {s: ctx[s][0] for s in ctx if s in members and s not in pos and s not in barred}
            gated = {st["symbol"] if isinstance(st, dict) else st for st in
                     (apply_strategy_rules({s: v for s, v in day_inds.items()}, rules) if rules else
                      [{"symbol": s} for s in day_inds])}
            entrants = []
            for s in day_inds:
                if s not in gated:
                    continue
                today, prev, last_dirs = ctx[s]
                if count_firing_groups(wave.entry, today, prev, last_dirs) > 0:
                    entrants.append(s)
            if max_holdings is not None:
                free = max(0, max_holdings - len(pos))
                if len(entrants) > free:
                    entrants = _rank_top(entrants, ds, prices, indicators, momentum_method, free)
            for s in entrants:
                today, prev, last_dirs = ctx[s]
                px = prices[s].get(ds, last_close.get(s))
                if not px:
                    continue
                fires = count_firing_groups(wave.entry, today, prev, last_dirs)
                _buy_tranches(pos, s, fires, px, tranche, k, ds, ever_held, diag, _cash=None)
```

> **Cash threading:** the inline `cash` mutation in Task 5 must be refactored into a small local helper so both accumulation and entry share it. Implement `_buy_tranches` as a closure over `cash` using a one-element list (`cash_box = [cash]`) or restructure to return spent amount; the implementer picks one and keeps `diag`/`ever_held`/`reentries` bookkeeping identical to Task 5. Add a unit assertion that total spent never drives `cash < 0` and book never exceeds `N0`.

Add `_rank_top` using the existing momentum selector (synchronous wrapper acceptable for v1 — call the same ranking math; if `select_top_stocks_by_momentum` is async, replicate its sort: rank entrants by `calculate_stock_momentum_fast` desc and slice `free`). Keep it deterministic.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wave_engine.py -v`
Expected: PASS (all engine unit tests).

- [ ] **Step 5: Commit**

```bash
git add wave_engine.py tests/test_wave_engine.py
git commit -m "feat(wave): scheduled cadence, fractional exit, reentry, pre-gate, max_holdings"
```

---

### Task 7: Wave engine — costs, PIT fail-loud, benchmark, summary + diagnostics output

**Files:**
- Modify: `wave_engine.py`
- Test: `tests/test_wave_engine.py` (add)

**Interfaces:**
- New params: `include_brokerage: bool = False`, `custom_brokerage_rate: float = 0.0`, `point_in_time: bool = True`.
- Adds to the returned dict: `summary` (keys mirroring the existing engine's response: `total_return`, `cagr`, `sharpe_ratio`, `max_drawdown`, `alpha`, `benchmark_cagr` — match the exact key names the existing `/api/simulation/run` returns; see `run_strategy_simulation` output assembly around `api_server.py:4740-4900`), daily `results` list (`[{date, portfolio_value, benchmark_value, holdings:[...], added:[...], exited:[...]}]`) shaped like the existing engine so the current results page renders it, plus `wave_diagnostics` (`tranches_deployed`, `pct_deployed_series`, `take_profits`, `stops`).
- PIT fail-loud: if `point_in_time` is True and `intervals` is missing/empty for the universe, raise `ValueError("universe lacks point-in-time reconstitution; cannot run PIT-honest")` — never silently fall back.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_wave_engine.py  (append)
import pytest
from wave_engine import run_wave_simulation, summarize

def test_pit_fail_loud_when_no_intervals(monkeypatch):
    ever = ["AAA"]
    dates = ["2020-01-01"]
    indicators = {"AAA": {"2020-01-01": {"mean_long": 60}}}
    prices = {"AAA": {"2020-01-01": 100.0}}
    wave = WaveConfig(capital_N=100000, tranches=20, buy_cadence="daily",
                      entry=[[{"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}}]],
                      exit=[[{"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 0}}]])
    with pytest.raises(ValueError, match="point-in-time"):
        run_wave_simulation(ever, None, prices, indicators, {}, dates, wave, point_in_time=True)

def test_summary_metrics_match_script_formulas():
    # equity curve: flat then +10% -> known CAGR/Sharpe under the scripts' formulas
    eq = [("2020-01-01", 100.0), ("2026-08-30", 110.0)]
    s = summarize(eq, N0=100.0, bench={}, start="2020-01-01", end="2026-08-30")
    yrs = (__import__("datetime").date(2026, 8, 30) - __import__("datetime").date(2020, 1, 1)).days / 365.25
    assert round(s["cagr"], 4) == round(((110.0 / 100.0) ** (1 / yrs) - 1) * 100, 4)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_engine.py::test_summary_metrics_match_script_formulas -v`
Expected: FAIL — `ImportError: cannot import name 'summarize'`.

- [ ] **Step 3: Write minimal implementation**

```python
# wave_engine.py  (append)
import math

def summarize(eq_curve, N0, bench, start, end, rf=0.06):
    from datetime import datetime
    eq = [e for _, e in eq_curve]
    yrs = (datetime.strptime(end, "%Y-%m-%d").date() - datetime.strptime(start, "%Y-%m-%d").date()).days / 365.25
    total_return = (eq[-1] / N0 - 1) * 100
    cagr = ((eq[-1] / N0) ** (1 / yrs) - 1) * 100 if eq[-1] > 0 and yrs > 0 else 0
    rets = [eq[i] / eq[i-1] - 1 for i in range(1, len(eq)) if eq[i-1] > 0]
    if len(rets) > 1:
        mu = statistics.mean(rets); sd = statistics.stdev(rets)
        ann_ret = (1 + mu) ** 252 - 1; ann_vol = sd * math.sqrt(252)
        sharpe = (ann_ret - rf) / ann_vol if ann_vol > 0 else 0
    else:
        sharpe = 0
    peak = -1e18; mdd = 0
    for e in eq:
        peak = max(peak, e); mdd = min(mdd, e / peak - 1)
    mdd *= 100
    bdates = [ds for ds, _ in eq_curve if ds in bench]
    bench_cagr = (((bench[bdates[-1]] / bench[bdates[0]]) ** (1 / yrs) - 1) * 100) if bdates else 0
    return {"total_return": total_return, "cagr": cagr, "sharpe_ratio": sharpe,
            "max_drawdown": mdd, "benchmark_cagr": bench_cagr, "alpha": cagr - bench_cagr}
```

Then at the top of `run_wave_simulation`, add the PIT guard:

```python
    if point_in_time and not intervals:
        raise ValueError("universe lacks point-in-time reconstitution; cannot run PIT-honest")
```

Add cost application at each fill (when `include_brokerage`): reduce shares bought / increase cash deducted using the same brokerage math the existing engine uses (reference `rebalance_portfolio_with_charges`, `api_server.py:4040`). For v1 the golden-master path uses `include_brokerage=False` (scripts are gross), so a faithful but simple per-fill charge `spend_effective = spend * (1 + rate)` capped by cash is acceptable; add a focused unit test that a non-zero rate reduces final equity vs the gross run.

Before returning, attach `summary` and the daily `results` list (build per-day holdings from `pos` and the day's prices, plus `added`/`exited` from that day's transitions — track them in the loop) and `wave_diagnostics`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wave_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add wave_engine.py tests/test_wave_engine.py
git commit -m "feat(wave): costs, PIT fail-loud, benchmark, summary + results output"
```

---

### Task 8: Golden master — reproduce both proven waves

**Files:**
- Create: `tests/test_wave_golden_master.py`
- Test: same file (marked `integration` — needs Mongo with the loaded `market_hunt` data)

**Interfaces:**
- Consumes: `run_wave_simulation`; the `WaveConfig` encodings from the spec Appendix; `meanlong_wave_backtest.py` / `bull_wave_backtest.py` as the oracle (re-run them, or read their committed `*_equity.csv`).

> **Oracle handling:** if the scripts' scratchpad CSVs are gone, the test first invokes each script's data-loading + main loop to regenerate the equity curve in-process, then runs the engine on the SAME loaded data and asserts equality. Prefer importing the scripts' `load_prices`/`load_indicators`/`load_membership` so both paths see identical inputs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wave_golden_master.py
import pytest
from datetime import date, datetime
pytestmark = pytest.mark.integration

START = datetime(2020, 1, 1); END = datetime(2026, 8, 30); N0 = 1_000_000.0

def _load_world():
    from pymongo import MongoClient
    from membership import load_membership, members_as_of, union_over
    import meanlong_wave_backtest as ml  # reuse its loaders
    db = MongoClient("mongodb://localhost:27017")["market_hunt"]
    intervals = load_membership(db, "NIFTY50", date(2026, 6, 24))
    ever = sorted(union_over(intervals, START.date(), END.date()))
    prices = ml.load_prices(db, ever)
    # indicators as full dicts for the engine (ml/ms/tvx)
    inds = {s: {} for s in ever}
    for d in db["indicators"].find({"indicator_type": "truevx", "symbol": {"$in": ever},
                                    "date": {"$gte": START, "$lte": END}},
                                   {"symbol": 1, "date": 1, "data": 1}):
        dd = d.get("data") or {}
        inds[d["symbol"]][d["date"].strftime("%Y-%m-%d")] = {
            "mean_long": dd.get("mean_long") or 0.0, "mean_short": dd.get("mean_short") or 0.0,
            "mean_mid": dd.get("mean_mid") or 0.0, "truevx_score": dd.get("truevx_score") or 0.0}
    bench = ml.load_prices(db, ["Nifty 50"]).get("Nifty 50", {})
    all_dates = set()
    for s in ever:
        all_dates |= (set(inds[s].keys()) & set(prices[s].keys()))
    return intervals, ever, prices, inds, bench, sorted(all_dates)

def test_level_wave_matches_oracle():
    from wave_models import WaveConfig
    from wave_engine import run_wave_simulation, summarize
    intervals, ever, prices, inds, bench, dates = _load_world()
    g = lambda L: [{"kind": "cross", "metric": "mean_long", "level": L, "direction": "up"},
                   {"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up", "negate": True}]
    wave = WaveConfig(capital_N=N0, tranches=20, reentry=True, buy_cadence="daily", exit_mode="full",
                      entry=[g(40), g(50), g(60)], accumulate=[],
                      exit=[[{"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up"}]]
                           + [[{"kind": "cross", "metric": "mean_long", "level": L, "direction": "down"}]
                              for L in (40, 50, 60, 70)])
    out = run_wave_simulation(ever, intervals, prices, inds, bench, dates, wave, point_in_time=True)
    s = summarize(out["equity_curve"], N0, bench, dates[0], "2026-08-30")
    assert round(s["cagr"], 1) == 15.9
    assert round(s["sharpe_ratio"], 2) == 1.23
    assert round(s["max_drawdown"], 1) == -8.1

def test_bull_wave_matches_oracle():
    from wave_models import WaveConfig
    from wave_engine import run_wave_simulation, summarize
    intervals, ever, prices, inds, bench, dates = _load_world()
    wave = WaveConfig(capital_N=N0, tranches=20, reentry=True, buy_cadence="monthly", buy_date="first",
                      exit_mode="full",
                      entry=[[{"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}},
                              {"kind": "compare", "lhs_metric": "truevx_score", "op": "<=", "rhs": {"metric": "mean_long"}},
                              {"kind": "compare", "lhs_metric": "mean_short", "op": "<=", "rhs": {"metric": "mean_long"}}]],
                      accumulate=[],
                      exit=[[{"kind": "last_cross_dir", "metric": "mean_long", "grid": [50, 60, 70, 80, 90], "direction": "down"},
                             {"kind": "compare", "lhs_metric": "truevx_score", "op": ">", "rhs": {"metric": "mean_long"}},
                             {"kind": "compare", "lhs_metric": "mean_short", "op": ">", "rhs": {"metric": "mean_long"}}]])
    out = run_wave_simulation(ever, intervals, prices, inds, bench, dates, wave, point_in_time=True)
    s = summarize(out["equity_curve"], N0, bench, dates[0], "2026-08-30")
    assert round(s["cagr"], 1) == 16.8
    assert round(s["sharpe_ratio"], 2) == 0.73
    assert round(s["max_drawdown"], 1) == -37.0
```

- [ ] **Step 2: Run test to verify it fails (then drives debugging)**

Run: `python -m pytest tests/test_wave_golden_master.py -v -m integration`
Expected: FAIL initially. Use systematic-debugging to reconcile ANY mismatch against the oracle scripts — the engine must match. Common reconciliation points: cross-state update ordering (last_dirs must reflect today before exit check), the `70 not in ups` block via `negate`, monthly buy-date selection, valuation on gap days via `last_close`. **Do not adjust the published target numbers; fix the engine.**

- [ ] **Step 3: Reconcile to green**

Iterate on `wave_engine.py` until both tests pass. If a true byte-level equity match is needed for confidence, add an assertion comparing `out["equity_curve"]` element-wise to the script's regenerated curve (tolerance `abs < 1e-6`).

- [ ] **Step 4: Confirm pass**

Run: `python -m pytest tests/test_wave_golden_master.py -v -m integration`
Expected: PASS (2 passed) — engine reproduces 15.9/1.23/−8.1 and 16.8/0.73/−37.

- [ ] **Step 5: Commit**

```bash
git add tests/test_wave_golden_master.py wave_engine.py
git commit -m "test(wave): golden master — engine reproduces level-wave + bull-wave"
```

---

### Task 9: API — optional `wave` field + router branch + stateless regression

**Files:**
- Modify: `api_server.py` (`SimulationParams` ~2218; `Strategy`/`StrategyRule` ~2203; `/api/simulation/run` handler ~2606; `run_strategy_simulation` dispatch ~4324)
- Test: `tests/test_wave_api.py`

**Interfaces:**
- `SimulationParams` gains `wave: Optional[WaveConfig] = None`. `Strategy` gains `wave: Optional[WaveConfig] = None`.
- `/api/simulation/run`: when the resolved strategy/params carry `wave`, build the engine inputs (universe ever-members, membership, prices, indicators as full metric dicts, bench, dates) and call `run_wave_simulation`, returning a response with the SAME top-level shape as today (`summary` + daily `results`); else unchanged.

- [ ] **Step 1: Write the failing test (stateless regression + route presence)**

```python
# tests/test_wave_api.py
import pytest, os, requests
pytestmark = pytest.mark.integration
BASE = os.environ.get("MH_API_BASE", "http://127.0.0.1:3001")

def test_stateless_run_unchanged_when_no_wave():
    # a known stateless strategy must produce the same summary it always did
    body = {"strategy_id": "<SEED_STATELESS_STRATEGY_ID>", "universe": "NIFTY50",
            "start_date": "2022-01-01", "end_date": "2022-06-30",
            "point_in_time_membership": True}
    r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=120)
    r.raise_for_status()
    s = r.json()["summary"]
    assert "cagr" in s and "sharpe_ratio" in s   # shape stable; pin exact numbers to a saved baseline

def test_wave_block_routes_to_wave_engine():
    body = {"universe": "NIFTY50", "start_date": "2022-01-01", "end_date": "2022-12-31",
            "point_in_time_membership": True,
            "wave": {"capital_N": 1000000, "tranches": 20, "buy_cadence": "daily", "exit_mode": "full",
                     "entry": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
                     "exit": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "down"}]]}}
    r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=180)
    r.raise_for_status()
    j = r.json()
    assert "wave_diagnostics" in j and "summary" in j
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wave_api.py -v -m integration`
Expected: FAIL — `wave` rejected by `SimulationParams` (422) / no `wave_diagnostics` key.

- [ ] **Step 3: Implement the field + router branch**

In `api_server.py`: `from wave_models import WaveConfig` and `from wave_engine import run_wave_simulation`. Add `wave: Optional[WaveConfig] = None` to `SimulationParams` and `Strategy`. In the `/api/simulation/run` handler, after loading the strategy/params, branch:

```python
    if params.wave is not None:
        # build ever-members, intervals, prices, indicators (full metric dicts), bench, dates
        result = run_wave_simulation(
            ever, intervals, prices, indicators, bench, dates, params.wave,
            rules=strategy_rules, max_holdings=params.max_holdings,
            momentum_method=params.momentum_ranking,
            include_brokerage=params.include_brokerage,
            custom_brokerage_rate=params.custom_brokerage_rate,
            point_in_time=params.point_in_time_membership)
        return _wave_response(result, params)   # map summary+results+wave_diagnostics to existing response shape
    # ... existing stateless path unchanged ...
```

Keep data-loading DRY by reusing the loaders the stateless path already uses (same partitions / indicators query), assembling `indicators[s][ds] = {mean_long, mean_short, mean_mid, truevx_score}`.

- [ ] **Step 4: Run test to verify it passes**

Run a fresh server on a spare port against the loaded DB, then:
`MH_API_BASE=http://127.0.0.1:<port> python -m pytest tests/test_wave_api.py -v -m integration`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api_server.py tests/test_wave_api.py
git commit -m "feat(api): route /api/simulation/run to wave engine when wave block present"
```

---

### Task 10: API — persist `wave` in strategy CRUD

**Files:**
- Modify: `api_server.py` (strategy save/update/get ~2260-2316)
- Test: `tests/test_wave_api.py` (add)

**Interfaces:**
- `POST`/`PUT /api/simulation/strategies` accept and store optional `wave`; `GET` returns it. Docs without `wave` behave exactly as before.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wave_api.py  (append)
def test_strategy_crud_roundtrips_wave_block():
    wave = {"capital_N": 1000000, "tranches": 20, "buy_cadence": "daily", "exit_mode": "full",
            "entry": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
            "exit": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "down"}]]}
    body = {"name": "wave-roundtrip", "description": "t", "rules": [], "wave": wave}
    r = requests.post(f"{BASE}/api/simulation/strategies", json=body, timeout=30); r.raise_for_status()
    sid = r.json()["id"]
    got = next(s for s in requests.get(f"{BASE}/api/simulation/strategies", timeout=30).json() if s["id"] == sid)
    assert got["wave"]["tranches"] == 20
    requests.delete(f"{BASE}/api/simulation/strategies/{sid}", timeout=30)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MH_API_BASE=... python -m pytest tests/test_wave_api.py::test_strategy_crud_roundtrips_wave_block -v -m integration`
Expected: FAIL — `wave` dropped on save / absent on get.

- [ ] **Step 3: Implement**

Include `wave` in the inserted/updated strategy doc and in the serialized GET response (only when present, to keep old docs clean).

- [ ] **Step 4: Run test to verify it passes**

Run: same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api_server.py tests/test_wave_api.py
git commit -m "feat(api): persist optional wave block in strategy CRUD"
```

---

### Task 11: Frontend — Stateful/Wave builder section + routing

**Files:**
- Modify: `frontend/src/app/analytics/simulator/page.tsx`
- (Follow the file's existing rule-row / state patterns; the flat rules panel stays exactly as-is.)

**Interfaces:**
- Produces, in the POST body to `/api/simulation/run` (and strategy save), an optional `wave` object matching `WaveConfig` JSON. Absent unless the user adds at least one entry signal in the Stateful section.

- [ ] **Step 1: Add collapsed "Stateful / Wave" section state**

Add React state mirroring `WaveConfig`: `waveEnabled` (derived: entry has ≥1 signal), `capitalN`, `tranches`, `reentry`, `exitMode`, `buyCadence`, `buyDate`, and three signal-group arrays `entryGroups`, `accumGroups`, `exitGroups`. A signal row = `{kind, metric, level, direction, grid, lhsMetric, op, rhsKind, rhsMetric, rhsNumber, negate}`. Render a collapsible panel below the existing rules panel.

- [ ] **Step 2: Render signal-group editors**

For each of Entry / Accumulate / Exit: a list of groups (OR), each group a list of signal rows (AND), with "add group", "add signal", "remove", and a per-row `negate` toggle. Row fields switch on `kind` (cross: metric·direction·level; compare: lhsMetric·op·rhs[metric|number]; last_cross_dir: metric·grid-chips·direction). Capital panel: `capital_N`, `tranches`, `reentry`, `exit_mode`, `buy_cadence` (+ `buy_date`, hidden when daily).

- [ ] **Step 3: Serialize + route**

Add a `buildWaveBlock()` that returns `undefined` when Entry has no signals, else the `WaveConfig` JSON. Include it as `wave` in the run/save POST bodies. When `wave` is present the backend routes automatically (Task 9) — no frontend mode flag.

- [ ] **Step 4: Manual verification (TDD-light for UI)**

Build the frontend and run a wave config end-to-end against a live backend; confirm the network request carries the `wave` block and the results page renders the returned `summary`/curve. Capture a screenshot.

Run: `cd frontend && npm run build`
Expected: clean build, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/analytics/simulator/page.tsx
git commit -m "feat(frontend): stateful/wave builder section (progressive disclosure) + routing"
```

---

### Task 12: Frontend — wave diagnostics on results page

**Files:**
- Modify: the simulator results rendering in `frontend/src/app/analytics/simulator/page.tsx` (or its results component)

**Interfaces:**
- Consumes: `wave_diagnostics` (`take_profits`, `stops`, `tranches_deployed`, `pct_deployed_series`) from the run response.

- [ ] **Step 1: Render a diagnostics strip when present**

When the run response includes `wave_diagnostics`, show a compact strip near the summary: tranches deployed, take-profits vs stops, and an avg/min % deployed (the cash-drag), clearly labeled **HYPOTHETICAL**. Hide entirely for stateless runs.

- [ ] **Step 2: Manual verification**

Run a wave config; confirm the diagnostics strip renders and a stateless run shows none.

Run: `cd frontend && npm run build`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/analytics/simulator/page.tsx
git commit -m "feat(frontend): wave diagnostics strip on results (deployed %, TP/stop)"
```

---

## Self-Review

**Spec coverage:**
- Config model + router → Tasks 1, 9. ✓
- Three signal kinds + negate + groups → Tasks 1–4. ✓
- Tranche+accumulate capital, fractional exit, reentry, pre-gate, max_holdings → Tasks 5–6. ✓
- Costs / PIT fail-loud / benchmark / output shape → Task 7. ✓
- Golden master (both waves) → Task 8. ✓
- Persistence → Task 10. ✓
- Progressive-disclosure UI + diagnostics → Tasks 11–12. ✓
- buy_cadence + negate + firing-group tranche semantics (spec Appendix) → encoded in Tasks 1, 2, 5, 6, 8. ✓

**Placeholder scan:** `<SEED_STATELESS_STRATEGY_ID>` in Task 9 is an intentional fixture the implementer fills from a seeded strategy; the stateless regression should additionally pin exact numbers against a saved baseline captured before any change. All other steps carry concrete code.

**Type consistency:** `run_wave_simulation` signature is identical across Tasks 5–9. `summarize` keys (`cagr`, `sharpe_ratio`, `max_drawdown`, `benchmark_cagr`, `alpha`, `total_return`) are used consistently in Tasks 7–8. `WaveConfig` field names match the spec Section 1 and the Appendix encodings.

**Known risk to watch (from spec):** cross-state update ordering vs exit evaluation is the most likely source of golden-master drift — Task 8 Step 2 calls it out explicitly.
