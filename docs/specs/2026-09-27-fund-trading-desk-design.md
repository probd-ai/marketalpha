# Fund Trading Desk — v1 design

**Date:** 2026-09-27 · **Status:** draft for approval · **Author:** Arpitha B

## 1. Goal

Run the five-sleeve fund described in the Fund Strategy & Operating Report (27 Sep 2026) as a
**model portfolio**. Every week the system rebuilds each sleeve's model book, then publishes a **trade ticket**. The
fund manager executes the ticket by hand at the broker and records what was done.

The engine that produces the ticket is the same engine that produces the back-test. This follows the tvagent north
star: research you can trust becomes trading you can run.

### In scope (v1)
- A **book engine** (backend, market-hunt). It implements the three sleeve kinds: Grid-10, weekly tranche, and asset
  (gold / Nasdaq-100). It also implements the fund layer, which does the monthly sleeve reset.
- **Weekly and monthly runs**, published as immutable snapshots.
- **Trade tickets**: per-sleeve orders with share quantities, each with a reason code.
- **Pre-trade checks.**
- **Manual execution log**, plus a model-versus-actual tracking view.
- **Trading desk frontend**, a new Next.js app at `tvagent/frontend/`.

### Out of scope (v1) — later phases
- Broker connectivity (Dhan or Kite order placement, live holdings sync).
- The research library.
- A risk and compliance module beyond the pre-trade checks.
- Investor units / NAV register.
- Choosing the go-live transition (adopt current holdings vs start from cash) — decided during paper trading.

## 2. Concepts

| Term | Meaning |
|---|---|
| **Fund** | One capital pool (₹45 L) with a **configurable model start date**, a base currency (INR) and a list of sleeves with target weights |
| **Sleeve** | A capital slice run by one rule set: `grid10`, `tranche` or `asset` |
| **Model book** | A sleeve's positions according to the engine (fractional shares, rupee values, lots) |
| **Run** | One weekly (or monthly-reset) step of every due sleeve. Uses signals from the previous close and fills at the next open |
| **Snapshot** | The frozen result of a run: model books before and after, trades, reasons, check results, data fingerprint |
| **Ticket** | A snapshot's trades converted to whole-share orders for the fund's actual capital, with limit guidance |
| **Execution record** | What the manager actually did per ticket line: done, partial or skipped, with quantity and price |
| **Actual book** | Positions implied by all execution records. Compared with the model book |

## 3. Architecture

```
tvagent/
  frontend/                 NEW  Next.js 15 · React 19 · Tailwind 4 · TanStack Query · Recharts · Vitest
  market-hunt/              backend (FastAPI + Mongo, port 3001)
    fund/                   NEW package
      engine/               pure Python, no I/O
        types.py            Signals, Prices, Lot, SleeveState, Order, Reason
        grid10.py           Grid-10 rule set
        tranche.py          weekly-tranche rule set
        asset.py            asset sleeve (hold target weight)
        costs.py            charges + slippage (BrokerageCalculator rates injected)
        fund.py             fund layer: sleeves + monthly reset
        calendar.py         weekly / monthly schedule, flexible weekday
      data.py               builds Signals/Prices from Mongo (PIT membership, TrueVX, prices)
      store.py              snapshots, checkpoints, tickets, execution records (Mongo)
      service.py            run orchestration, drift guard, ticket builder, checks
    app/routers/fund.py     NEW  REST endpoints (§8)
    tests/fund/             NEW  unit + golden-master tests
```

**Rules**
- The engine is **pure**: data in, state and orders out. It never touches Mongo, the clock or the network.
- `fund/data.py` is the only place that reads market data.
- `fund/store.py` is the only place that persists.
- Existing code (`engine/stateless.py`, portfolios, broker, holdings) is **not modified**. The new desk sits beside it.

## 4. Book engine

### 4.1 Interface

```python
class Sleeve(Protocol):
    def step(self, state: SleeveState, sig: Signals, px: Prices, costs: Costs) -> StepResult: ...
# StepResult = (new_state, orders: list[Order])
# each Order = (symbol, side, qty, fill_px, reason: Reason, lot_id)
```

- `Signals` holds everything taken at the **previous close**:
  - index members as of that date;
  - 22-day return;
  - grid state;
  - Alpha eligibility.
- `Prices` holds the **open** of the trading day, used for fills, and closes for valuation.
- `SleeveState` holds:
  - cash;
  - lots (symbol, qty, open date, tranche age in rebalances);
  - the sleeve's own parameters.

### 4.2 Rule sets (exactly as tested; parameters are config, defaults shown)

**Grid-10** (`grid10`; universe NIFTY50, N=10, band 5, lookback 22)
1. Grid state per stock = sign of the last change in `floor(mean_long / 5)`, held between changes.
2. Sell a holding when grid = −1 **and** 22-day return > 0.
3. Sell a holding when it leaves the universe.
4. Buy: fill slots up to N with the lowest 22-day return names among grid = +1 members not held.
5. Target = equity / N. Money is applied in this order:
   - gaps: new names up to target, then under-target holdings;
   - if money is short, every gap is filled pro-rata;
   - any remainder is spread equally over all holdings.
6. Never trim.

**Weekly tranche** (`tranche`; growth = NIFTY200, satellite = NIFTY500MICRO750)
1. Alpha-eligible = truevx_score > 90 ∧ mean_short > 80 ∧ mean_mid > 60 ∧ mean_long > 50.
2. Picks = top 5 by 22-day return among eligible members.
3. Tranche = equity / 40.
4. A pick adds a tranche, capped so the holding does not exceed 10% of equity.
5. A tranche expires after 8 rebalances; if its stock is picked again it rolls over (netting in one order).
6. Every rebalance, trim any holding above 10% back to 10%.
7. Leftover cash tops up holdings in rank order, up to 10% each, as fresh tranches.
8. Forced sell on universe exit.
9. A halted stock waits.

**Asset** (`asset`): hold one instrument. It is traded only by the fund-layer monthly reset.
- Gold = **GOLDBEES** (Nippon India ETF Gold BeES).
- Nasdaq-100 = **MON100** (Motilal Oswal Nasdaq 100 ETF).
- Both are NSE-listed and priced from market-hunt (history from 2007 / 2011, current to the last session). The engine
  values and trades the ETF itself, so there is no index proxy and no fee assumption; the ETF price already carries
  expenses and tracking error.

### 4.3 Fund layer
- Sleeves run on their own schedules (the stock sleeves weekly).
- In the first trading week of each month, sleeve values are restored to target weights.
- Cash moves between sleeves. Inside a stock sleeve it is applied pro-rata to existing positions, as the back-test does.
- The rupees moved are charged at 0.15% (configurable).

### 4.4 Costs and timing (fixes engine bug #1 by construction)
- Statutory charges on **both** sides, using `BrokerageCalculator` rates.
- Slippage of 5 bp per side.
- Fills at the next open.
- Warm-up: data loaded from 122 calendar days before inception (the validated research window: 22-bar returns + grid history).

### 4.5 Acceptance — golden master
With identical inputs, the engine must reproduce the research harness curves to within 1e-9 relative:
- `grid10_weekly.csv` (Grid-10, weekday k = 0..4);
- `n200_8wcap10_deploy.csv`;
- `n750_cap_weekly8w_cap10deploy.csv`.

The fund layer must reproduce the report's whole-fund median path (the `blend` in `fund_report_data.py`) **when fed
the report's asset series** (TradingView gold / NDX in ₹, net 0.5%/yr). This is a test fixture only; production uses
the ETFs.

Known difference, Oct 2020 – Sep 2026:

| Series | GOLDBEES | MON100 |
|---|---|---|
| ETF | 18.9%/yr | 25.6%/yr |
| Report's TradingView series (net 0.5%/yr) | 19.2%/yr | 22.4%/yr |

MON100's extra ~3 pts reflects a widening premium to NAV caused by overseas-investment limits. That premium can
shrink, so it is a risk, not an edge.

The harness scripts are frozen as the **oracle**. Any intentional difference must be written down here first.

## 5. Runs, snapshots, checkpoints

### 5.0 Model start date (user setting)
- `model_start` is part of the fund config and is chosen by the manager. It defaults to 2020-10-01, the report's
  back-test start.
- **Allowed range:** from 2020-10-01 up to today.
  - 2020-10-01 is the earliest date with validated point-in-time membership for all three stock universes.
  - Warm-up data is loaded 122 days before `model_start`.
- **Effect.** Every sleeve starts from cash at `model_start`, at its target weight. The engine replays to today, so
  the first ticket buys whatever the model holds by then.
  - An early date (e.g. 2020-10-01) gives a mature book, identical to the report.
  - Today's date builds the books from scratch.
- **Changing it** creates a new **model version**:
  - new checkpoints and a fresh replay;
  - earlier snapshots stay in the journal under their old version and are never edited;
  - the desk shows which version is active.
- Changing capital or sleeve weights also creates a new model version, and needs a confirmation step on the page.

- **Schedule.** One run per week per stock sleeve, on any weekday the manager chooses. The run is refused if a run
  for that ISO week is already published. The monthly reset is a separate run type in the first trading week.
- **Checkpoint.** After each published run, the complete engine state is stored. The next run loads it and steps
  forward, so it takes seconds, not a full replay.
- **Replay from inception** is still available as a verification job. It must equal the latest checkpoint.
- **Snapshot** (immutable once published), containing:
  - run id, type, ISO week, signal date, fill date;
  - a data fingerprint: a hash of the prices, indicators and membership used;
  - the model books before and after;
  - the orders with reasons;
  - the check results;
  - the engine version.
- **Drift guard.** Before publishing, the verification replay is compared with the published history. A mismatch means
  the data changed under a published week. The desk then shows `DRIFT` with the differing weeks and symbols and
  blocks the run until the manager acknowledges it. Past snapshots are never rewritten.
- **Lifecycle:** `DRAFT` (computed, reviewable) → `PUBLISHED` (manager approves; ticket frozen) → `EXECUTING`
  (execution records being entered) → `CLOSED`.

## 6. Tickets

- Model trades are converted to whole shares for the fund's actual capital, using the latest close.
  - Buys round down; sells round to the full held quantity when a position is being exited.
  - Residual cash is shown.
- Each line shows:
  - sleeve, symbol, side, quantity, reference price, rupee value, limit guidance (reference ± 0.3%, configurable);
  - a reason code and a plain-English reason, e.g. `GRID_DOWN_POSITIVE_MONTH` — "grid turned down, 22-day return +4.1%".
- Order of execution: sells before buys, grouped by sleeve.
- Export: CSV (for manual entry at the broker) and a printable view.

**Reason codes**

| Code | Meaning |
|---|---|
| `GRID_DOWN_POSITIVE_MONTH` | Grid-10 sell rule |
| `NEW_LOWEST_22D` | Grid-10 entry |
| `TOPUP_TO_TARGET` | Sale money used to fill a gap |
| `REMAINDER_SPREAD` | Leftover money spread equally |
| `TOP5_NEW_TRANCHE` | Weekly-tranche buy |
| `TRANCHE_ROLL` | Expiring tranche renewed |
| `TRANCHE_EXPIRED` | 8-rebalance clock ran out |
| `TRIM_ABOVE_CAP` | Holding above 10% trimmed |
| `CASH_DEPLOY` | Leftover cash top-up |
| `INDEX_EXIT` | Stock left the universe |
| `MONTHLY_RESET` | Fund-layer sleeve reset |

## 7. Pre-trade checks

Each check produces `PASS`, `WARN` or `BLOCK`. A `BLOCK` prevents publishing.

| Check | Blocks when |
|---|---|
| Data freshness | Latest price or indicator date is older than the last trading session |
| Coverage | A universe member lacks a price or indicator on the signal date |
| Membership | Membership data does not cover the signal date |
| Drift | The verification replay differs from published history |
| Caps (tranche) | Any post-trade holding is above 10% + tolerance |
| Cash | Any sleeve's post-trade cash < −0.5% of equity |
| Concentration (Grid-10) | WARN only when a holding is above 25% (informational; no cap by design) |
| Liquidity | WARN when an order exceeds 5% of the 20-day average traded value |
| Instrument | BLOCK when an asset sleeve's ETF has no price on the signal date |
| ETF premium (MON100) | WARN only. Informational until an iNAV source is added (§11) |

## 8. API (market-hunt, `/api/fund/...`)

| Method | Path | Purpose |
|---|---|---|
| GET/PUT | `/api/fund` | Fund config (capital, inception, sleeves, weights, instruments) |
| GET | `/api/fund/status` | Data freshness, next due runs, last snapshot, drift state |
| POST | `/api/fund/runs` | Compute a DRAFT run (weekly or monthly) → job id (async worker thread) |
| GET | `/api/fund/runs/{id}` | Snapshot: books, orders, checks |
| POST | `/api/fund/runs/{id}/publish` | Human approval → PUBLISHED, ticket frozen |
| GET | `/api/fund/runs/{id}/ticket` | Ticket JSON; `?format=csv` |
| POST | `/api/fund/runs/{id}/executions` | Record done / partial / skipped per ticket line |
| GET | `/api/fund/books` | Current model books per sleeve |
| GET | `/api/fund/actual` | Actual book from execution records + tracking difference vs model |
| GET | `/api/fund/journal` | Append-only audit log (runs, publishes, executions, acknowledgements) |
| POST | `/api/fund/verify` | Full replay vs checkpoint (drift check) → job id |

- Every endpoint is localhost-only, consistent with the existing `MH_API_TOKEN` rule.
- No endpoint accepts a symbol, quantity or price for the model. Those only come from the engine.
- Execution records do accept the manager's actual fill quantity and price.

## 9. Frontend — trading desk (`tvagent/frontend/`)

| Screen | Content |
|---|---|
| **Desk** (home) | Week status: data fresh? which sleeves due? drift? The last snapshot, and a "Compute this week's run" action |
| **Run review** | Per-sleeve tabs. Before/after model book, orders with reasons, the check panel, and a Publish button (confirmation built into the page) |
| **Ticket** | Sells then buys, by sleeve. Whole-share quantities, limit guidance, CSV export, print view. Execution entry per line |
| **Books** | Model book per sleeve: weight, lots / tranche ages, 22-day return, grid state or Alpha status |
| **Tracking** | Model vs actual per sleeve: quantity gaps and the rupee tracking difference |
| **Journal** | Audit log with filters |

- Design: dense, calm terminal look, light and dark themes.
- Tables are sortable.
- Every number comes from the API; the frontend never recomputes a quantity.

## 10. Testing

- **Engine unit tests**: every rule in §4.2, including edge cases:
  - halted stock;
  - a pick already at the cap;
  - fewer than N eligible names;
  - index exit on a trim day;
  - sale money short versus in surplus;
  - roll versus expiry.
- **Golden master** (§4.5) runs in CI against the frozen harness outputs.
- **Determinism**: two replays from the same data → byte-identical snapshots.
- **Checkpoint equivalence**: checkpoint + step equals a full replay.
- **Service tests**: drift guard, publish lifecycle, ticket rounding, check verdicts.
- **API**: integration smoke tests (marked `integration`, as existing).
- **Frontend**: Vitest component tests and a build check.

## 11. Open items
1. MON100 premium to NAV: no iNAV feed yet, so the premium check is a placeholder. Add a source later.
3. Go-live transition (adopt current holdings vs start from cash) — during paper trading.
4. Broker connectivity phase: reuse the existing sync-plan → approve → execute chain by feeding it the published ticket.

## 12. As-built notes (2026-09-27)

Decisions made during the build that refine the sections above:

- **Deterministic symbol order.**
  - The research harness iterated Python sets, so its results changed with each process's hash seed. Growth on
    weekday 0 came out 5.59x, 5.76x and 5.80x on the same data.
  - The engine visits symbols in sorted order, and a cross-hash-seed test proves identical output.
  - The golden-master tests force the frozen harness into the same order (`tests/fund_desk/oracle.py`).
- **Checkpoints = drift verification.**
  - Each run replays from `model_start`, which takes seconds; data loading dominates at about 30 s. The replay runs in
    segments ending at every published run's signal date.
  - At each segment end the state is compared with that run's stored checkpoint. A mismatch gives Drift = BLOCK
    until the manager acknowledges it; after that it shows as WARN.
- **Next-session projection.** A run is computed after the close of signal day S. The fill session F is projected with
  open = close = S's close, so the week's orders exist before F's prices do. The real open replaces the projection on
  the next run.
- **Initial build.** The first run of a model version buys the whole model book at target weights. Later runs carry
  only that week's changes.
- **Capital anchor.**
  - `capital` is the money at go-live. Rupee amounts are anchored so the fund equals `capital` on the first published
    run's signal day (or on S before anything is published), then grow with the model.
  - The desk chart is labelled as a back-test with capital invested at `model_start`.
- **Monthly reset** happens on the first run day of each month (the spec's "first trading week"), not on the first
  calendar session.
- **Index exits in Grid-10** are queued at the start of the day using that day's membership. This gives the same result
  as the harness's end-of-previous-day queue, and stays exact across checkpoints.
- **Warm-up** is 122 days (§4.4).
- **Tests live in `tests/fund_desk/`**, because `tests/fund/` clashed with the `fund` package name.

## 13. Security review (2026-09-27)

This was an adversarial review of the desk code by the assistant. Human sign-off is still required before merge
(organisation policy).

| # | Severity | Finding | Fix | Evidence |
|---|---|---|---|---|
| 1 | **Critical** | The frontend listened on `0.0.0.0:3300` with a catch-all `/api/*` rewrite. The backend trusts 127.0.0.1, so every localhost-only API — including `/api/broker` order placement — was reachable from the LAN (verified: `200` from 192.168.1.6). | Rewrite restricted to `/api/fund/*`; Next bound to `127.0.0.1` (`-H`). | Re-test: LAN unreachable; the broker route is not forwarded (404). |
| 2 | High | CSRF: body-less POSTs (publish, discard, run, drift-ack) are CORS "simple requests", so any website open in the browser could trigger them. | Required `X-Fund-Desk: 1` header (forces a preflight, which the allow-list refuses) plus an Origin allow-list check on POST/PUT. | `test_security.py`; live curl gives 403 / 400. |
| 3 | High | Settings accepted untyped sleeve dicts: Mongo operator injection via `universe`, unbounded parameters, unknown keys. | Full type, range and allow-list validation (indices with reconstitution data; symbols in `symbol_mappings`). | Unit tests; a live injection attempt is refused. |
| 4 | Medium | Execution records: `done` could store an arbitrary or negative quantity; NaN/∞ prices were accepted; no typo guard; duplicate lines were allowed. | `done` = ticket quantity; `partial` strictly between 0 and the ticket quantity; price finite, > 0 and within ±20% of the reference; whole request validated before any write. | Unit tests. |
| 5 | Medium | Double publish for one ISO week was possible under concurrent requests (check-then-act). | Atomic DRAFT→PUBLISHED update, a unique sparse `publish_key` index, and a process lock. | Integration tests. |
| 6 | Low | CSV export: no escaping; formula injection possible. | `csv` module; leading `= + - @` neutralised; filename sanitised. | Unit test. |

**Residual items (accepted):**
- Job error text is returned to the localhost client.
- No authentication beyond localhost / `MH_API_TOKEN`. Suitable for a single-user machine.

## 14. Deposits and withdrawals (2026-09-27)

- **Units.** The fund is accounted in units against the model's NAV (the blended fund curve).
  - At go-live, `capital` buys `capital / NAV(go-live signal day)` units.
  - A cash flow recorded after go-live stays **PENDING**. The next published run applies it, buying or redeeming
    `amount / NAV(S)` units, so flows change the fund's size and never its returns.
- **Ticket.** A flow resizes every sleeve so the money lands at target weights. A deposit buys pro-rata to the
  sleeve's holdings (reason `DEPOSIT`); a withdrawal sells pro-rata (`WITHDRAWAL`). When the run also carries the
  monthly reset, the resize targets the reset weights of the post-flow value.
- **Guards:**
  - Flows are refused before go-live; set Capital instead.
  - The amount must be finite and non-zero.
  - A withdrawal must be smaller than the fund value.
  - Publishing is refused if pending flows changed after the run was computed.
  - A flow can be cancelled only while PENDING.
- **API:**
  - `GET /api/fund/cashflows`
  - `POST /api/fund/cashflows {amount, note}` (withdrawal = negative)
  - `POST /api/fund/cashflows/{id}/cancel`
- **UI:** the Cash page, plus net invested on the Desk.
- **Code:** `fund/flows.py` (pure), store `fund_cashflows`, service `add_cashflow` / `cancel_cashflow`.
- **Tests:** `test_flows.py` (unit) and `test_flows_service.py` (lifecycle).

## 15. Fund rebalance policy (2026-09-27)

- **Decision:** replace the monthly sleeve reset with **half-yearly (January and July) plus a 5-point drift band**.
  - The fund rebalances on the first run day of January and July.
  - On any other month's first run day, it rebalances early if a sleeve is more than 5 weight points from target.
- **Evidence** (`scripts/research_grid/desk_reset_policy.py`, live engine, Oct 2020 – Sep 2026):
  - Return and worst fall were unchanged: 31.2%/yr and −19.4%, against 31.4% and −19.7% for monthly.
  - Rebalances drop from 12 to 2 a year, and extra ticket lines from about 600 to about 100 a year.
  - Drift stayed at most 4.9 points; the band never fired.
  - Never rebalancing gave −24.3% worst fall and 15.5 points of drift.
  - Half-yearly and yearly without a band drift up to about 10 points depending on the months chosen. The band
    removes that timing luck.
- **Settings:**
  - `rebalance_months` (list of months), `rebalance_band` (weight points, or null to switch off). Both are
    material: a change starts a new model version.
  - Configs saved before this setting keep their original rule (every month, no band).
- **Applied** as model version 2. The initial-build draft was recomputed.

## 16. Between-run trades reach the ticket (2026-10-04)

- **Bug:** sleeves act on every session for index exits (`tranche.py` step 1, `grid10.py` step 1), but the ticket
  carried only the run day's orders. ASKAUTOLTD left the Total Market index effective Wed 2026-09-30; the model sold it
  that day and no ticket ever told the manager. Index reviews rarely take effect on a Monday (since 2025: 3/3 Nifty 50,
  6/7 Nifty 200, 11/12 Total Market removals were mid-week), so every review was exposed.
- **Fix:** `compute_run` adds every model order dated after the last published fill and up to S
  (`ticket.orders_since`) to the ticket, with holdings as of the last ticket (`ticket.held_before_ticket`) so the sale
  is a full exit. The line reads "Left its index on 30 Sep (model sold it then; sell at the next open)". The engine,
  replay and checkpoints are unchanged — only the ticket gains the missing lines.
- **Tests:** `tests/fund_desk/test_missed_orders.py` (unit + a real-data replay of the published 09-25 build).
- **Also 2026-10-04:** NSE trading-holiday calendar (`market_calendar.py`, refresh stage `calendar`, Mongo
  `market_holidays`) — the fill session skips exchange holidays; "Market calendar" pre-trade check.
