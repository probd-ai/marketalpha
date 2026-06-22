# Momentum-Calc Optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the redundant per-call date-parsing/scan in the backtest momentum ranking (1.2M `strptime` calls ≈ half the run time) by precomputing parsed, per-symbol, date-sorted series once — producing **byte-for-byte identical** backtest results.

**Architecture:** Add two pure helpers that parse each date once and build per-symbol parallel arrays (`build_price_series`, `build_indicator_series`); add a pure `calculate_stock_momentum_fast` that reproduces the *exact* logic of the existing async `calculate_stock_momentum` via `bisect` slicing instead of re-scanning; wire them into `select_top_stocks_by_momentum` (backward-compatibly) and `run_strategy_simulation`. The existing `calculate_stock_momentum` stays as the reference oracle for a differential equivalence test.

**Tech Stack:** Python 3.13 · stdlib `bisect`/`datetime` · pytest (pure unit tests, no Mongo/server). All in `market-hunt/api_server.py`.

Spec: `docs/superpowers/specs/2026-06-22-momentum-calc-optimization-design.md`.

## Global Constraints

- **All work in the `market-hunt/` directory** (its own git repo, branch `walkforward-validation`). Run python/pytest/git from inside `market-hunt/`. Interpreter: `.venv/Scripts/python.exe`.
- **NO simulation math changes.** Outputs must match the golden master exactly: 2-sleeve NIFTY100 PIT max-7 first+mid 2022→today = **book 8 · sleeves +107.46% / +74.54% · summary total_return +91.0% · max_drawdown −24.49%**; single-sleeve first = **+107.46%**.
- **Keep the existing `calculate_stock_momentum` (async) in place** — the differential test uses it as the reference oracle. Do not delete or rename it.
- **`select_top_stocks_by_momentum` stays backward-compatible** (new optional kwargs) so `run_strategy_simulation_debug` (out of scope) keeps working unchanged.
- **Pure functions only in unit tests** — no Mongo, no server. The live golden-master + re-profile are run by the controller after Task 3.
- **No new dependencies** (`bisect`, `datetime`, `timedelta` are stdlib; `timedelta`/`datetime` already imported in api_server.py).
- **Commits** authored `Arpitha B`, empty email, no `Co-Authored-By`: `git -c commit.gpgsign=false commit --author="Arpitha B <>"`.

---

## File Structure

```
market-hunt/
  api_server.py            # MODIFY: add build_price_series, build_indicator_series,
                           #         calculate_stock_momentum_fast; wire into
                           #         select_top_stocks_by_momentum + run_strategy_simulation
  test_momentum_fast.py    # NEW: pure unit tests for the helpers + differential equivalence
```

---

### Task 1: Series-builder helpers (`build_price_series`, `build_indicator_series`)

**Files:**
- Modify: `market-hunt/api_server.py` (add two module-level functions just above `async def calculate_stock_momentum` at line ~3392)
- Test: `market-hunt/test_momentum_fast.py` (create)

**Interfaces:**
- Produces `build_price_series(price_data_history: dict) -> dict[str, tuple[list[datetime], list[float]]]` — maps `symbol → (dates_ascending, closes)` parallel arrays; each date string parsed exactly once.
- Produces `build_indicator_series(indicator_data_history: dict | None) -> dict[str, tuple[list[datetime], list[dict]]]` — maps `symbol → (dates_ascending, data_dicts)`; empty dict if input is falsy.

- [ ] **Step 1: Write the failing test** (`market-hunt/test_momentum_fast.py`)
```python
from datetime import datetime
from api_server import build_price_series, build_indicator_series


def _price_hist():
    # date_str -> {symbol -> {close_price,...}}; intentionally out of order
    return {
        "2024-01-03": {"AAA": {"close_price": 12.0}, "BBB": {"close_price": 50.0}},
        "2024-01-01": {"AAA": {"close_price": 10.0}},
        "2024-01-02": {"AAA": {"close_price": 11.0}, "BBB": {"close_price": 49.0}},
    }


def test_build_price_series_sorts_and_splits_by_symbol():
    s = build_price_series(_price_hist())
    aaa_dates, aaa_closes = s["AAA"]
    assert aaa_dates == [datetime(2024, 1, 1), datetime(2024, 1, 2), datetime(2024, 1, 3)]
    assert aaa_closes == [10.0, 11.0, 12.0]
    bbb_dates, bbb_closes = s["BBB"]            # BBB missing on 01-01
    assert bbb_dates == [datetime(2024, 1, 2), datetime(2024, 1, 3)]
    assert bbb_closes == [49.0, 50.0]


def test_build_indicator_series_empty_and_basic():
    assert build_indicator_series(None) == {}
    assert build_indicator_series({}) == {}
    ind = {"2024-01-02": {"AAA": {"truevx_score": 5}},
           "2024-01-01": {"AAA": {"truevx_score": 4}}}
    s = build_indicator_series(ind)
    dates, data = s["AAA"]
    assert dates == [datetime(2024, 1, 1), datetime(2024, 1, 2)]
    assert data == [{"truevx_score": 4}, {"truevx_score": 5}]
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `market-hunt/`): `.venv/Scripts/python.exe -m pytest test_momentum_fast.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_price_series'`.

- [ ] **Step 3: Add the helpers to `api_server.py`** (immediately above `async def calculate_stock_momentum`, ~line 3392)
```python
def build_price_series(price_data_history: dict) -> dict:
    """Precompute, once, per-symbol ascending (dates, closes) parallel arrays.

    Parses each date string exactly once (vs re-parsing per momentum call).
    Iterating sorted date keys yields each symbol's arrays already ascending.
    """
    series: dict = {}
    for date_str in sorted(price_data_history.keys()):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        for symbol, prices in price_data_history[date_str].items():
            entry = series.get(symbol)
            if entry is None:
                entry = ([], [])
                series[symbol] = entry
            entry[0].append(d)
            entry[1].append(prices["close_price"])
    return series


def build_indicator_series(indicator_data_history) -> dict:
    """Per-symbol ascending (dates, data_dicts) parallel arrays; {} if no data."""
    series: dict = {}
    if not indicator_data_history:
        return series
    for date_str in sorted(indicator_data_history.keys()):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        for symbol, data in indicator_data_history[date_str].items():
            entry = series.get(symbol)
            if entry is None:
                entry = ([], [])
                series[symbol] = entry
            entry[0].append(d)
            entry[1].append(data)
    return series
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest test_momentum_fast.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**
```bash
git add api_server.py test_momentum_fast.py
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "perf(sim): add per-symbol price/indicator series builders (parse dates once)"
```

---

### Task 2: `calculate_stock_momentum_fast` + differential equivalence test

**Files:**
- Modify: `market-hunt/api_server.py` (add `calculate_stock_momentum_fast` just below the helpers from Task 1)
- Test: `market-hunt/test_momentum_fast.py` (append)

**Interfaces:**
- Consumes: per-symbol series tuples from `build_price_series`/`build_indicator_series` (Task 1); the existing async `calculate_stock_momentum` (reference oracle, unchanged).
- Produces: `calculate_stock_momentum_fast(price_sym: tuple[list, list] | None, indicator_sym: tuple[list, list] | None, current_date_obj: datetime, method: str) -> float` — a pure, synchronous reproduction of `calculate_stock_momentum`'s value for one symbol, using `bisect` over the precomputed arrays. `price_sym`/`indicator_sym` are that symbol's `(dates, values)` tuples (or `None`/absent → treated as empty).

**Exact-logic requirements (must match `calculate_stock_momentum` at `api_server.py:3392-3604`):**
- `lookback_days`/`required_days`: `price_roc_66d`→90/66, `price_roc_222d`→300/222, **else**→30/20 (covers `20_day_return`, `risk_adjusted`, `technical`).
- `start_date = current_date_obj - timedelta(days=lookback_days)`.
- Price window = `closes[lo:hi]` where `lo = bisect_left(dates, start_date)`, `hi = bisect_right(dates, current_date_obj)` (inclusive both ends — matches `start_date <= d <= current`).
- `< 2` points → `0.0`. `current_price = window[-1]`. `20_day`: `window[-20]` if `len>=20` else `window[0]`; `66d`: `[-66]`/`[0]`; `222d`: `[-222]`/`[0]`; return `((cur/lb)-1)*100` if `lb>0` else `0.0`.
- `risk_adjusted` (`<5`→0): daily returns over window, mean/std, `(mean/std)*sqrt(252)*100` if std>0 else `mean*100`.
- `technical` (`<10`→0): `ma_10`/`ma_20` from `window[-10:]`/`window[-20:]`, `(price_momentum + trend_momentum)/2`.
- Indicator methods: `recs = data[:bisect_right(dates, current_date_obj)]`; `<=22`→0; `current = recs[-1]`, `past = recs[-23]`; `safe_get(d,k)=d.get(k) or 0 if None`; ROC `((cur-past)/past)*100` if `past>0` else 0; `stock_score_roc` uses weights `0.1/0.2/0.3/0.4` over `truevx_score/mean_short/mean_mid/mean_long`.
- Any exception → `0.0` (wrap in try/except like the original).

- [ ] **Step 1: Write the failing differential test** (append to `test_momentum_fast.py`)
```python
import asyncio
from datetime import datetime, timedelta
import api_server
from api_server import (build_price_series, build_indicator_series,
                        calculate_stock_momentum, calculate_stock_momentum_fast)


def _ramp_price_hist(start="2023-06-01", n=300):
    # AAA rises 100->..., BBB falls; daily calendar dates (weekends included is fine,
    # the function filters by calendar-day window identically for old and new).
    base = datetime.strptime(start, "%Y-%m-%d")
    hist = {}
    for i in range(n):
        ds = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        hist[ds] = {"AAA": {"close_price": 100.0 + i},
                    "BBB": {"close_price": 200.0 - 0.3 * i}}
    return hist


def _ind_hist(start="2023-06-01", n=60):
    base = datetime.strptime(start, "%Y-%m-%d")
    hist = {}
    for i in range(n):
        ds = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        hist[ds] = {"AAA": {"truevx_score": 50 + i, "mean_short": 40 + i,
                            "mean_mid": 30 + i, "mean_long": 20 + i}}
    return hist


def _old(symbol, current, price_hist, method, ind_hist=None):
    return asyncio.run(api_server.calculate_stock_momentum(
        symbol, current, price_hist, ind_hist, method))


def test_fast_matches_legacy_price_methods():
    price_hist = _ramp_price_hist()
    pseries = build_price_series(price_hist)
    current = "2024-01-15"
    cur_obj = datetime.strptime(current, "%Y-%m-%d")
    for method in ["20_day_return", "price_roc_66d", "price_roc_222d",
                   "risk_adjusted", "technical"]:
        for sym in ["AAA", "BBB"]:
            old = _old(sym, current, price_hist, method)
            new = calculate_stock_momentum_fast(pseries.get(sym), None, cur_obj, method)
            assert abs(old - new) < 1e-9, f"{method}/{sym}: old={old} new={new}"


def test_fast_matches_legacy_indicator_methods():
    price_hist = _ramp_price_hist(n=60)
    ind_hist = _ind_hist()
    iseries = build_indicator_series(ind_hist)
    current = "2023-07-30"
    cur_obj = datetime.strptime(current, "%Y-%m-%d")
    for method in ["truevx_roc", "short_mean_roc", "mid_mean_roc",
                   "long_mean_roc", "stock_score_roc"]:
        old = _old("AAA", current, price_hist, method, ind_hist)
        new = calculate_stock_momentum_fast(None, iseries.get("AAA"), cur_obj, method)
        assert abs(old - new) < 1e-9, f"{method}: old={old} new={new}"


def test_fast_edge_cases():
    cur = datetime(2024, 1, 15)
    assert calculate_stock_momentum_fast(None, None, cur, "20_day_return") == 0.0
    assert calculate_stock_momentum_fast(([cur], [10.0]), None, cur, "20_day_return") == 0.0  # <2
    # lookback price 0 -> 0.0
    dates = [cur - timedelta(days=2), cur - timedelta(days=1), cur]
    assert calculate_stock_momentum_fast((dates, [0.0, 5.0, 10.0]), None, cur, "20_day_return") == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest test_momentum_fast.py -k "fast" -v`
Expected: FAIL — `cannot import name 'calculate_stock_momentum_fast'`.

- [ ] **Step 3: Add `calculate_stock_momentum_fast` to `api_server.py`** (just below the Task 1 helpers; add `import bisect` at top of file if absent)
```python
def calculate_stock_momentum_fast(price_sym, indicator_sym, current_date_obj, method):
    """Pure, synchronous reproduction of calculate_stock_momentum for one symbol,
    using bisect over precomputed ascending series. Same numbers, no re-parsing."""
    try:
        if method == "price_roc_66d":
            lookback_days, _required = 90, 66
        elif method == "price_roc_222d":
            lookback_days, _required = 300, 222
        else:
            lookback_days, _required = 30, 20

        if method in ("truevx_roc", "short_mean_roc", "mid_mean_roc",
                      "long_mean_roc", "stock_score_roc"):
            if not indicator_sym:
                return 0.0
            i_dates, i_data = indicator_sym
            hi = bisect.bisect_right(i_dates, current_date_obj)
            recs = i_data[:hi]                       # ascending; date <= current
            if len(recs) <= 22:
                return 0.0
            current_indicators = recs[-1]            # most recent
            past_indicators = recs[-23]              # 22 records back

            def safe_get(data, key):
                val = data.get(key)
                return val if val is not None else 0

            if method == "truevx_roc":
                c, p = safe_get(current_indicators, "truevx_score"), safe_get(past_indicators, "truevx_score")
            elif method == "short_mean_roc":
                c, p = safe_get(current_indicators, "mean_short"), safe_get(past_indicators, "mean_short")
            elif method == "mid_mean_roc":
                c, p = safe_get(current_indicators, "mean_mid"), safe_get(past_indicators, "mean_mid")
            elif method == "long_mean_roc":
                c, p = safe_get(current_indicators, "mean_long"), safe_get(past_indicators, "mean_long")
            else:  # stock_score_roc
                c = (0.1 * safe_get(current_indicators, "truevx_score") + 0.2 * safe_get(current_indicators, "mean_short")
                     + 0.3 * safe_get(current_indicators, "mean_mid") + 0.4 * safe_get(current_indicators, "mean_long"))
                p = (0.1 * safe_get(past_indicators, "truevx_score") + 0.2 * safe_get(past_indicators, "mean_short")
                     + 0.3 * safe_get(past_indicators, "mean_mid") + 0.4 * safe_get(past_indicators, "mean_long"))
            return ((c - p) / p) * 100 if p > 0 else 0.0

        # ---- price-based methods ----
        if not price_sym:
            return 0.0
        dates, closes = price_sym
        start_date = current_date_obj - timedelta(days=lookback_days)
        lo = bisect.bisect_left(dates, start_date)
        hi = bisect.bisect_right(dates, current_date_obj)
        window = closes[lo:hi]                       # ascending closes in [start, current]
        if len(window) < 2:
            return 0.0
        current_price = window[-1]

        if method == "20_day_return":
            lb = window[-20] if len(window) >= 20 else window[0]
            return ((current_price / lb) - 1) * 100 if lb > 0 else 0.0
        if method == "price_roc_66d":
            lb = window[-66] if len(window) >= 66 else window[0]
            return ((current_price / lb) - 1) * 100 if lb > 0 else 0.0
        if method == "price_roc_222d":
            lb = window[-222] if len(window) >= 222 else window[0]
            return ((current_price / lb) - 1) * 100 if lb > 0 else 0.0
        if method == "risk_adjusted":
            if len(window) < 5:
                return 0.0
            daily = [(window[i] / window[i - 1]) - 1 for i in range(1, len(window)) if window[i - 1] > 0]
            if not daily:
                return 0.0
            mean_return = sum(daily) / len(daily)
            variance = sum((r - mean_return) ** 2 for r in daily) / len(daily)
            std_return = variance ** 0.5
            if std_return > 0:
                return (mean_return / std_return) * (252 ** 0.5) * 100
            return mean_return * 100
        if method == "technical":
            if len(window) < 10:
                return 0.0
            p10 = window[-10:] if len(window) >= 10 else window
            p20 = window[-20:] if len(window) >= 20 else window
            ma_10 = sum(p10) / len(p10)
            ma_20 = sum(p20) / len(p20)
            price_m = ((current_price / ma_20) - 1) * 100 if ma_20 > 0 else 0
            trend_m = ((ma_10 / ma_20) - 1) * 100 if ma_20 > 0 else 0
            return (price_m + trend_m) / 2
        return 0.0
    except Exception:
        return 0.0
```
Note: the legacy `risk_adjusted` builds daily returns guarding `prev_price > 0`; this reproduces that with the `if window[i-1] > 0` filter. The legacy `20_day_return` filters the window by `start_date` (30-day calendar window) then indexes — identical here.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest test_momentum_fast.py -v`
Expected: PASS (all 5 tests — Task 1's 2 + Task 2's 3). The differential tests prove `calculate_stock_momentum_fast` equals the legacy function across every method.

- [ ] **Step 5: Commit**
```bash
git add api_server.py test_momentum_fast.py
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "perf(sim): calculate_stock_momentum_fast + differential equivalence test vs legacy"
```

---

### Task 3: Wire fast path into selection + simulation; golden-master verify

**Files:**
- Modify: `market-hunt/api_server.py` — `select_top_stocks_by_momentum` (~3606) add optional series kwargs + fast path; `run_strategy_simulation` (~4136) build series once + pass at the rebalance call (~4389)

**Interfaces:**
- Consumes: `build_price_series`, `build_indicator_series`, `calculate_stock_momentum_fast` (Tasks 1-2).
- Produces: `select_top_stocks_by_momentum(..., *, price_series=None, indicator_series=None, current_date_obj=None)` — when `price_series` is provided, computes momentum via `calculate_stock_momentum_fast` (sync); otherwise falls back to the existing `await calculate_stock_momentum` (so `run_strategy_simulation_debug` is unaffected).

- [ ] **Step 1: Add optional fast path to `select_top_stocks_by_momentum`** (`api_server.py:3606`)

Change the signature to add keyword-only params:
```python
async def select_top_stocks_by_momentum(qualified_stocks: list, current_holdings: dict,
                                price_data_history: dict, indicator_data_history: dict,
                                current_date: str, max_holdings: int,
                                momentum_method: str = "20_day_return",
                                *, price_series=None, indicator_series=None,
                                current_date_obj=None) -> tuple:
```
Add a local momentum helper right after the early `total_candidates <= max_holdings` return, and replace BOTH `await calculate_stock_momentum(symbol, current_date, price_data_history, indicator_data_history, momentum_method)` calls (the holdings loop ~3628 and the qualified loop ~3643) with `await _momentum(symbol)`:
```python
        _cdo = current_date_obj or datetime.strptime(current_date, "%Y-%m-%d")

        async def _momentum(symbol):
            if price_series is not None:
                return calculate_stock_momentum_fast(
                    price_series.get(symbol), 
                    (indicator_series or {}).get(symbol), _cdo, momentum_method)
            return await calculate_stock_momentum(
                symbol, current_date, price_data_history, indicator_data_history, momentum_method)
```
So `momentum = await _momentum(symbol)` in both loops. Everything else in the function (candidate assembly, sort desc, `[:max_holdings]`, added/removed, logging) is unchanged.

- [ ] **Step 2: Build series once + pass them in `run_strategy_simulation`**

After `price_data` is fully populated (right after the concurrent price-load block, ~line 4215) and after `indicator_data` is built, add:
```python
        price_series = build_price_series(price_data)
        indicator_series = build_indicator_series(indicator_data)
```
Then at the rebalance selection call (`api_server.py:~4389`), pass the precomputed series + parsed date. Change:
```python
                selected_stocks, momentum_added, momentum_removed = await select_top_stocks_by_momentum(
                    qualified_stocks, current_holdings, price_data, indicator_data,
                    date_str, params.max_holdings, params.momentum_ranking)
```
to:
```python
                selected_stocks, momentum_added, momentum_removed = await select_top_stocks_by_momentum(
                    qualified_stocks, current_holdings, price_data, indicator_data,
                    date_str, params.max_holdings, params.momentum_ranking,
                    price_series=price_series, indicator_series=indicator_series,
                    current_date_obj=datetime.strptime(date_str, "%Y-%m-%d"))
```
(Use the exact variable names present — `indicator_data` is the dict built from the indicator query; confirm its name by reading the function. If the indicator dict has a different local name, use that.)

- [ ] **Step 3: Verify the module imports**

Run: `.venv/Scripts/python.exe -c "import api_server; print('import ok')"`
Expected: `import ok`.

- [ ] **Step 4: Run the pure unit suite (regression)**

Run: `.venv/Scripts/python.exe -m pytest test_momentum_fast.py -q`
Expected: PASS (5 tests) — the helpers + equivalence still hold.

- [ ] **Step 5: Commit**
```bash
git add api_server.py
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "perf(sim): use precomputed momentum series in run_strategy_simulation (kills 1.2M strptime)"
```

- [ ] **Step 6: CONTROLLER — live golden-master + re-profile** (not run by the implementer subagent)

The controller restarts the backend with the new code on a fresh port and verifies, against live Mongo:
1. **Oracle:** 2-sleeve NIFTY100 PIT max-7 first+mid, capital ₹10L, `start_date=2022-01-01`→today → assert **book == 8**, sleeve returns **+107.46 / +74.54**, summary **total_return == 91.0**, **max_drawdown == -24.49** (to existing rounding). Exact match required.
2. **Single-sleeve** NIFTY100 PIT max-7 first 2022→today → **+107.46**.
3. **Indicator method:** one run with `momentum_ranking="truevx_roc"` (small window) — compare summary before vs after (capture the pre-change value first by checking out the prior commit, or accept the post-change value as a smoke if a baseline isn't captured) — must be self-consistent and finite.
4. **Re-profile** (rerun the `profile_sim.py` harness): assert `datetime.strptime` call count dropped from ~1.2M to ~thousands and wall time materially lower; record the new single-sleeve time.
5. Full portfolio integration suite green: `MH_API_BASE=http://localhost:<port> .venv/Scripts/python.exe -m pytest test_portfolio_api.py -m integration -q`.

If any golden-master number differs, treat as a bug: dispatch a fix and re-verify before marking complete.

---

## Self-Review

**Spec coverage (design §3-4):**
- §3.1 `build_price_series` + indicator variant → Task 1 ✓
- §3.2 `calculate_stock_momentum_fast` exact-logic (all price + indicator methods, window via bisect) → Task 2 ✓
- §3.3 `select_top_stocks_by_momentum` consumes series, backward-compatible → Task 3 Step 1 ✓
- §3.4 wiring in `run_strategy_simulation` (build once, pass parsed date) → Task 3 Step 2 ✓
- §4 correctness gate: differential unit test (Task 2) + live golden-master oracle/single/indicator + re-profile (Task 3 Step 6) ✓
- §2 out-of-scope respected: debug path untouched (optional kwargs), no valuation-loop change ✓

**Placeholder scan:** No "TBD/TODO". Every code step shows complete code; the one runtime check ("confirm `indicator_data`'s local name by reading the function") is a concrete instruction, not a placeholder — the dict is built by the indicator query in `run_strategy_simulation` and the implementer must match its actual name.

**Type consistency:** `build_price_series`/`build_indicator_series` return `dict[symbol → (dates, values)]`; `calculate_stock_momentum_fast(price_sym, indicator_sym, current_date_obj, method)` consumes those exact tuples; `select_top_stocks_by_momentum` passes `price_series.get(symbol)` / `(indicator_series or {}).get(symbol)` (the per-symbol tuple or None) + `current_date_obj` — matching the fast fn's params. The legacy `calculate_stock_momentum` signature is untouched (still referenced by the diff test and the fallback path).

## Execution prerequisites
- Tasks 1-2 (pure unit tests) need no Mongo/server. Task 3 Step 6 (golden-master + profile + integration) needs live Mongo + loaded NIFTY100 data on a fresh server port (controller-run).
