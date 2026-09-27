# Book engine — Phase 1 implementation plan

**Spec:** `docs/specs/2026-09-27-fund-trading-desk-design.md` (§3, §4, §5.0)

**Goal.** A pure-Python book engine in `market-hunt/fund/engine/` that reproduces the research harness exactly.
It implements the Grid-10, weekly-tranche and asset sleeves, plus the fund layer, with replay and checkpoints.

**Not in this phase:** store, service, API (Phase 2) and the frontend (Phase 3).

## How we work
- **Test first.** Write the failing test, watch it fail, implement, watch it pass. **Commit only when the user asks**;
  then one commit per task, author "Arpitha B", no Co-Authored-By trailer.
- **Oracle.** The harness in `scripts/research_grid/` is frozen:
  - `rotation_roc.run` with Grid-10's `pick_fn`;
  - `weekly_tranche.run_weekly`;
  - `fund_report_data.blend`.
  Do not edit these files. The differential tests compare the engine with the oracle on **identical inputs loaded
  in the same process**, so later data refreshes cannot break the golden master.
- **Test markers.**
  - Unit tests are pure and use synthetic frames; they run by default.
  - Differential tests need Mongo and are marked `integration`: `pytest -m integration tests/fund_desk`.
- **Engine rules.** No Mongo, clock, network or pandas-global state inside `fund/engine/`. Pandas and numpy are fine.

## Harness behaviours the engine must reproduce (write them into the code as comments)

**H1. 22-day return.** `close / close.dropna().shift(22)`, shifted over the stock's own valid bars and reindexed.

**H2. Grid state.**
- On mean_long's own non-NaN dates: `sign(diff(floor(ml / 5)))`.
- Zeros become NaN, then forward-fill.
- Reindex to the price calendar and forward-fill again.

**H3. Calendar.**
- Trading days are the bench (Nifty 50) days with a close, from `start`.
- The weekly schedule is the k-th session of each ISO week (the last session if the week is shorter).
- The monthly schedule is the first session of the month, plus k sessions.
- Grid-10 also rebalances on day index 0.

**H4. Signal day.** `prev` = the previous calendar session. On day 0 it is the last price date before `start`.

**H5. Grid-10 pool.** Members with a non-NaN 22-day return at `prev` **and** a non-NaN open on the fill day.
- Sells consider only holdings that are in the pool. A holding without an open that day is kept.

**H6. Grid-10 fills** (`rotation_roc.run`):
- Sell proceeds `q·px·(1−slip)·(1−sell_rate)`.
- A buy of `v` spends `v`, and `q = v / (px·(1+slip)·(1+buy_rate))`.
- Equity at the open values held names without an open at the last close.
- Gap fill: `target = equity / len(pick)`. Gaps are only for names with an open.
  - If the total gap exceeds cash, allocate pro-rata.
  - Any remainder is spread equally over the tradable picks.

**H7. Index exits.**
- After each day's valuation, holdings not in **tomorrow's** membership are queued.
- The queue is sold at the next day's open. If there is no open, the stock is sold at its last close once the last
  bar is before that day; otherwise retry the next day.

**H8. Tranche engine** (`run_weekly`):
- Exits on membership (same day, as in the code) happen before the rebalance.
- Order on each rebalance: cap trims (at the open), then the plan (expiry versus new tranche per symbol), then net
  sells, then buys scaled by `min(1, cash / need)`, then cash deployment in rank order to the limit.
- Deployment stops once cash ≤ 0.5% of equity.
- Then every tranche ages by one.
- A halted expiring tranche is re-queued with age = weeks.

**H9. Valuation.** End-of-day equity = cash + Σ qty·close, using forward-filled closes.

## Tasks

### Task 1 — Package skeleton and types
**Files:**
- `fund/__init__.py`
- `fund/engine/__init__.py`
- `fund/engine/types.py`
- `tests/fund_desk/__init__.py`
- `tests/fund_desk/test_types.py`

**Build:**
- Frozen dataclasses `Lot(symbol, qty, opened: date, age: int)`, `Order(symbol, side, qty, px, value, reason, lot_age)`.
- `Reason` as a `str` Enum with the codes in spec §6.
- `SleeveState(cash, lots: dict[str, list[Lot]], params: dict)` with `qty(symbol)`, `value(prices)`,
  `to_dict()` / `from_dict()`.

**Test:**
- Round-trip `from_dict(to_dict(s)) == s`.
- `qty` sums a symbol's lots.
- `value` uses the given prices.

### Task 2 — Market data frame contract and derived signals
**Files:**
- `fund/engine/signals.py`
- `tests/fund_desk/test_signals.py`

**Build (pure functions on DataFrames):**
- `roc(closes, n=22)` (H1).
- `grid_state(mean_long, step=5)` (H2).
- `alpha_eligible(fields, rules)` with rules `{truevx_score: 90, mean_short: 80, mean_mid: 60, mean_long: 50}`,
  strict `>`, NaN → False.

**Test (synthetic):**
- A series with a gap: the shift skips NaN bars.
- A grid series 48 → 52 → 51 → 47 → 49 gives states +1, +1, −1, −1.
- Alpha eligibility on NaN is False.

**Differential test (integration):** equals `grid_meanrev`/`value_grid_filter` outputs on NIFTY50 2020–26 exactly.

### Task 3 — Calendar
**Files:**
- `fund/engine/calendar.py`
- `tests/fund_desk/test_calendar.py`

**Build:**
- `trading_days(bench_closes, start, end)`.
- `weekly_days(cal, k)`.
- `monthly_days(cal, k)`.
- `first_week_of_month(cal)` for the reset.
- `prev_session(cal, all_price_dates, i)` (H3, H4).

**Test:**
- A 4-session ISO week with k = 4 → the last session.
- A month starting on a holiday.
- Day 0's previous session comes from the price dates before start.

### Task 4 — Costs
**Files:**
- `fund/engine/costs.py`
- `tests/fund_desk/test_costs.py`

**Build:** `Costs(buy_rate, sell_rate, slip)` with:
- `sell_proceeds(q, px)`;
- `buy_qty(value, px)` (H6);
- `from_brokerage_calculator()`, a factory used only outside the engine that calls `grid_meanrev.charge_rates`'
  logic.

**Test:** the formulas equal H6 to 1e-12.

### Task 5 — Grid-10 sleeve
**Files:**
- `fund/engine/grid10.py`
- `tests/fund_desk/test_grid10.py`

**Build:** `Grid10(n=10, lookback=22)` with
`step(state, day, prev, members, opens, prev_close, roc_prev, grid_prev) -> (state, orders)`,
implementing H5 and H6. Reasons:
- `GRID_DOWN_POSITIVE_MONTH`;
- `NEW_LOWEST_22D`;
- `TOPUP_TO_TARGET`;
- `REMAINDER_SPREAD`.

It also provides `apply_index_exits(state, day, opens, last_close_before)` (H7) with reason `INDEX_EXIT`.

**Unit tests (synthetic, 12 symbols):**
- (a) The sell fires only when grid is −1 and the return is > 0.
- (b) A down-state holding with a negative return is kept.
- (c) Buys are the lowest-return up-state names.
- (d) Sale money short → pro-rata gaps.
- (e) A surplus is spread equally, including over holdings already above target.
- (f) Winners are never trimmed.
- (g) A holding with no open is kept and valued at the last close.
- (h) Fewer than N eligible names → a smaller book.

### Task 6 — Replay driver for a single sleeve
**Files:**
- `fund/engine/replay.py`
- `tests/fund_desk/test_replay.py`

**Build:** `replay_sleeve(sleeve, data, start, end, schedule, capital, costs) -> (equity: Series, state, orders_by_day)`.
- It runs the day loop (H3, H7, H9) and calls `sleeve.step` on schedule days.
- It supports checkpoints: `replay_sleeve(..., from_state=(day, state))` continues from a saved day.

**Tests:**
- Determinism: two runs give identical results.
- Checkpoint equivalence: replay to day X, save, continue to the end; this equals a full replay, byte-identical.

### Task 7 — Grid-10 golden master (integration)
**File:** `tests/fund_desk/test_golden_grid10.py`

**Setup:** load NIFTY50 data once, the same way `grid10.py` does. Build the oracle `pick` exactly as `grid10.py`.

**Assert:** for k in 0..4, the engine equity curve for `weekly+k` equals `rotation_roc.run(...)` to 1e-9 relative on
every day. The final value also matches `grid10_weekly.csv` column k (tolerance 1e-9).

Also assert that the order log matches the oracle `log` (symbol, side, qty to 1e-9).

### Task 8 — Weekly-tranche sleeve
**Files:**
- `fund/engine/tranche.py`
- `tests/fund_desk/test_tranche.py`

**Build:** `Tranche(top=5, weeks=8, slot_div=40, max_w=0.10, deploy_cash=True)` implementing H8 with lots as tranches.
Reasons:
- `TOP5_NEW_TRANCHE`;
- `TRANCHE_ROLL`;
- `TRANCHE_EXPIRED`;
- `TRIM_ABOVE_CAP`;
- `CASH_DEPLOY`;
- `INDEX_EXIT`.

**Unit tests:**
- A pick already at the cap buys nothing.
- A roll nets to no trade.
- Expiry sells.
- A trim shrinks every lot pro-rata.
- Deployment respects the cap and the rank order.
- A halted expiring tranche waits.
- Buys are scaled when cash is short.

### Task 9 — Tranche golden master (integration)
**File:** `tests/fund_desk/test_golden_tranche.py`

**Assert:** for NIFTY200 and NIFTY500MICRO750, k in 0..4, the engine equals `run_weekly(..., weeks=8, slot_div=40,
max_w=0.10, deploy_cash=True)` to 1e-9. It also matches `n200_8wcap10_deploy.csv` and
`n750_cap_weekly8w_cap10deploy.csv`.

This test is slow (loads N750 data), so mark it `integration` + `slow`.

### Task 10 — Asset sleeve and fund layer
**Files:**
- `fund/engine/asset.py`
- `fund/engine/fund.py`
- `tests/fund_desk/test_fund.py`

**Build:**
- `Asset(symbol)`.
- `Fund(sleeves: dict[name, (sleeve, weight)], reset_cost=0.0015)` with `replay_fund(...)`:
  - each sleeve steps on its own schedule;
  - on the first session of each new month, sleeve values are restored to target weights;
  - inside a stock sleeve, lots are scaled pro-rata to the new value (charging `reset_cost` on the rupees moved,
    as `blend` does).
- `model_start` is validated: from 2020-10-01 to today; otherwise a `ValueError` naming the allowed range.

**Unit tests:**
- Two-sleeve synthetic fund: weights restored on a month change.
- Cost equals 0.15% of the rupees moved.
- Pro-rata lot scaling keeps each lot's age.

**Golden master (integration):** feed the report's sleeve curves and asset series. `replay_fund` equals
`fund_report_data.blend` to 1e-9.

### Task 11 — Engine versioning and a full-fund smoke test
- Add `ENGINE_VERSION = "1.0.0"` in `fund/engine/__init__.py`.
- Add `tests/fund_desk/test_fund_smoke.py` (integration): the full five-sleeve fund with GOLDBEES and MON100,
  `model_start` 2020-10-01. It asserts:
  - it runs;
  - the final value is finite;
  - weights on each reset day are within 1e-9 of the target;
  - every sleeve has ≥ 1 order.
- Record the CAGR in the test output. It is **not asserted**: the ETFs differ from the report's series (spec §4.5).

## Done when
- All unit tests pass by default.
- `pytest -m integration tests/fund_desk` passes against the frozen harness.
- No existing test regresses (full suite green).
- A review confirms `fund/engine/` has no I/O.
