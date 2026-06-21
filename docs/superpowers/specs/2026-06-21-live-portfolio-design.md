# Live Portfolio — Design Spec

> First user-facing surface of the **tvagent** production quant terminal. Backend stays
> market-hunt (FastAPI + Mongo); we add a thin, additive set of portfolio endpoints and build a
> brand-new glass-themed Next.js frontend in `tvagent/frontend/`. Research is done (see
> `RESEARCH_LOG.md` / aligned-stack); this productionizes *running* it as composable portfolios.

**Date:** 2026-06-21 · **Status:** approved design, pre-plan

---

## 1 · Goal

Let a user create and monitor **multiple portfolios**, where a portfolio is a **bundle of strategies**
("sleeves") spanning any combination of indexes, rebalance types, holdings caps, and ranking methods
available in the existing simulator. Each portfolio shows a **live** (as-of-last-session) view: current
holdings, performance curve in rupees vs **Nifty 50**, sleeve attribution, and what to rebalance now.

This is the first of three planned terminal surfaces (Monitor → Research → Backtest). The app shell and
navigation are designed to hold all three; only **Live Portfolio** is built now.

## 2 · Core concepts

- **Sleeve** — one strategy run config: a saved strategy's rules (`strategy_id`) + execution params
  (`universe`, `rebalance_frequency`, `rebalance_date`, `rebalance_type`, `max_holdings`,
  `momentum_ranking`) + a **capital weight %**.
- **Portfolio** — N capital-weighted sleeves (fund-of-funds). Each sleeve runs independently on its own
  universe/schedule; the portfolio is the capital-weighted composition of the sleeves.
- **Composition model** — **capital-weighted sleeves.** Sleeve gets `total_capital × weight%`; portfolio
  value = sum of sleeve values; holdings = union (tagged by sleeve); benchmark = a single Nifty 50
  bought-and-held with the same `total_capital`.
- **"Live"** — on demand: run each sleeve to *today* (`end_date = as_of`), PIT-on. The **last daily entry
  is the current book**; the daily `results[]` series is the equity curve. There are **no real broker
  fills** — the track is a **hypothetical simulation** of what the strategies would have done from
  inception to now. The UI labels it `HYPOTHETICAL · PIT` everywhere it shows performance.

### Honesty constraints (non-negotiable, per project ethos)
- Default **point-in-time membership ON** (survivorship-bias-free); a portfolio that requests a universe
  without reconstitution data **fails loud** (no silent fallback to the biased current snapshot).
- Performance is labeled hypothetical/simulated; "as of <last session date>" is always shown.
- PIT **coverage** (ever-members that had no price data) is surfaced, not hidden.

## 3 · Scope

**In:** portfolio CRUD + composite-run backend endpoints; the glass frontend (Portfolios home, Live
Portfolio detail, portfolio builder/editor); reuse of existing strategies (`/api/simulation/strategies`)
incl. inline create.
**Out (future surfaces / phases):** Research & Backtest surfaces; real execution/fills/live tracking with
a persisted track record; multi-user/auth; alerts; editing the data layer or migrating off Mongo.

---

## 4 · Backend (additive to market-hunt `api_server.py`)

### 4.1 `portfolios` Mongo collection
```jsonc
{
  "id": "portfolio_<ms-ts>",
  "name": "Growth Book",
  "description": "",
  "total_capital": 1000000,            // ₹ split across sleeves
  "benchmark_symbol": "Nifty 50",      // portfolio-level, fixed baseline
  "inception_date": "2025-06-21",      // default today−1y; floor 2022-01-01
  "point_in_time_membership": true,    // portfolio-level default
  "sleeves": [
    {
      "id": "sleeve_<n>",
      "label": "Mid · monthly",
      "strategy_id": "strategy_...",   // refs simulation_strategies
      "weight": 45,                     // capital % ; sleeve weights sum to 100
      "universe": "NIFTYMIDCAP150",
      "rebalance_frequency": "monthly", // monthly|weekly|quarterly|dynamic
      "rebalance_date": "first",        // first|last|mid
      "rebalance_type": "equal_weight", // equal_weight|skewed
      "max_holdings": 10,
      "momentum_ranking": "20_day_return",
      "include_brokerage": true,
      "exchange": "NSE"
    }
  ],
  "created_at": "...", "last_modified": "..."
}
```
Indexes: `id` (unique). New Pydantic models `Sleeve`, `Portfolio` mirror this.

### 4.2 Endpoints
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/portfolio` | create (validate, persist) → `{success, portfolio_id}` |
| `GET` | `/api/portfolio` | list all → `{success, portfolios:[...]}` |
| `GET` | `/api/portfolio/{id}` | fetch one (404 if missing) |
| `PUT` | `/api/portfolio/{id}` | update (preserve id) |
| `DELETE` | `/api/portfolio/{id}` | delete |
| `POST` | `/api/portfolio/{id}/run?end_date=YYYY-MM-DD` | **composite run** (default `end_date`=today) |

**Validation (create/update):** ≥1 sleeve · weights sum to 100 (±0.5) · every `strategy_id` exists ·
`inception_date ≥ 2022-01-01` and `≤ end` · each `universe` non-empty. Reject with HTTP 400 + clear detail.

### 4.3 Composite-run algorithm (`/api/portfolio/{id}/run`)
1. Load portfolio. For each sleeve, build `SimulationParams`:
   `portfolio_base_value = round(total_capital × weight/100)`, `start_date = inception_date`,
   `end_date = as_of`, `point_in_time_membership = portfolio.point_in_time_membership`,
   `benchmark_symbol = "Nifty 50"`, plus the sleeve's exec params. Call the **existing**
   `run_strategy_simulation(...)`. (Reused unchanged — no engine fork.)
2. **Blended curve:** union all sleeve result dates; for each date, sum each sleeve's `portfolio_value`
   (forward-filling a sleeve's last known value on dates it lacks). → `curve[].portfolio_value`.
3. **Benchmark curve:** compute **once** from Nifty 50 daily closes —
   `benchmark_value(date) = total_capital × close(date)/close(inception)`. (Computed independently, **not**
   summed from sleeves, to avoid alignment/double-count bugs.) → `curve[].benchmark_value`.
4. **Current book:** take each sleeve's **last** daily entry `holdings[]`; tag each holding with its
   sleeve `label`; aggregate the same symbol appearing in >1 sleeve (sum `market_value`, `quantity`;
   weighted-avg prices); recompute `weight = market_value / final_blended_value`.
5. **Summary:** `total_return = (final_blended/total_capital − 1)×100`; `benchmark_return` likewise;
   `alpha = total_return − benchmark_return`; `max_drawdown` on the blended curve; `sharpe_ratio` on
   blended daily returns (reuse the simulator's 252-day / 6% rf method); `total_value`, `total_pnl`.
6. **Per-sleeve attribution:** each sleeve's `total_return`, `contribution = sleeve_return × weight/100`,
   `current_holdings_count`, and `point_in_time_coverage`.
7. **Rebalance actions:** from each sleeve's latest rebalance, collect `new_added`/`exited`, tagged by
   sleeve, plus the next-due date per cadence. (v1: show; no execution.)

**Response:**
```jsonc
{ "success": true,
  "portfolio": { "id","name","as_of","inception_date","point_in_time": true },
  "summary": { "total_value","total_pnl","total_return","benchmark_return","alpha",
               "max_drawdown","sharpe_ratio" },
  "curve": [ { "date","portfolio_value","benchmark_value" } ],
  "current_book": [ { "symbol","sleeve","quantity","current_price","market_value","weight",
                      "pnl","pnl_percent" } ],
  "sleeves": [ { "id","label","weight","total_return","contribution","holdings_count","coverage" } ],
  "rebalance_actions": { "next_due": "YYYY-MM-DD",   // earliest upcoming rebalance across sleeves
                         "add": [{symbol,sleeve,target_weight}],
                         "exit": [{symbol,sleeve}] } }   // each tagged by its sleeve cadence
```

**Errors:** PIT unavailable for a sleeve's universe → propagate HTTP 400 (loud) with which universe; a
sleeve with zero priced symbols → reflected in `coverage`, not a crash; missing benchmark prices →
explicit error.

### 4.4 Backend tests (TDD)
- **Composition unit tests** (pure): given synthetic per-sleeve `results[]`, assert blended curve =
  weighted sum, benchmark independent, holdings union+aggregation, summary math, attribution sums to
  portfolio return (within rounding).
- **Validation tests:** weights≠100, unknown `strategy_id`, inception < 2022 → 400.
- **Endpoint tests:** CRUD round-trip in Mongo; `/run` happy path against seeded strategies/data; PIT-
  unavailable universe → 400. Follow the repo's existing `test_*_api.py` style.

---

## 5 · Frontend (`tvagent/frontend/` — new Next.js app)

### 5.1 Tech stack (reuse market-hunt's proven choices, fresh design)
Next.js 15 (App Router) · React 19 · TypeScript · Tailwind CSS 4 · TanStack Query (data) ·
TanStack Table (holdings) · **Recharts** area chart for the ₹ curve (declarative, fits a simple
two-series area; lightweight-charts available later if we add candlesticks) · fetch client to
`http://localhost:3001`. API base via env (`NEXT_PUBLIC_API_BASE`).

### 5.2 Screens
1. **Portfolios home** (`/`) — grid of glass portfolio cards: name, current ₹ value, return %, mini
   sparkline, sleeve-color dots, as-of date. `+ New portfolio`. Click → detail.
2. **Live Portfolio detail** (`/portfolio/[id]`) — the **dense glass grid** (locked design):
   - **Command bar:** portfolio switcher (dropdown) · `HYPOTHETICAL · PIT` badge · date-range
     (`1Y` default / `Since 2022` / `Custom`, floor 2022-01-01) · as-of date · **Run live** (refetch) ·
     **Edit**.
   - **Left col:** KPI strip (Invested→Now, Nifty-same-₹, Alpha, Sharpe·MaxDD) → **₹ curve vs Nifty 50**
     (both start at `total_capital` at inception, area-filled) → **Holdings** table (sleeve-tagged,
     sortable).
   - **Right col:** **Sleeve attribution** (weight·return·contribution + PIT coverage) → **Rebalance now**
     (add/exit, next-due) → **Capital allocation** bar.
3. **Portfolio builder / editor** (dedicated routes `/portfolio/new` and `/portfolio/[id]/edit`): name,
   `total_capital` (default ₹10,00,000),
   `inception_date` (default today−1y, floor 2022), PIT toggle (default ON), benchmark (Nifty 50, fixed
   v1). **Sleeves:** add rows — pick a saved strategy (`/api/simulation/strategies`) or **create one
   inline** (rules: metric/operator/threshold), set universe/rebalance/holdings/ranking + **weight %**.
   Live weight-sum validator (must = 100). Save → `POST/PUT /api/portfolio`.

### 5.3 States
- **Empty** (no portfolios) — inviting "create your first portfolio" on home.
- **Loading / running** — `/run` fans out N sleeve simulations; show a progress state (can take seconds);
  skeleton glass panels.
- **Error** — backend down, or **PIT unavailable** for a universe → show the loud 400 detail with a
  "load reconstitution for X" hint; never render a silently-biased result.
- **Partial coverage** — show a coverage chip (e.g. "PIT 96% · 3 unpriced") rather than hiding the gap.

### 5.4 Design system — glass (locked)
- **Background:** deep slate `linear-gradient(160deg,#0c1020,#0a0d18)` + 2–3 soft **radial glows**
  (indigo/violet/teal) so the blur has something to refract.
- **`.glass` panel:** `bg linear-gradient(180deg, rgba(255,255,255,.085), rgba(255,255,255,.03))`,
  `border 1px rgba(255,255,255,.10)`, `box-shadow 0 8px 30px rgba(0,0,0,.35), inset 0 1px 0
  rgba(255,255,255,.16)` (bright top edge), `backdrop-filter blur(14px) saturate(140%)`, radius 12px.
- **Accents:** positive `#34d399`, negative `#fb7185`, primary `#7c9cff`, sleeve palette
  `#34d399 / #7c9cff / #c084fc / …`, muted `#93a0bd`.
- **Type:** Inter (UI); **tabular-nums** for all figures; 9px uppercase tracked micro-labels for panel
  titles. The frontend-design skill governs the actual build; these tokens are the committed direction.
- Codify as Tailwind theme tokens + a `.glass` utility/component; centralize colors so they're not
  hardcoded per component.

### 5.5 Frontend tests
- API client + composition-response typing (mock `/run` payload).
- Builder validation (weight sum, ≥1 sleeve, inception floor).
- Render tests for the three states (loading/error/partial) and the detail layout with a fixture payload.

---

## 6 · Edge cases
- Same symbol held by multiple sleeves → aggregated in the book (no duplicate rows).
- Sleeve with 0 holdings on a date (gate excludes all) → contributes its cash value; book shows nothing
  for it; curve still continuous.
- `end_date` on a non-trading day → last available session is the as-of.
- Weights that don't sum to 100 → blocked at save (validation), never silently normalized.
- Very long inception (Since 2022) → more sleeves × more dates; show progress; consider per-sleeve
  caching keyed by (sleeve params, inception, as_of) — optimization, not v1-blocking.

## 7 · Definition of done
Backend: portfolio CRUD + `/run` with composition, validated, TDD-green against Mongo, PIT-loud.
Frontend: Portfolios home + Live Portfolio detail (glass, locked layout) + builder, wired to the backend,
all three states handled, honest labeling throughout. A user can create a multi-sleeve portfolio, run it
live, and read its ₹ performance vs Nifty 50 with per-sleeve attribution.
