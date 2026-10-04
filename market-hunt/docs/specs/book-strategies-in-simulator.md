# Book strategies in the simulator and portfolio simulator

Status: approved 2026-09-28 (scope: Grid-10 + tranche). Author: Arpitha B.

## Why

The fund's three stock sleeves — value (Grid-10), growth and satellite (weekly top-5 tranches) — run only inside the
fund desk engine (`fund/engine/`). The market-hunt strategy simulator (`/api/simulation/run`) and portfolio simulator
(`/api/portfolio/{id}/run`) cannot express them:

- no "hold unless" exit (Grid-10 keeps a name until grid DOWN **and** 22-day return > 0);
- no top-up-to-equity/N sizing without trimming, no age-limited tranches with a weight cap;
- no 22-day price-ROC in the signal vocabulary.

Re-implementing those rules in the stateless or wave engine would create a second truth that drifts. Instead the
simulator gets a third strategy type — **book** — that runs the fund desk engine itself.

## Design

### Strategy schema

`Strategy.book: Optional[BookConfig]`, persisted in `simulation_strategies` like `wave`.

```json
{"kind": "grid10",  "params": {"n": 10}}
{"kind": "tranche", "params": {"top": 5, "weeks": 8, "slot_div": 40, "max_w": 0.10, "deploy_cash": true}}
```

`kind` ∈ {`grid10`, `tranche`}; params are validated by the fund desk's own rule table (`fund.config.PARAMS`) through
a shared `validate_book(kind, params)` — one validator for the fund desk and the simulator. Omitted params take the
engine defaults.

### One engine factory

`fund/engine/factory.py`:
- `make_book(kind, params, symbol=None)` — the engine object (`Grid10` / `Tranche` / `Asset`). `fund.service._book`
  delegates to it.
- `base_schedule(kind, cal, weekday)` — rebalance days: Grid-10 = `period_start_shifted(cal, "weekly", k)`, tranche =
  `week_session(cal, k)`. `fund.service.compute_run` uses it for its back-filled history.

### Simulator run (`/api/simulation/run`)

Routing order: **book** (request or saved strategy) → wave → stateless. The book path calls
`engine/book_adapter.run_book_simulation`:

1. `fund.data.build_market(db, universe, kind, start, today)` — same loader as the fund desk (PIT membership anchored
   at today, 122-day warm-up, TrueVX signals).
2. `replay_sleeve(make_book(...), market, costs, start, end, base_schedule(kind, cal, book_weekday), capital)`.
3. `to_sim_result(...)` maps the replay onto the existing simulator response (`results[]` day rows with
   `portfolio_value`, `benchmark_value`, `holdings[]`, `new_added`, `exited`, `exited_details`, `cash`, `trade_details`;
   `summary` with `total_return`, `benchmark_return`, `alpha`, `max_drawdown`, `sharpe_ratio`, `total_trades`) plus a
   `book` block (kind, params, weekday, engine_version, order reasons). The existing results UI and
   `portfolio_composition` consume it unchanged.

Run settings that apply: `start_date`, `end_date`, `portfolio_base_value`, `universe`, `slippage_bps`,
`include_brokerage` (off → zero statutory charges, slippage still applies), `benchmark_symbol` (default Nifty 50),
new `book_weekday` (0 = Mon … 4 = Fri).

Honesty rules (fail loud, never silently ignore):
- `start_date` ≥ 2020-10-01 (`MODEL_START_FLOOR`, the validated PIT floor) — 400 otherwise.
- The book engine is always point-in-time; `point_in_time_membership=false` is rejected.
- Settings the book engine does not implement are rejected with 400 when set to a non-default value:
  `primary_filter_take`, `regime_gate`, `stock_gate`, `portfolio_gate`, `stop_loss_percent`, `side='short'`,
  `rebalance_offset_sessions`, `wave`, `filter`, and `rebalance_frequency` other than `weekly`.
- `max_holdings` / `momentum_ranking` / `rebalance_type` / `rebalance_date` are defined by the book itself; they are
  ignored and the response's `book.ignored_settings` lists any that were sent with non-default values.

Execution timing differs from the stateless engine and is stated in the response: signals at the previous close,
fills at the session's open (the fund desk rule), not same-close fills.

### Portfolio simulator

`_run_sleeve` checks the saved strategy; a book strategy runs `run_book_simulation` with the sleeve's capital, universe,
slippage, brokerage flag and new `Sleeve.book_weekday`. A book sleeve with any gate on, or a run with
`offset_sessions != 0`, is refused (400) for the reasons above. Inception floor for a portfolio containing a book
sleeve stays 2022-01-01 (portfolio floor, later than the book floor).

### Frontend

- Simulator builder: a "Book engine" section (like the wave section) — kind picker + that kind's params; saved
  strategies show a `book` badge; run form defaults frequency to weekly and shows the weekday picker for book
  strategies, hiding settings the book does not use.
- Portfolio form: when a sleeve's strategy is a book, hide frequency / max holdings / ranking / gates and show the
  weekday picker.

## Verification

- Unit (synthetic markets, `tests/fund_desk/helpers.make_market`): validator, factory parity with the old service
  code, schedule parity, `to_sim_result` shape and arithmetic (portfolio_value == replay equity to the rupee, holdings
  sum + cash == portfolio_value, exits carry P&L), guard rejections.
- Integration (live Mongo): simulator book run of Grid-10 on NIFTY50 2020-10-01 → 2026-09-25, k = 0..4, final
  multiple equals the frozen golden curve `scripts/research_grid/grid10_weekly.csv` (rel 1e-9); tranche run on
  NIFTY200 equals `replay_sleeve` on the same market; portfolio run with a book sleeve blends without error.
- Existing suites (stateless, wave, fund desk golden masters) unchanged.

### Other engines refuse books

`run_strategy_simulation` / `run_strategy_simulation_debug` raise on a strategy with a `book` block, so no other
caller (multi-run sweeps, walk-forward, anything new) can silently run a book's empty rule list as "hold everything".
The portfolio preview / rebalance plan (`/api/portfolio/{id}/preview`) refuses portfolios with book sleeves: its
equal-weight target sizing would misstate a no-trim / capped book. Book sleeves are traded from the fund desk ticket.

## Out of scope

Asset (ETF) sleeves in the simulator; a generic rule builder for hold-unless exits; fund-desk sleeves that reference
saved simulator strategies; a book-aware portfolio preview / rebalance plan.
