# Walk-Forward Validation of the RS-Momentum Strategy — Design

**Date:** 2026-06-20
**Repo:** market-hunt (prototype/stopgap)
**Goal:** Decide whether the RS-Momentum edge measured in-sample (the portfolio-size sweep:
~28% CAGR, Sharpe ~1.1 on point-in-time NIFTY500, 2020–2026) is **robust out-of-sample**, or a
curve-fit. Concretely: does *adaptively choosing* `(max_holdings, ranking_window)` on past data beat
*fixing* one config — and does either beat the Nifty 50 benchmark — on data never seen at selection time.

## Context

The sweep (`scripts/sweep_portfolio_size.py`) showed a flat CAGR plateau (N≈10–30, ~28%) with the
best risk-adjusted point at N≈25–30 (Sharpe ~1.16) — but it was **in-sample**: every N was scored on
the same 2020–2026 window it was chosen from. The spiky N=15 dip is a noise warning. Walk-forward is
the honest test: tune on a rolling *training* window, measure on the *next* window, never reuse the
test window for selection.

The strategy under test is the existing market-hunt simulator (TrueVX-qualified universe ranked by a
momentum method), driven point-in-time correct (`point_in_time_membership=True`, the survivorship-safe
path already built). We validate **exactly that** strategy — not a reimplementation.

## Decisions (locked with user)

- **Engine:** market-hunt `run_strategy_simulation`, driven **in-process** (import + call directly),
  not over HTTP. Avoids the reload/orphaned-socket issues and is far faster across many runs.
- **Tuned parameters (2-D grid):**
  - `max_holdings ∈ {10, 15, 20, 25, 30}`
  - `momentum_ranking ∈ {"20_day_return", "price_roc_66d", "price_roc_222d"}` (≈1m / 3m / 12m windows)
  - = **15 configs** per training fold. All other params fixed at the sweep's settings: monthly
    rebalance, `rebalance_date="first"`, `rebalance_type="equal_weight"`, `point_in_time_membership=True`,
    `include_brokerage=True`, universe `NIFTY500`, benchmark `Nifty 50`, base value 100000.
- **Scheme:** **rolling** (sliding) walk-forward — stricter than expanding because each test fold is a
  fresh regime. 3-year train, 1-year test, step 1 year:

  | Fold | Train (select) | Test (OOS) |
  |------|----------------|------------|
  | 1 | 2018-01-01 .. 2020-12-31 | 2021 |
  | 2 | 2019-01-01 .. 2021-12-31 | 2022 |
  | 3 | 2020-01-01 .. 2022-12-31 | 2023 |
  | 4 | 2021-01-01 .. 2023-12-31 | 2024 |
  | 5 | 2022-01-01 .. 2024-12-31 | 2025 |
  | 6 | 2023-01-01 .. 2025-12-31 | 2026-01-01 .. 2026-06-30 |

  TrueVX scores are valid from 2018-01-01, so the earliest train window starts there.
- **Selection objective on train:** **Sharpe ratio** (the defensible knee). CAGR reported alongside.
- **Pass/fail criterion (what robustness means here):**
  1. **Adaptive vs fixed.** Stitch the 6 OOS test windows into one continuous equity curve for the
     walk-forward adaptive picks (curve A), a never-retuned fixed baseline `N=20, price_roc_66d`
     (curve B), and Nifty 50 (curve C). The edge is *real and the optimizer earns its keep* only if
     **A ≳ B and B > C** out-of-sample. If A ≤ B, the plateau is flat and the honest recommendation is
     "fix N, don't optimize." If B ≤ C, the strategy has no OOS edge over the benchmark.
  2. **Stability.** Report the selected `(N, window)` per fold. If selection thrashes (different
     window/N nearly every fold), flag it as an overfitting signal — no stable optimum exists.
  3. **Degradation.** Report per-fold in-sample (train-best) vs OOS metrics. A large train→test drop
     is the multiple-testing inflation made visible.
- The OOS curve is **unbiased by construction** (the test window is never used to choose the config),
  so the 2-D grid's multiple-testing inflation is absorbed into the train→OOS degradation, not the
  headline OOS number.

## Architecture (small, testable units)

New module `market-hunt/walkforward/`:

- **`sim_runner.py`** — thin in-process adapter. Builds a `SimulationParams` (reused from
  `api_server`) for a given `(start, end, max_holdings, momentum_ranking)`, looks up the strategy doc
  once, and calls `run_strategy_simulation`. Loads membership + `IndicatorDataManager` **once** and
  reuses them across all runs (the expensive setup is not repeated per grid point). Returns a compact
  `RunResult`: `{cagr, sharpe, max_drawdown, alpha, total_return, equity: list[(date, value)]}`,
  derived from `result["summary"]` and `result["results"][].portfolio_value`.
- **`folds.py`** — `make_folds(train_years, test_years, step_years, data_start, data_end)` → list of
  `Fold(train_start, train_end, test_start, test_end)`. **Leakage guard:** asserts
  `test_start > train_end` for every fold; raises if not.
- **`walk_forward.py`** — orchestration: for each fold, run the 15-config grid on the train window via
  `sim_runner`, pick the max-Sharpe config, run that one config on the test window, collect
  `FoldResult(fold, best_config, train_metrics, oos_metrics, oos_equity)`. Also runs the fixed baseline
  and the benchmark on each test window. Grid runs within a fold are independent → may use a process
  pool, but a serial loop is acceptable for a one-time run.
- **`report.py`** — formats the per-fold table (best config, train vs OOS Sharpe/CAGR/maxDD/alpha),
  stitches the OOS windows into curves A/B/C, prints the verdict against the pass/fail criterion, and
  writes an optional matplotlib equity-curve PNG (A vs B vs C) to scratchpad.
- **`run_walkforward.py`** — CLI entrypoint wiring the above with the locked defaults; flags to
  override grid, window lengths, and objective for experimentation.

### Data flow

`make_folds` → for each fold: `sim_runner.run(train window, each grid config)` → select max Sharpe →
`sim_runner.run(test window, selected config)` + baseline + benchmark → `FoldResult` → `report`
stitches OOS → verdict.

## Error handling

- A sim run that returns too few rebalances (e.g., a fold with sparse data) is recorded as a failed
  config and excluded from selection; if an entire train fold has zero valid configs, the fold is
  reported as skipped, not silently dropped.
- PIT membership unavailable → `load_membership` raises `ValueError`; surface it loudly (same contract
  as the API's 400), never fall back to the biased universe.
- **Warmup correctness:** `price_roc_222d` and TrueVX need ~222 trading days of history *before* a
  fold's first rebalance. The simulator already fetches price history from 2016 regardless of
  `start_date`, so signals on the first rebalance of each window are computed from real prior data, not
  truncated. This is asserted in tests (see below), not assumed.

## Verification (evidence before "done")

TDD unit tests in `walkforward/tests/`:
- **`test_folds.py`** — fold count and exact dates for the locked scheme; the leakage guard raises when
  `test_start <= train_end`; rolling step advances by exactly `step_years`.
- **`test_walk_forward.py`** — with a stub `sim_runner` returning known metrics, selection picks the
  max-Sharpe config; OOS stitching concatenates test windows in date order without gaps/overlaps; a
  failed config is excluded from selection.
- **`test_sim_runner.py`** — `RunResult` is built correctly from a sample `run_strategy_simulation`
  output (summary keys + equity from `results[].portfolio_value`); a window whose first rebalance is at
  a fold boundary still yields valid (non-NaN) first-rebalance signals (warmup assertion).

Integration evidence (the actual answer, run once after units pass):
- Full 6-fold walk-forward on PIT NIFTY500; the per-fold table, the A/B/C OOS curves, and the verdict.
- Sanity: fold-3 train window (2020–2022) best config should resemble the in-sample sweep's pick on
  the overlapping span (consistency check, not a pass condition).

## Out of scope

- Optimizing rebalance frequency, weighting scheme, or the TrueVX qualification threshold (fixed here).
- Tuning more than the two named axes (YAGNI; expanding the grid only inflates overfitting).
- Porting this to the tvagent clean engine (a later step once the stopgap result is known).
- Transaction-cost sensitivity beyond the simulator's existing brokerage model.
