# Momentum-Calc Optimization — Design Spec

> Targeted performance fix to the market-hunt backtest engine's momentum ranking. Removes redundant
> date-parsing/scanning that profiling proved is ~half the run time, **without changing any simulation
> math** — gated by an exact golden-master/oracle match. NOT a vectorization rewrite (profiling showed
> the valuation loop is not the bottleneck).

**Date:** 2026-06-22 · **Status:** approved design, pre-plan · **Repo:** market-hunt (`api_server.py`)

---

## 1 · Goal & evidence

Make a portfolio run materially faster while producing **byte-for-byte identical results**.

**Profiling** (single sleeve, NIFTY100 PIT 2022→today, 145 ever-members, 1,106 trading days, `cProfile`):

| Hot spot | Calls | Cost | Cause |
|---|---|---|---|
| `datetime.strptime` | **1,221,021** | **~18.8s of 35s** | `calculate_stock_momentum` re-parses *every* date key in the full price history on *every* call |
| price load `get_price_data` | ~146 (concurrent) | ~3.5s | legitimate one-time load (already concurrent) — **not a target** |
| valuation loop | — | minor | NOT the bottleneck — vectorization would miss the real cost |

`calculate_stock_momentum` (`api_server.py:3392`) is invoked **per candidate × per rebalance** by
`select_top_stocks_by_momentum` (`api_server.py:3606`). Each call scans the entire `price_data_history`
dict and `strptime`-parses every date string → O(rebalances × candidates × all_dates) redundant parses.

## 2 · Scope

**In:** eliminate the redundant per-call date-parsing and full-history scan in the momentum ranking by
precomputing parsed, per-symbol, date-sorted series **once per sleeve run**; refactor
`calculate_stock_momentum` + `select_top_stocks_by_momentum` to consume it; wire it into
`run_strategy_simulation`. Preserve **exact** selection logic and outputs.

**Out:** vectorizing the day-by-day valuation loop; changing any strategy/ranking/allocation/charge math;
the price-load I/O (already concurrent); `run_strategy_simulation_debug` (separate, not the hot path —
may receive the same fix later, not now). If re-profiling after this still shows a large loop cost, that
is a *separate* future decision.

---

## 3 · Design

### 3.1 New helper — `build_price_series`
```
build_price_series(price_data_history: dict[str, dict]) -> dict[str, list[tuple[date, float]]]
```
One pass over the loaded `price_data_history` (keyed `date_str → {symbol → {close_price, …}}`). Each
date string is parsed **once**. Produces, per symbol, a list of `(date_obj, close_price)` **sorted
ascending by date**. A symbol's list contains only dates on which that symbol has a price — identical to
the set the current code builds via `symbol in prices`.

Built **once** in `run_strategy_simulation` immediately after `price_data` is populated, before the
rebalance loop. Cost: ~1,100 date parses + one dict traversal (vs 1.2M parses today).

### 3.2 Refactor `calculate_stock_momentum`
New signature (price-based methods):
```
calculate_stock_momentum_fast(series: list[tuple[date, float]], current_date_obj: date,
                              method: str) -> float
```
Logic **preserved exactly**:
- `lookback_days` / `required_days` per method unchanged (`20_day_return`→30/20, `price_roc_66d`→90/66,
  `price_roc_222d`→300/222, `risk_adjusted`, …).
- `start_date = current_date_obj - timedelta(days=lookback_days)`.
- Window = the slice of `series` with `start_date <= date <= current_date_obj`, obtained by **bisect**
  on the pre-sorted series (no scan, no parse). This yields the **same** ascending `symbol_prices` the
  current code produces.
- `if len(window) < 2: return 0.0`. `current_price = window[-1].close`. Lookback index unchanged:
  `window[-20]` (or `[-66]`/`[-222]`) when long enough else `window[0]`; return `((cur/lb)-1)*100` if
  `lb > 0` else `0.0`. `risk_adjusted` and any other branches keep their exact arithmetic over `window`.
- Indicator-based methods (`truevx_roc`, `short_mean_roc`, …) that today scan `indicator_data_history`
  get the **same treatment**: precompute `build_indicator_series` once and slice it. (If an indicator
  method's current logic differs, replicate it exactly over the precomputed series.)

`current_date_obj` is passed already-parsed (parsed once per rebalance by the caller), removing the
per-call `strptime(current_date)` too.

### 3.3 `select_top_stocks_by_momentum`
Signature gains the precomputed `price_series` (and `indicator_series` if needed); replaces
`price_data_history`/`indicator_data_history` for the momentum calls. It passes `price_series[symbol]`
(or `[]` if absent) and the parsed `current_date_obj` to `calculate_stock_momentum_fast`. All other
behavior (candidate assembly, sort by `momentum_score` desc, `[:max_holdings]`, added/removed
computation, logging) is unchanged.

### 3.4 Wiring in `run_strategy_simulation`
After the concurrent price load builds `price_data` (and after indicator load), build the series once:
`price_series = build_price_series(price_data)` (+ indicator series if used). At each rebalance
(`api_server.py:4389`), pass the precomputed series + the already-parsed rebalance date to
`select_top_stocks_by_momentum`. Parse each rebalance date once (not inside the momentum calc).

### 3.5 Units & boundaries
- `build_price_series` / `build_indicator_series`: pure functions (dict → dict of sorted lists),
  independently unit-testable.
- `calculate_stock_momentum_fast`: pure function (series + date + method → float), independently
  unit-testable against hand-computed expected values.
- `select_top_stocks_by_momentum`: orchestration, unchanged except input source.

---

## 4 · Correctness gate (the spine)

**Golden-master / characterization, captured BEFORE the change:**
1. **Oracle (price-based, default method):** 2-sleeve NIFTY100 PIT, max-7, rebalance first+mid, capital
   ₹10L, `start_date=2022-01-01`→today. Current truth: **book = 8**, sleeves **+107.46% / +74.54%**,
   summary **total_return +91.0% · max_drawdown −24.49%**. The refactor must reproduce these exactly
   (to the existing rounding).
2. **Single-sleeve** NIFTY100 PIT max-7 first, 2022→today: **+107.46%**.
3. **Indicator-method path:** one run using an indicator-based `momentum_ranking` (e.g. `truevx_roc`) on
   a small fixed universe/window — capture its current summary, assert unchanged after the refactor
   (covers the indicator-series branch).

**Unit tests (pure functions):**
- `build_price_series`: given a small `price_data_history`, returns per-symbol ascending `(date, close)`
  lists containing only that symbol's dates.
- `calculate_stock_momentum_fast`: hand-computed `20_day_return`, `price_roc_66d`, `<2 points → 0.0`,
  `lookback_price == 0 → 0.0`, window-boundary (date exactly at `start_date`) — assert it equals the
  **old** `calculate_stock_momentum` output for the same inputs (differential test old-vs-new on a
  fixture).

Any deviation from the golden master = a bug, fix before proceeding. **Re-profile** after green to
confirm the win and record the new single-sleeve time.

## 5 · Risk & rollback
Low: no math changes; only date-parsing and history-scanning are hoisted/precomputed. The differential
unit test (old fn vs new fn) plus the golden-master integration runs catch any drift. Rollback = revert
the commit(s); the engine's external contract (inputs/outputs of `run_strategy_simulation`) is unchanged.

## 6 · Definition of done
`build_price_series` (+ indicator variant if needed) and `calculate_stock_momentum_fast` implemented and
unit-tested; `select_top_stocks_by_momentum` + `run_strategy_simulation` wired to use them;
golden-master (oracle + single-sleeve + indicator-method) reproduced **exactly**; re-profile shows the
1.2M `strptime` calls gone and a materially lower wall time; full portfolio integration suite still green.
