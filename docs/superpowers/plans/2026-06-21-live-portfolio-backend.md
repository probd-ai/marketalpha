# Live Portfolio — Backend Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add portfolio CRUD + a composite-run endpoint to the market-hunt FastAPI backend, so a portfolio (a bundle of capital-weighted strategy "sleeves") can be created, persisted in Mongo, and run "live" to today — returning a blended ₹ curve vs Nifty 50, current book, sleeve attribution, and rebalance actions.

**Architecture:** All composition + validation logic lives in **pure, unit-testable functions** in a new `portfolio_composition.py` (no Mongo, no server) — that's where correctness risk is, so that's where the TDD rigor goes. Pydantic models live in `portfolio_models.py`. Thin endpoints in `api_server.py` resolve the universe + load strategies (reusing existing helpers), call the **existing `run_strategy_simulation`** once per sleeve, then delegate to `compose_portfolio`. Endpoint tests are live-server integration smoke (the repo's existing convention), marked `integration`.

**Tech Stack:** Python 3.12 · FastAPI · Pydantic v2 · PyMongo (`mongo_conn.db`) · numpy (already a dep) · pytest 9 (`requests` for integration). Design spec: `docs/superpowers/specs/2026-06-21-live-portfolio-design.md`.

## Global Constraints

- **All backend work is in the `market-hunt/` directory, which is its own git repo** (separate from tvagent, and gitignored by tvagent). Run python, pytest, and git **from inside `market-hunt/`**.
- **Python interpreter:** `.venv/Scripts/python.exe` (Windows). **Console encoding:** prefix runs with `PYTHONIOENCODING=utf-8` if any emoji-printing path is hit.
- **No new runtime dependencies.** numpy, pymongo, fastapi, pydantic, requests are already available. Do **not** add httpx/mongomock/testcontainers.
- **Reuse `run_strategy_simulation` unchanged** — do not fork or edit the simulation engine.
- **Point-in-time honesty:** portfolio runs default `point_in_time_membership=True`; a universe without reconstitution data must **fail loud** (propagate the existing HTTP 400 from the PIT path) — never silently fall back to the biased snapshot.
- **Pure functions** in `portfolio_composition.py` take/return plain dicts & lists — no DB, no FastAPI, no I/O — so they unit-test without a server.
- **Test markers:** composition/validation tests run by default; endpoint tests use `@pytest.mark.integration` (deselected by default per `pytest.ini`; run explicitly with `-m integration` against a live server on **port 3001**).
- **Earliest inception:** `2022-01-01` (microcap/PIT data validity floor).
- **Commits** authored `Arpitha B` with empty email, **no `Co-Authored-By`**. Commit with `git -c commit.gpgsign=false commit --author="Arpitha B <>"`. Prefer new commits over amending.

---

## File Structure

```
market-hunt/
  portfolio_models.py            # NEW: Sleeve, Portfolio Pydantic models
  portfolio_composition.py       # NEW: pure functions — validate + compose
  api_server.py                  # MODIFY: add portfolio CRUD + /run endpoints
  test_portfolio_composition.py  # NEW: unit tests (default suite)
  test_portfolio_api.py          # NEW: integration tests (@pytest.mark.integration)
```

---

### Task 1: Portfolio Pydantic models

**Files:**
- Create: `market-hunt/portfolio_models.py`
- Test: `market-hunt/test_portfolio_composition.py` (create; model tests added here, composition tests appended later)

**Interfaces:**
- Produces: `Sleeve` and `Portfolio` Pydantic models. `Sleeve` fields: `id:str`, `label:str`, `strategy_id:str`, `weight:float`, `universe:str`, `rebalance_frequency:str="monthly"`, `rebalance_date:str="first"`, `rebalance_type:str="equal_weight"`, `max_holdings:int=10`, `momentum_ranking:str="20_day_return"`, `include_brokerage:bool=True`, `exchange:str="NSE"`. `Portfolio` fields: `id:Optional[str]=None`, `name:str`, `description:str=""`, `total_capital:float=1000000`, `benchmark_symbol:str="Nifty 50"`, `inception_date:str`, `point_in_time_membership:bool=True`, `sleeves:List[Sleeve]`, `created_at:Optional[str]=None`, `last_modified:Optional[str]=None`.

- [ ] **Step 1: Write the failing test** (`market-hunt/test_portfolio_composition.py`)

```python
from portfolio_models import Sleeve, Portfolio


def test_portfolio_model_defaults_and_serialization():
    p = Portfolio(
        name="Growth Book",
        inception_date="2025-06-21",
        sleeves=[Sleeve(id="sleeve_1", label="Mid", strategy_id="strategy_1",
                        weight=100, universe="NIFTYMIDCAP150")],
    )
    assert p.total_capital == 1000000
    assert p.point_in_time_membership is True
    assert p.benchmark_symbol == "Nifty 50"
    s = p.sleeves[0]
    assert s.rebalance_frequency == "monthly" and s.max_holdings == 10
    dumped = p.model_dump()
    assert dumped["sleeves"][0]["universe"] == "NIFTYMIDCAP150"
    assert dumped["name"] == "Growth Book"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `market-hunt/`): `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portfolio_models'`.

- [ ] **Step 3: Write `portfolio_models.py`**

```python
from typing import List, Optional
from pydantic import BaseModel


class Sleeve(BaseModel):
    id: str
    label: str
    strategy_id: str
    weight: float                       # capital % ; sleeve weights sum to 100
    universe: str
    rebalance_frequency: str = "monthly"   # monthly|weekly|quarterly|dynamic
    rebalance_date: str = "first"          # first|last|mid
    rebalance_type: str = "equal_weight"   # equal_weight|skewed
    max_holdings: int = 10
    momentum_ranking: str = "20_day_return"
    include_brokerage: bool = True
    exchange: str = "NSE"


class Portfolio(BaseModel):
    id: Optional[str] = None
    name: str
    description: str = ""
    total_capital: float = 1000000
    benchmark_symbol: str = "Nifty 50"
    inception_date: str                  # YYYY-MM-DD ; floor 2022-01-01
    point_in_time_membership: bool = True
    sleeves: List[Sleeve]
    created_at: Optional[str] = None
    last_modified: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(portfolio): Sleeve + Portfolio pydantic models" -- portfolio_models.py test_portfolio_composition.py
```
(Run `git add portfolio_models.py test_portfolio_composition.py` first.)

---

### Task 2: `validate_portfolio` (pure)

**Files:**
- Create: `market-hunt/portfolio_composition.py`
- Test: `market-hunt/test_portfolio_composition.py` (append)

**Interfaces:**
- Consumes: nothing (operates on plain dicts).
- Produces: `EARLIEST_INCEPTION: str = "2022-01-01"` and `validate_portfolio(p: dict, known_strategy_ids: set[str]) -> list[str]` — returns a list of human-readable error strings (empty list = valid).

- [ ] **Step 1: Write the failing test** (append to `test_portfolio_composition.py`)

```python
from portfolio_composition import validate_portfolio


def _valid_portfolio_dict():
    return {
        "name": "P", "inception_date": "2024-01-01", "total_capital": 1000000,
        "sleeves": [
            {"strategy_id": "s1", "weight": 60, "universe": "NIFTY500"},
            {"strategy_id": "s2", "weight": 40, "universe": "NIFTYMIDCAP150"},
        ],
    }


def test_validate_accepts_good_portfolio():
    assert validate_portfolio(_valid_portfolio_dict(), {"s1", "s2"}) == []


def test_validate_rejects_bad_weights():
    p = _valid_portfolio_dict()
    p["sleeves"][0]["weight"] = 50  # now sums to 90
    errs = validate_portfolio(p, {"s1", "s2"})
    assert any("sum to 100" in e for e in errs)


def test_validate_rejects_unknown_strategy():
    errs = validate_portfolio(_valid_portfolio_dict(), {"s1"})  # s2 unknown
    assert any("unknown strategy_id: s2" in e for e in errs)


def test_validate_rejects_no_sleeves():
    p = _valid_portfolio_dict(); p["sleeves"] = []
    errs = validate_portfolio(p, set())
    assert any("at least one sleeve" in e for e in errs)


def test_validate_rejects_early_inception():
    p = _valid_portfolio_dict(); p["inception_date"] = "2021-06-01"
    errs = validate_portfolio(p, {"s1", "s2"})
    assert any("2022-01-01" in e for e in errs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -k validate -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portfolio_composition'`.

- [ ] **Step 3: Write `validate_portfolio` in `portfolio_composition.py`**

```python
EARLIEST_INCEPTION = "2022-01-01"


def validate_portfolio(p: dict, known_strategy_ids: set[str]) -> list[str]:
    """Return a list of validation error strings (empty == valid)."""
    errors: list[str] = []
    sleeves = p.get("sleeves") or []

    if not sleeves:
        errors.append("portfolio must have at least one sleeve")

    if sleeves:
        total_w = round(sum(float(s.get("weight", 0)) for s in sleeves), 4)
        if abs(total_w - 100) > 0.5:
            errors.append(f"sleeve weights must sum to 100 (got {total_w})")

    for s in sleeves:
        if s.get("strategy_id") not in known_strategy_ids:
            errors.append(f"unknown strategy_id: {s.get('strategy_id')}")
        if not s.get("universe"):
            errors.append("each sleeve requires a universe")

    inception = p.get("inception_date")
    if not inception or inception < EARLIEST_INCEPTION:
        errors.append(f"inception_date must be on or after {EARLIEST_INCEPTION}")

    return errors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -k validate -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add portfolio_composition.py test_portfolio_composition.py
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(portfolio): validate_portfolio pure function"
```

---

### Task 3: Curve blending + benchmark curve (pure)

**Files:**
- Modify: `market-hunt/portfolio_composition.py`
- Test: `market-hunt/test_portfolio_composition.py` (append)

**Interfaces:**
- Produces:
  - `blend_curve(sleeve_results: list[list[dict]]) -> list[dict]` — each inner list is one sleeve's daily entries (dicts with `"date"` and `"portfolio_value"`). Returns `[{"date","portfolio_value"}]` over the union of dates, summing sleeves and forward-filling each sleeve's last known value on dates it lacks.
  - `benchmark_curve(dates: list[str], benchmark_closes: dict[str, float], total_capital: float) -> dict[str, float]` — maps date→₹ value of Nifty 50 bought-and-held with `total_capital`, based on the first available close in `dates`. Forward-fills.

- [ ] **Step 1: Write the failing test** (append)

```python
from portfolio_composition import blend_curve, benchmark_curve


def test_blend_curve_sums_and_forward_fills():
    a = [{"date": "2024-01-01", "portfolio_value": 100},
         {"date": "2024-01-02", "portfolio_value": 110}]
    b = [{"date": "2024-01-01", "portfolio_value": 200},
         {"date": "2024-01-03", "portfolio_value": 260}]  # missing 01-02
    curve = blend_curve([a, b])
    vals = {c["date"]: c["portfolio_value"] for c in curve}
    assert vals["2024-01-01"] == 300            # 100 + 200
    assert vals["2024-01-02"] == 310            # 110 + (ff 200)
    assert vals["2024-01-03"] == 370            # (ff 110) + 260
    assert [c["date"] for c in curve] == ["2024-01-01", "2024-01-02", "2024-01-03"]


def test_benchmark_curve_scales_to_capital():
    dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
    closes = {"2024-01-01": 100.0, "2024-01-02": 110.0, "2024-01-03": 90.0}
    bm = benchmark_curve(dates, closes, total_capital=1000.0)
    assert bm["2024-01-01"] == 1000.0           # base
    assert bm["2024-01-02"] == 1100.0           # +10%
    assert bm["2024-01-03"] == 900.0            # -10% vs base


def test_benchmark_curve_forward_fills_missing_close():
    dates = ["2024-01-01", "2024-01-02"]
    closes = {"2024-01-01": 50.0}               # 01-02 missing
    bm = benchmark_curve(dates, closes, total_capital=500.0)
    assert bm["2024-01-02"] == 500.0            # carried forward
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -k "blend or benchmark" -v`
Expected: FAIL — `ImportError: cannot import name 'blend_curve'`.

- [ ] **Step 3: Add functions to `portfolio_composition.py`**

```python
def blend_curve(sleeve_results: list[list[dict]]) -> list[dict]:
    """Union dates across sleeves; sum portfolio_value, forward-filling each sleeve."""
    all_dates = sorted({d["date"] for sr in sleeve_results for d in sr})
    by_sleeve = [{d["date"]: float(d["portfolio_value"]) for d in sr} for sr in sleeve_results]
    last = [0.0] * len(sleeve_results)
    curve: list[dict] = []
    for dt in all_dates:
        total = 0.0
        for i, m in enumerate(by_sleeve):
            if dt in m:
                last[i] = m[dt]
            total += last[i]
        curve.append({"date": dt, "portfolio_value": round(total, 2)})
    return curve


def benchmark_curve(dates: list[str], benchmark_closes: dict[str, float],
                    total_capital: float) -> dict[str, float]:
    """₹ value of Nifty 50 bought-and-held with total_capital, indexed to first close."""
    base = None
    for dt in dates:
        if dt in benchmark_closes and benchmark_closes[dt]:
            base = float(benchmark_closes[dt])
            break
    out: dict[str, float] = {}
    if not base:
        return {dt: round(total_capital, 2) for dt in dates}
    last = total_capital
    for dt in dates:
        if dt in benchmark_closes and benchmark_closes[dt]:
            last = total_capital * float(benchmark_closes[dt]) / base
        out[dt] = round(last, 2)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -k "blend or benchmark" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add portfolio_composition.py test_portfolio_composition.py
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(portfolio): blended + benchmark curve composition"
```

---

### Task 4: Holdings union (pure)

**Files:**
- Modify: `market-hunt/portfolio_composition.py`
- Test: `market-hunt/test_portfolio_composition.py` (append)

**Interfaces:**
- Produces: `union_holdings(sleeve_books: list[tuple[str, list[dict]]], blended_final: float) -> list[dict]` — `sleeve_books` is a list of `(sleeve_label, holdings)` where each holding dict has `symbol, quantity, current_price, market_value, pnl`. Aggregates a symbol held by multiple sleeves (sum `market_value`/`quantity`, sum cost = `market_value - pnl`); recomputes `weight = market_value/blended_final*100` and `pnl_percent = pnl/cost*100`; multi-sleeve symbols get `sleeve` = comma-joined labels. Sorted by `market_value` desc.

- [ ] **Step 1: Write the failing test** (append)

```python
from portfolio_composition import union_holdings


def test_union_holdings_aggregates_shared_symbol():
    mid = [{"symbol": "DIXON", "quantity": 10, "current_price": 100,
            "market_value": 1000, "pnl": 200}]
    micro = [{"symbol": "DIXON", "quantity": 5, "current_price": 100,
              "market_value": 500, "pnl": 50},
             {"symbol": "KAYNES", "quantity": 4, "current_price": 250,
              "market_value": 1000, "pnl": -100}]
    book = union_holdings([("Mid", mid), ("Micro", micro)], blended_final=2500.0)
    by_sym = {h["symbol"]: h for h in book}

    dixon = by_sym["DIXON"]
    assert dixon["quantity"] == 15 and dixon["market_value"] == 1500
    assert "Mid" in dixon["sleeve"] and "Micro" in dixon["sleeve"]
    assert dixon["weight"] == 60.0                      # 1500/2500
    # cost = (1000-200)+(500-50)=1250 ; pnl=250 ; pnl% = 20.0
    assert dixon["pnl"] == 250 and dixon["pnl_percent"] == 20.0

    assert by_sym["KAYNES"]["pnl_percent"] == round(-100 / 1100 * 100, 2)
    assert [h["symbol"] for h in book] == ["DIXON", "KAYNES"]   # sorted by mkt value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -k union -v`
Expected: FAIL — `ImportError: cannot import name 'union_holdings'`.

- [ ] **Step 3: Add `union_holdings` to `portfolio_composition.py`**

```python
def union_holdings(sleeve_books: list[tuple[str, list[dict]]],
                   blended_final: float) -> list[dict]:
    """Union latest holdings across sleeves; aggregate shared symbols; recompute weight/pnl%."""
    agg: dict[str, dict] = {}
    for label, holdings in sleeve_books:
        for h in holdings:
            sym = h["symbol"]
            a = agg.get(sym)
            if a is None:
                a = {"symbol": sym, "sleeves": [], "quantity": 0.0,
                     "market_value": 0.0, "cost": 0.0,
                     "current_price": h.get("current_price", 0)}
                agg[sym] = a
            mv = float(h.get("market_value", 0))
            a["quantity"] += float(h.get("quantity", 0))
            a["market_value"] += mv
            a["cost"] += mv - float(h.get("pnl", 0))
            if label not in a["sleeves"]:
                a["sleeves"].append(label)

    out: list[dict] = []
    for a in agg.values():
        mv, cost = a["market_value"], a["cost"]
        pnl = mv - cost
        out.append({
            "symbol": a["symbol"],
            "sleeve": ", ".join(a["sleeves"]),
            "quantity": round(a["quantity"], 4),
            "current_price": a["current_price"],
            "market_value": round(mv, 2),
            "weight": round(mv / blended_final * 100, 2) if blended_final else 0,
            "pnl": round(pnl, 2),
            "pnl_percent": round(pnl / cost * 100, 2) if cost else 0,
        })
    out.sort(key=lambda x: x["market_value"], reverse=True)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -k union -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add portfolio_composition.py test_portfolio_composition.py
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(portfolio): union_holdings aggregation across sleeves"
```

---

### Task 5: Summary metrics (pure)

**Files:**
- Modify: `market-hunt/portfolio_composition.py`
- Test: `market-hunt/test_portfolio_composition.py` (append)

**Interfaces:**
- Produces:
  - `max_drawdown(values: list[float]) -> float` — worst peak-to-trough %, ≤ 0.
  - `sharpe_ratio(values: list[float], rf: float = 0.06) -> float` — annualized (252) on simple daily returns of the ₹ series.
  - `summary_metrics(curve: list[dict], total_capital: float) -> dict` — uses `curve[].portfolio_value` and `curve[].benchmark_value`; returns `{total_value,total_pnl,total_return,benchmark_return,alpha,max_drawdown,sharpe_ratio}`.

- [ ] **Step 1: Write the failing test** (append)

```python
from portfolio_composition import max_drawdown, sharpe_ratio, summary_metrics


def test_max_drawdown_basic():
    assert max_drawdown([100, 120, 90, 110]) == round((90 - 120) / 120 * 100, 2)  # -25.0
    assert max_drawdown([100, 110, 120]) == 0.0


def test_sharpe_zero_for_flat_series():
    assert sharpe_ratio([100, 100, 100]) == 0.0


def test_summary_metrics_returns_and_alpha():
    curve = [
        {"date": "2024-01-01", "portfolio_value": 1000, "benchmark_value": 1000},
        {"date": "2024-01-02", "portfolio_value": 1200, "benchmark_value": 1100},
    ]
    s = summary_metrics(curve, total_capital=1000.0)
    assert s["total_value"] == 1200 and s["total_pnl"] == 200
    assert s["total_return"] == 20.0 and s["benchmark_return"] == 10.0
    assert s["alpha"] == 10.0
    assert "max_drawdown" in s and "sharpe_ratio" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -k "drawdown or sharpe or summary" -v`
Expected: FAIL — `ImportError: cannot import name 'max_drawdown'`.

- [ ] **Step 3: Add metrics to `portfolio_composition.py`**

```python
import numpy as np


def max_drawdown(values: list[float]) -> float:
    peak = float("-inf")
    mdd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak * 100
            if dd < mdd:
                mdd = dd
    return round(mdd, 2)


def sharpe_ratio(values: list[float], rf: float = 0.06) -> float:
    if len(values) < 2:
        return 0.0
    arr = np.array(values, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    rets = rets[np.isfinite(rets)]
    if rets.size == 0:
        return 0.0
    vol = float(np.std(rets)) * np.sqrt(252)
    ann = (1 + float(np.mean(rets))) ** 252 - 1
    return round((ann - rf) / vol, 3) if vol > 0 else 0.0


def summary_metrics(curve: list[dict], total_capital: float) -> dict:
    pf = [c["portfolio_value"] for c in curve]
    bm = [c.get("benchmark_value", total_capital) for c in curve]
    final_pf = pf[-1] if pf else total_capital
    final_bm = bm[-1] if bm else total_capital
    total_return = (final_pf / total_capital - 1) * 100 if total_capital else 0.0
    bench_return = (final_bm / total_capital - 1) * 100 if total_capital else 0.0
    return {
        "total_value": round(final_pf, 2),
        "total_pnl": round(final_pf - total_capital, 2),
        "total_return": round(total_return, 2),
        "benchmark_return": round(bench_return, 2),
        "alpha": round(total_return - bench_return, 2),
        "max_drawdown": max_drawdown(pf),
        "sharpe_ratio": sharpe_ratio(pf),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -k "drawdown or sharpe or summary" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add portfolio_composition.py test_portfolio_composition.py
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(portfolio): drawdown, sharpe, summary metrics"
```

---

### Task 6: `compose_portfolio` orchestration (pure)

**Files:**
- Modify: `market-hunt/portfolio_composition.py`
- Test: `market-hunt/test_portfolio_composition.py` (append)

**Interfaces:**
- Consumes: `blend_curve`, `benchmark_curve`, `union_holdings`, `summary_metrics` (Tasks 3–5).
- Produces: `compose_portfolio(*, sleeves: list[dict], sleeve_runs: list[dict], total_capital: float, benchmark_closes: dict[str, float], portfolio_meta: dict, as_of: str) -> dict`.
  - `sleeves[i]` = sleeve meta dict with at least `id`, `label`, `weight`.
  - `sleeve_runs[i]` = output of `run_strategy_simulation` for that sleeve: a dict with `"results"` (list of daily entries, each having `date`, `portfolio_value`, `holdings`, `new_added`, `exited`), `"summary"` (with `total_return`), and optional `"point_in_time_coverage"`. Aligned index-for-index with `sleeves`.
  - `portfolio_meta` = `{id, name, point_in_time}`.
  - Returns the full payload **without** the top-level `success` flag: `{portfolio, summary, curve, current_book, sleeves, rebalance_actions}`. `rebalance_actions.next_due` is `None` in v1 (cadence date math deferred); `add`/`exit` come from each sleeve's **last** daily entry `new_added`/`exited`, tagged by sleeve label.

- [ ] **Step 1: Write the failing test** (append)

```python
from portfolio_composition import compose_portfolio


def _sleeve_run(values, holdings_last, added=None, exited=None):
    results = []
    dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
    for i, (d, v) in enumerate(zip(dates, values)):
        last = i == len(values) - 1
        results.append({
            "date": d, "portfolio_value": v,
            "holdings": holdings_last if last else [],
            "new_added": (added or []) if last else [],
            "exited": (exited or []) if last else [],
        })
    final_ret = (values[-1] / values[0] - 1) * 100
    return {"results": results, "summary": {"total_return": round(final_ret, 2)},
            "point_in_time_coverage": {"total_ever_members": 10, "covered": 9, "missing": ["X"]}}


def test_compose_portfolio_end_to_end():
    sleeves = [
        {"id": "sl1", "label": "Mid", "weight": 60},
        {"id": "sl2", "label": "Micro", "weight": 40},
    ]
    runs = [
        _sleeve_run([600, 660, 720], [{"symbol": "DIXON", "quantity": 5,
                    "current_price": 100, "market_value": 500, "pnl": 50}],
                    added=["DIXON"], exited=["OLD"]),
        _sleeve_run([400, 420, 480], [{"symbol": "KAYNES", "quantity": 2,
                    "current_price": 200, "market_value": 400, "pnl": 80}],
                    added=["KAYNES"]),
    ]
    closes = {"2024-01-01": 100.0, "2024-01-02": 105.0, "2024-01-03": 110.0}
    out = compose_portfolio(
        sleeves=sleeves, sleeve_runs=runs, total_capital=1000.0,
        benchmark_closes=closes, portfolio_meta={"id": "p1", "name": "P", "point_in_time": True},
        as_of="2024-01-03")

    assert out["curve"][-1]["portfolio_value"] == 1200          # 720 + 480
    assert out["curve"][-1]["benchmark_value"] == 1100.0        # +10% on 1000
    assert out["summary"]["total_return"] == 20.0
    assert out["summary"]["benchmark_return"] == 10.0 and out["summary"]["alpha"] == 10.0
    assert {h["symbol"] for h in out["current_book"]} == {"DIXON", "KAYNES"}
    attr = {s["id"]: s for s in out["sleeves"]}
    assert attr["sl1"]["contribution"] == round(20.0 * 60 / 100, 2)   # 12.0
    assert attr["sl1"]["coverage"]["missing"] == ["X"]
    assert out["rebalance_actions"]["next_due"] is None
    syms_added = {a["symbol"] for a in out["rebalance_actions"]["add"]}
    assert syms_added == {"DIXON", "KAYNES"}
    assert out["portfolio"]["as_of"] == "2024-01-03"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -k compose -v`
Expected: FAIL — `ImportError: cannot import name 'compose_portfolio'`.

- [ ] **Step 3: Add `compose_portfolio` to `portfolio_composition.py`**

```python
def compose_portfolio(*, sleeves: list[dict], sleeve_runs: list[dict],
                      total_capital: float, benchmark_closes: dict[str, float],
                      portfolio_meta: dict, as_of: str) -> dict:
    sleeve_results = [run.get("results", []) for run in sleeve_runs]

    curve = blend_curve(sleeve_results)
    dates = [c["date"] for c in curve]
    bm = benchmark_curve(dates, benchmark_closes, total_capital)
    for c in curve:
        c["benchmark_value"] = bm.get(c["date"], total_capital)

    blended_final = curve[-1]["portfolio_value"] if curve else total_capital

    sleeve_books = []
    for s, run in zip(sleeves, sleeve_runs):
        results = run.get("results", [])
        holdings = results[-1].get("holdings", []) if results else []
        sleeve_books.append((s["label"], holdings))
    current_book = union_holdings(sleeve_books, blended_final)

    summary = summary_metrics(curve, total_capital)

    attribution = []
    add, exit_ = [], []
    for s, run in zip(sleeves, sleeve_runs):
        results = run.get("results", [])
        sleeve_return = float(run.get("summary", {}).get("total_return", 0.0))
        last = results[-1] if results else {}
        attribution.append({
            "id": s["id"], "label": s["label"], "weight": s["weight"],
            "total_return": round(sleeve_return, 2),
            "contribution": round(sleeve_return * float(s["weight"]) / 100, 2),
            "holdings_count": len(last.get("holdings", [])),
            "coverage": run.get("point_in_time_coverage"),
        })
        for sym in last.get("new_added", []):
            add.append({"symbol": sym, "sleeve": s["label"], "target_weight": None})
        for sym in last.get("exited", []):
            exit_.append({"symbol": sym, "sleeve": s["label"]})

    return {
        "portfolio": {"id": portfolio_meta.get("id"), "name": portfolio_meta.get("name"),
                      "as_of": as_of, "inception_date": dates[0] if dates else None,
                      "point_in_time": portfolio_meta.get("point_in_time", True)},
        "summary": summary,
        "curve": curve,
        "current_book": current_book,
        "sleeves": attribution,
        "rebalance_actions": {"next_due": None, "add": add, "exit": exit_},
    }
```

- [ ] **Step 4: Run test to verify it passes, then the whole composition suite**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -v`
Expected: PASS (all composition + model tests; ~13 tests).

- [ ] **Step 5: Commit**

```bash
git add portfolio_composition.py test_portfolio_composition.py
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(portfolio): compose_portfolio orchestration"
```

---

### Task 7: Portfolio CRUD endpoints

**Files:**
- Modify: `market-hunt/api_server.py` (add models import + 5 routes; place near the existing `/api/simulation/strategies` routes, ~line 2257)
- Test: `market-hunt/test_portfolio_api.py` (create; integration)

**Interfaces:**
- Consumes: `mongo_conn.db.portfolios` (Mongo collection), `Portfolio` model (Task 1), `validate_portfolio` (Task 2), existing `mongo_conn.db.simulation_strategies`.
- Produces routes: `POST /api/portfolio` → `{success, portfolio_id}` (400 on validation errors); `GET /api/portfolio` → `{success, portfolios:[...]}`; `GET /api/portfolio/{id}` (404 if missing); `PUT /api/portfolio/{id}` (preserve id, 404 if missing, 400 on validation); `DELETE /api/portfolio/{id}`.

- [ ] **Step 1: Add imports + routes to `api_server.py`**

Near the top imports add:
```python
from portfolio_models import Portfolio
from portfolio_composition import validate_portfolio, compose_portfolio
```

After the existing `delete_strategy` route (around line 2391), add:
```python
def _known_strategy_ids() -> set:
    return {d["id"] for d in mongo_conn.db.simulation_strategies.find({}, {"id": 1})}


@app.post("/api/portfolio")
async def create_portfolio(portfolio: Portfolio):
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    data = portfolio.model_dump()
    errors = validate_portfolio(data, _known_strategy_ids())
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    data["id"] = f"portfolio_{int(datetime.now().timestamp() * 1000)}"
    data["created_at"] = datetime.now().isoformat()
    data["last_modified"] = data["created_at"]
    mongo_conn.db.portfolios.insert_one(data)
    return JSONResponse(content={"success": True, "portfolio_id": data["id"]})


@app.get("/api/portfolio")
async def list_portfolios():
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    out = []
    for doc in mongo_conn.db.portfolios.find().sort("last_modified", -1):
        doc["_id"] = str(doc["_id"])
        out.append(doc)
    return JSONResponse(content={"success": True, "portfolios": out})


@app.get("/api/portfolio/{portfolio_id}")
async def get_portfolio(portfolio_id: str):
    doc = mongo_conn.db.portfolios.find_one({"id": portfolio_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    doc["_id"] = str(doc["_id"])
    return JSONResponse(content={"success": True, "portfolio": doc})


@app.put("/api/portfolio/{portfolio_id}")
async def update_portfolio(portfolio_id: str, portfolio: Portfolio):
    existing = mongo_conn.db.portfolios.find_one({"id": portfolio_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    data = portfolio.model_dump()
    errors = validate_portfolio(data, _known_strategy_ids())
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    data.pop("id", None)
    data["last_modified"] = datetime.now().isoformat()
    mongo_conn.db.portfolios.update_one({"id": portfolio_id}, {"$set": data})
    return JSONResponse(content={"success": True, "portfolio_id": portfolio_id})


@app.delete("/api/portfolio/{portfolio_id}")
async def delete_portfolio(portfolio_id: str):
    result = mongo_conn.db.portfolios.delete_one({"id": portfolio_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return JSONResponse(content={"success": True, "portfolio_id": portfolio_id})
```

- [ ] **Step 2: Write the integration test** (`market-hunt/test_portfolio_api.py`)

```python
import pytest
import requests

BASE = "http://localhost:3001"
pytestmark = pytest.mark.integration


def _a_strategy_id():
    r = requests.get(f"{BASE}/api/simulation/strategies", timeout=10)
    strategies = r.json().get("strategies", [])
    assert strategies, "seed at least one strategy first"
    return strategies[0]["id"]


def test_portfolio_crud_roundtrip():
    sid = _a_strategy_id()
    body = {
        "name": "Test Book", "inception_date": "2024-01-01", "total_capital": 1000000,
        "sleeves": [{"id": "s1", "label": "All", "strategy_id": sid, "weight": 100,
                     "universe": "NIFTY500"}],
    }
    created = requests.post(f"{BASE}/api/portfolio", json=body, timeout=15).json()
    assert created["success"]
    pid = created["portfolio_id"]
    try:
        got = requests.get(f"{BASE}/api/portfolio/{pid}", timeout=10).json()
        assert got["portfolio"]["name"] == "Test Book"
        assert any(p["id"] == pid for p in
                   requests.get(f"{BASE}/api/portfolio", timeout=10).json()["portfolios"])
    finally:
        assert requests.delete(f"{BASE}/api/portfolio/{pid}", timeout=10).json()["success"]


def test_portfolio_rejects_bad_weights():
    sid = _a_strategy_id()
    body = {"name": "Bad", "inception_date": "2024-01-01",
            "sleeves": [{"id": "s1", "label": "A", "strategy_id": sid, "weight": 80,
                         "universe": "NIFTY500"}]}
    resp = requests.post(f"{BASE}/api/portfolio", json=body, timeout=10)
    assert resp.status_code == 400 and "sum to 100" in resp.text
```

- [ ] **Step 3: Verify the module imports (no live server needed)**

Run: `.venv/Scripts/python.exe -c "import api_server; print('import ok')"`
Expected: `import ok` (confirms the new routes + imports parse and load).

- [ ] **Step 4: Run the integration test against a live server**

Start the server in another terminal: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe api_server.py` (port 3001, needs Mongo + ≥1 saved strategy). Then:
Run: `.venv/Scripts/python.exe -m pytest test_portfolio_api.py -m integration -v`
Expected: PASS (2 tests). If no strategy exists, create one first via `POST /api/simulation/strategies`.

- [ ] **Step 5: Commit**

```bash
git add api_server.py test_portfolio_api.py
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(api): portfolio CRUD endpoints"
```

---

### Task 8: `/api/portfolio/{id}/run` composite endpoint

**Files:**
- Modify: `market-hunt/api_server.py` (add the `/run` route after the CRUD routes)
- Test: `market-hunt/test_portfolio_api.py` (append integration test)

**Interfaces:**
- Consumes: `compose_portfolio` (Task 6), existing `run_strategy_simulation`, the existing PIT universe-resolution code from `run_simulation` (membership / `union_over` / `index_meta` snapshot), `IndicatorDataManager`, `StockDataManager`, `SimulationParams` (api_server.py:2215).
- Produces: `POST /api/portfolio/{id}/run?end_date=YYYY-MM-DD` → `{success: True, **compose_portfolio(...)}`. Defaults `end_date` to today. Propagates HTTP 400 when a sleeve's universe lacks PIT reconstitution (loud, no fallback).

- [ ] **Step 1: Add the `/run` route to `api_server.py`** (after `delete_portfolio`)

```python
@app.post("/api/portfolio/{portfolio_id}/run")
async def run_portfolio(portfolio_id: str, end_date: str | None = None):
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    p = mongo_conn.db.portfolios.find_one({"id": portfolio_id})
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    as_of = end_date or datetime.now().strftime("%Y-%m-%d")
    inception = p["inception_date"]
    total_capital = float(p["total_capital"])
    pit = bool(p.get("point_in_time_membership", True))

    sleeve_meta, sleeve_runs = [], []
    from indicator_data_manager import IndicatorDataManager
    from stock_data_manager import StockDataManager

    for sleeve in p["sleeves"]:
        strategy = mongo_conn.db.simulation_strategies.find_one({"id": sleeve["strategy_id"]})
        if not strategy:
            raise HTTPException(status_code=400, detail=f"strategy {sleeve['strategy_id']} not found")

        universe = sleeve["universe"]
        params = SimulationParams(
            strategy_id=sleeve["strategy_id"],
            portfolio_base_value=round(total_capital * float(sleeve["weight"]) / 100),
            rebalance_frequency=sleeve.get("rebalance_frequency", "monthly"),
            rebalance_date=sleeve.get("rebalance_date", "first"),
            rebalance_type=sleeve.get("rebalance_type", "equal_weight"),
            universe=universe,
            benchmark_symbol="Nifty 50",
            max_holdings=int(sleeve.get("max_holdings", 10)),
            momentum_ranking=sleeve.get("momentum_ranking", "20_day_return"),
            start_date=inception,
            end_date=as_of,
            include_brokerage=bool(sleeve.get("include_brokerage", True)),
            exchange=sleeve.get("exchange", "NSE"),
            point_in_time_membership=pit,
        )

        # Resolve the universe symbols exactly as /api/simulation/run does.
        membership = None
        if pit:
            from membership import load_membership, union_over
            try:
                membership = load_membership(mongo_conn.db, universe, datetime.now().date())
            except ValueError as ve:
                raise HTTPException(status_code=400, detail=(
                    f"Point-in-time membership unavailable for {universe}: {ve} "
                    f"Load its reconstitution events first."))
            s_d = datetime.strptime(inception, "%Y-%m-%d").date()
            e_d = datetime.strptime(as_of, "%Y-%m-%d").date()
            universe_symbols = sorted(union_over(membership, s_d, e_d))
        else:
            universe_symbols = [d["Symbol"] for d in
                                mongo_conn.db.index_meta.find({"index_name": universe}, {"Symbol": 1})]

        async with IndicatorDataManager() as dm:
            run = await run_strategy_simulation(dm, strategy, universe_symbols, params,
                                                membership=membership)
        sleeve_meta.append({"id": sleeve["id"], "label": sleeve["label"], "weight": sleeve["weight"]})
        sleeve_runs.append(run)

    # Nifty 50 closes over the window (single benchmark series).
    benchmark_closes = {}
    async with StockDataManager() as sm:
        bench = await sm.get_price_data(symbol="Nifty 50",
                                        start_date=datetime.strptime(inception, "%Y-%m-%d"),
                                        end_date=datetime.strptime(as_of, "%Y-%m-%d"),
                                        limit=10000, sort_order=1)
        for rec in bench:
            benchmark_closes[rec.date.strftime("%Y-%m-%d")] = float(rec.close_price)

    composed = compose_portfolio(
        sleeves=sleeve_meta, sleeve_runs=sleeve_runs, total_capital=total_capital,
        benchmark_closes=benchmark_closes,
        portfolio_meta={"id": p["id"], "name": p["name"], "point_in_time": pit},
        as_of=as_of)

    return JSONResponse(content=sanitize_for_json({"success": True, **composed}))
```
Note: `sanitize_for_json` already exists in `api_server.py` (used by the simulation route) — reuse it for safe numeric/datetime serialization.

- [ ] **Step 2: Verify the module imports**

Run: `.venv/Scripts/python.exe -c "import api_server; print('import ok')"`
Expected: `import ok`.

- [ ] **Step 3: Append the integration test** (`test_portfolio_api.py`)

```python
def test_portfolio_run_composes_curve_and_book():
    sid = _a_strategy_id()
    body = {"name": "Run Book", "inception_date": "2024-01-01", "total_capital": 1000000,
            "point_in_time_membership": True,
            "sleeves": [{"id": "s1", "label": "All", "strategy_id": sid, "weight": 100,
                         "universe": "NIFTY500"}]}
    pid = requests.post(f"{BASE}/api/portfolio", json=body, timeout=15).json()["portfolio_id"]
    try:
        r = requests.post(f"{BASE}/api/portfolio/{pid}/run",
                          params={"end_date": "2024-06-30"}, timeout=180)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["success"]
        assert len(data["curve"]) > 0
        assert {"portfolio_value", "benchmark_value", "date"} <= data["curve"][0].keys()
        assert {"total_return", "alpha", "sharpe_ratio"} <= data["summary"].keys()
        assert data["sleeves"][0]["weight"] == 100
    finally:
        requests.delete(f"{BASE}/api/portfolio/{pid}", timeout=10)
```

- [ ] **Step 4: Run the full integration suite against a live server**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_api.py -m integration -v`
Expected: PASS (3 tests). The run test needs NIFTY500 price + indicator + reconstitution data loaded (already present per data state).

- [ ] **Step 5: Run the default (unit) suite as a regression gate**

Run: `.venv/Scripts/python.exe -m pytest test_portfolio_composition.py -q`
Expected: PASS (all composition tests, zero failures). Confirms the pure layer is untouched.

- [ ] **Step 6: Commit**

```bash
git add api_server.py test_portfolio_api.py
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(api): composite portfolio run endpoint"
```

---

## Self-Review

**Spec coverage (design §4):**
- §4.1 `portfolios` collection + models → Task 1 (models), Tasks 7–8 persist the documented shape ✓
- §4.2 endpoints (POST/GET/GET{id}/PUT/DELETE + /run) → Task 7 (CRUD), Task 8 (/run) ✓
- §4.2 validation (≥1 sleeve, weights=100, strategy exists, inception≥2022) → Task 2, enforced in Tasks 7–8 ✓
- §4.3 composite algorithm: blended curve (T3), independent Nifty benchmark (T3), holdings union (T4), summary incl. maxDD/Sharpe (T5), attribution + rebalance (T6), endpoint wiring incl. PIT-loud universe resolution (T8) ✓
- §4.3 response shape → Task 6 returns `{portfolio,summary,curve,current_book,sleeves,rebalance_actions}`; Task 8 adds `success` ✓
- §4.4 tests: pure unit tests (T2–T6) + integration CRUD/run (T7–T8) ✓
- Honesty/PIT-loud → Task 8 propagates the 400, no fallback ✓

**Deferred (documented, not silent):** `rebalance_actions.next_due` is `None` in v1 (cadence date math deferred); `add`/`exit` come from the last rebalance entry. `target_weight` on adds is `None` in v1. These are explicit in Task 6's interface block.

**Placeholder scan:** No "TBD/TODO". Every code step shows complete code; every test step shows real assertions.

**Type consistency:** `compose_portfolio` keyword args (`sleeves`, `sleeve_runs`, `total_capital`, `benchmark_closes`, `portfolio_meta`, `as_of`) match the Task 8 call site exactly. `validate_portfolio(p, known_strategy_ids)` signature matches Task 7 calls. `blend_curve`/`benchmark_curve`/`union_holdings`/`summary_metrics` names match their imports inside `compose_portfolio`. Sleeve meta dict keys (`id`,`label`,`weight`) produced in Task 8 match what Task 6 reads. Daily-entry keys (`date`,`portfolio_value`,`holdings`,`new_added`,`exited`) match the verified `run_strategy_simulation` output (spec §verified contract).

## Execution prerequisites
- A live server on **port 3001** with Mongo + ≥1 saved strategy is required only for the `integration` tests (Tasks 7–8). The pure unit tests (Tasks 1–6) need neither.
- The `/run` integration test additionally needs NIFTY500 price/indicator/reconstitution data (present per current data state).

## Next plan
- **Plan 2 — Frontend:** `tvagent/frontend/` glass terminal (Portfolios home · dense Live Portfolio detail · builder) consuming these endpoints.
