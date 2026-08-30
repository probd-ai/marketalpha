# Unified Stateful Strategy Builder — Design

**Date:** 2026-06-24
**Repo:** market-hunt (engine + simulator frontend)
**Status:** spec — approved through Section 2 in brainstorming; Sections 3–5 decided autonomously per user direction ("take best action").

## Problem

The existing strategy builder produces a **stateless set-membership filter**: `rules[]` of
`(metric, operator, threshold)`, AND-joined → a qualified set *per date* → momentum-select top-N →
equal/skewed rebalance (`run_strategy_simulation`, `api_server.py`). It structurally cannot express
the strategies that proved out best in research — the stateful **waves**:

- **mean_long level-wave** — 15.9% CAGR / Sharpe 1.23 / −8.1% DD, OOS-robust both halves (best
  risk-adjusted result of the research arc).
- **bull-wave** — 16.8% / 0.73 / −37%, first strategy to add value on Nifty 50.

These live only as standalone scripts (`bull_wave_backtest.py`, `meanlong_wave_backtest.py`,
`meanlong_wave_oos.py`) because the builder can't represent: cross-detection, metric-vs-metric
compares, OR-groups, per-stock state (accumulate/hold/liquidate-all), tranching, re-entry, fractional
liquidation.

**Goal:** make the *one* existing builder flexible enough to express **any** mean/truevx strategy —
both today's stateless filters and the stateful waves — without breaking the existing engine or its
golden-mastered numbers.

## Locked decisions (from brainstorming)

1. **Full scope** — must reproduce the proven stateful waves inside the unified builder, not just
   richer filters.
2. **Router behind one builder** — builder emits one config; engine dispatches by presence of a
   stateful block. Existing engine untouched; new engine for stateful configs.
3. **Fixed tranche + accumulate** — capital split into `k` equal tranches; each entry/accumulate fire
   buys one tranche (scale-in); exit liquidates (full or fractional) to cash; cash recycles. Idle cash
   (drag) is expected and surfaced.
4. **Three signal kinds** — `cross`, `compare`, `last_cross_dir`. AND within a group; groups OR-fire.
   Static threshold (today's filter) is subsumed as `compare`-vs-number.
5. **Progressive-disclosure UI** — keep today's flat rules panel as the default/simple case
   (unchanged); add a collapsible "Stateful / Wave" section. Touching it routes to the new engine.
6. **market-hunt simulator** — upgrade the existing full builder (`simulator/page.tsx`) in place;
   tvagent/frontend stays portfolio-focused.

Metrics throughout: `{mean_short, mean_mid, mean_long, truevx_score}`.

## Section 1 — Config model + router discriminator

`SimulationParams` (today, unchanged) gains exactly one optional field:

```
SimulationParams:
  ... all existing fields unchanged ...
  wave: Optional[WaveConfig] = None        # NEW — the only addition
```

**Router rule (the discriminator):** `wave is None` → existing `run_strategy_simulation`, byte-for-byte
as today. `wave` present → new `run_wave_simulation`. No mode flag/enum — **presence of the `wave`
block IS the route.**

```
WaveConfig:
  capital_N:   float                 # total capital
  tranches:    int                   # k → tranche size = N/k
  reentry:     bool
  buy_cadence: "daily"|"monthly"|"weekly"|"quarterly"   # when entry/accumulate are evaluated
  buy_date:    "first"|"mid"|"last"  # which day within the cadence period (ignored for daily)
  entry:       [SignalGroup]         # OR across groups; see tranche semantics below
  accumulate:  [SignalGroup]         # OR; empty ⇒ reuse entry groups; cap at k total tranches
  exit:        [SignalGroup]         # OR; liquidate per exit_mode
  exit_mode:   "full" | "fractional"

SignalGroup = [Signal]               # AND within a group
Signal =
  {kind:"cross",          metric, level:number, direction:"up"|"down", negate:bool=false}
  {kind:"compare",        lhs_metric, op:">"|"<"|">="|"<="|"=="|"!=", rhs:{metric}|{number}, negate:bool=false}
  {kind:"last_cross_dir", metric, grid:[number...], direction:"up"|"down", negate:bool=false}
```

**Wave evaluation semantics (precise — derived from the oracle scripts):**
- The engine maintains per-symbol daily cross-state: `up_crosses_today` / `down_crosses_today`
  (levels the metric passed through since the previous observed bar) and `last_cross_dir`
  (most-recent grid-cross direction). Updated every trading day for every ever-member.
- A **first observed bar** for a symbol has no previous value → no cross can fire on it.
- **`cross`** signal is true only on the day a level is passed through ("today"); it therefore
  requires `buy_cadence == "daily"` when used in entry/accumulate (validation error otherwise).
  `compare` and `last_cross_dir` are point-in-time/state and work at any cadence. `exit` is always
  evaluated daily, so `cross` is always allowed in exit.
- **Tranches bought = number of distinct firing groups** on a buy-cadence date: for a FLAT symbol,
  count entry groups that fire; for a HELD symbol, count accumulate groups that fire (entry groups if
  `accumulate` is empty). Buy that many tranches (each = N/k), cash permitting, capped at k total
  tranches per symbol. This reproduces the level-wave behavior where one large up-move through 40/50/60
  (three single-level groups) buys three tranches in a day.
- **`negate`** inverts a single signal's truth. Used by level-wave entry: each buy level is its own
  group `[cross(mean_long, up, L), cross(mean_long, up, 70, negate=true)]` — so a move that also
  crosses 70 blocks entry.
- **Exit** fires (liquidate per `exit_mode`) when any exit group is true, evaluated daily.

**Persistence:** `simulation_strategies` docs gain optional `wave`. Old docs load unchanged (field
absent → `None` → stateless route). Strategy/StrategyRule models keep `rules[]`; `wave` is additive.

**`rules[]` as pre-gate in wave mode:** in wave mode the flat `rules[]` filter acts as a **pre-gate on
the candidate universe** — a stock must pass `rules[]` to be eligible for entry signals. Enables "only
consider stocks with truevx>50, then run the wave on them." Empty `rules[]` → whole universe eligible.

## Section 2 — Stateful engine (`wave_engine.py`)

Pure function, **mirrors the existing sim's signature and output** so the current results page renders
it with no results-side frontend change:

```
run_wave_simulation(universe, membership, price_series,
                    indicator_series, params, wave) -> SimulationResult
```

Reuses existing PIT membership, `indicators` collection, price loaders, and the `build_price_series` /
`build_indicator_series` fast-path (per-symbol ascending arrays) from the momentum-calc optimization —
no new data layer.

**Per-stock state machine** (each eligible symbol independent):

```
            entry fires (& passes rules[] pre-gate)
   FLAT ─────────────────────────────────────────► HELD(1 tranche)
    ▲                                                   │
    │ exit:full                          accumulate fires│ buy +1 tranche
    │                                    (cap at k total) │ (scale-in)
    └──────────────── exit fires ◄───────────────────────┘
       full       → FLAT (all to cash)
       fractional → sell 1 tranche; FLAT only when last tranche out
   reentry=false  → terminal FLAT (no re-buy after first full exit)
```

**Capital mechanics:**
- `tranche = N / k`. A buy needs one free tranche of cash; **if cash < tranche, the buy is skipped**
  (logged). Book never exceeds N. This is the cash-drag, surfaced honestly.
- `cross` / `last_cross_dir` evaluated on the per-symbol ascending indicator array (need yesterday vs
  today to detect a cross) — exactly as the standalone scripts do, so numbers match.
- `max_holdings` (if set) caps concurrent names; when more entries fire than free slots, rank by
  `momentum_ranking` (reuses existing selector). Unset → unbounded, pure event-driven.

**Costs / PIT / benchmark:** reuse the existing brokerage model for fills, the existing PIT gate
(fail-loud if a universe lacks reconstitution), and the existing benchmark daily-return track.
Performance labeled **HYPOTHETICAL** (no real fills).

**Output:** identical `SimulationResult` — daily `portfolio_value` / `benchmark_value` / `holdings`,
transaction log (`added` / `exited` with tranche count per fill), `summary` (CAGR, Sharpe, maxDD,
alpha). Plus **wave diagnostics**: tranches deployed, % capital deployed over time (the drag),
take-profit vs stop exit counts.

**File boundaries (keep `api_server.py` churn minimal):**
- `wave_engine.py` — pure state machine + capital + output assembly.
- `wave_signals.py` — pure `cross` / `compare` / `last_cross_dir` evaluators, unit-tested in isolation.
- `wave_models.py` — `WaveConfig` / `SignalGroup` / `Signal` pydantic models.
- `api_server.py` — gains only the router branch + the optional `wave` field on existing models.

## Section 3 — API + persistence

**Single endpoint, presence-routed** (no parallel `/run-wave`): `POST /api/simulation/run` keeps its
contract and accepts the optional `wave` block; the handler dispatches per the router rule. One endpoint
matches the one-builder intent and keeps the async-job/polling pattern (already used for long
portfolio runs) applicable uniformly. Wave runs can be long → eligible for the same job pattern.

Strategy CRUD (`POST/PUT/GET /api/simulation/strategies`) accept and persist the optional `wave` field.
`GET` returns it when present. Backward-compatible: docs without `wave` behave exactly as today.

## Section 4 — Builder UI (`simulator/page.tsx`, progressive disclosure)

- Flat rules panel: **unchanged** — default/simple case, routes to existing engine.
- New collapsible **"Stateful / Wave"** section, collapsed by default:
  - **Entry / Accumulate / Exit** subsections. Each holds OR-groups; each group holds AND-ed signal
    rows. Controls: *add group* (OR), *add signal* (AND within group), *remove*.
  - **Signal row**: `kind` selector → fields. `cross`: metric · direction (↑/↓) · level. `compare`:
    lhs metric · op · rhs (metric **or** number toggle). `last_cross_dir`: metric · grid (multi-level
    chips) · direction.
  - **Capital panel**: `capital_N`, `tranches (k)`, `reentry` toggle, `exit_mode` (full/fractional),
    `buy_cadence` (daily/monthly/weekly/quarterly) + `buy_date` (first/mid/last, hidden when daily).
  - Each signal row has a `negate` (NOT) toggle.
- Routing is implicit: if the Stateful section has any signal content, the POSTed config includes the
  `wave` block → stateful route. Otherwise the existing flat path is sent unchanged.
- **Results page reused as-is**; wave diagnostics (deployed %, TP/stop counts) rendered in the existing
  summary area when present.

## Section 5 — Testing / golden master

- **Unit — `wave_signals`:** cross up/down at exact boundary (yesterday<level≤today etc.); compare
  metric-vs-metric and metric-vs-number for all 6 ops; last_cross_dir across a multi-level grid.
- **Unit — `wave_engine`:** state transitions (FLAT→HELD→accumulate-cap-at-k→exit full/fractional→
  reentry on/off, terminal-flat when reentry=false); capital (tranche skip when cash<tranche, cash
  recycle on exit, book never exceeds N); max_holdings slot contention ranked by momentum.
- **Golden master (load-bearing):** `run_wave_simulation` reproduces **level-wave 15.9% / Sharpe 1.23 /
  −8.1% DD** and **bull-wave 16.8% / 0.73 / −37%** from the standalone scripts. The scripts are the
  oracle; a differential test runs engine vs script on identical inputs and asserts byte-identical
  equity curves + summary.
- **Regression (stateless path):** a couple of existing saved strategies run through the upgraded
  endpoint (`wave=None`) and produce **identical** numbers to today — proves the router left the
  existing engine untouched.
- **Integration:** live-server `requests` smoke for one wave config end-to-end (marked `integration`,
  deselected by default per repo convention; env-configurable base).

## Non-goals (YAGNI v1)

- No general signal DSL — only the three kinds, fixed tranche rule, full/fractional liquidation.
- No `roc`/velocity signal (velocity refuted for forward returns in TrueVX research).
- No multi-asset, no intrabar, no in-UI optimization sweeps.
- No changes to tvagent/frontend.
- No rebuild of the existing engine (router keeps it intact by construction).

## Risks

- **Golden-master drift:** the engine must match the scripts byte-identical; the scripts are gross of
  costs in places — golden-master runs must mirror the scripts' exact cost/PIT settings, with a
  separate test for the costed path.
- **Cross-detection at universe edges:** a symbol entering the PIT universe mid-window has no
  "yesterday" — define first-bar behavior explicitly (no cross can fire on a symbol's first observed
  bar) and test it.
- **`api_server.py` size:** already large; keep new logic in `wave_*.py` modules, touch `api_server.py`
  only for the router branch + model field.

## Appendix — the two proven waves encoded as `WaveConfig`

These are the golden-master targets. `metric` abbreviations: `ml`=mean_long, `ms`=mean_short,
`tvx`=truevx_score.

**mean_long level-wave** (`meanlong_wave_backtest.py` → 15.9% / 1.23 / −8.1%):
```
capital_N=1_000_000, tranches=20, reentry=true, buy_cadence="daily", exit_mode="full"
entry = [   # one group per buy level; each blocked if the same move also up-crosses 70
  [cross(ml, up, 40), cross(ml, up, 70, negate=true)],
  [cross(ml, up, 50), cross(ml, up, 70, negate=true)],
  [cross(ml, up, 60), cross(ml, up, 70, negate=true)],
]
accumulate = []   # empty ⇒ reuse entry groups (same up-cross adds tranches while held)
exit = [          # OR: up-cross 70 (take-profit) OR down-cross of any {40,50,60,70} (stop)
  [cross(ml, up, 70)],
  [cross(ml, down, 40)], [cross(ml, down, 50)],
  [cross(ml, down, 60)], [cross(ml, down, 70)],
]
```

**bull-wave** (`bull_wave_backtest.py` → 16.8% / 0.73 / −37%):
```
capital_N=1_000_000, tranches=20, reentry=true, buy_cadence="monthly", buy_date="first", exit_mode="full"
entry = [  [compare(ml,">",50), compare(tvx,"<=",ml), compare(ms,"<=",ml)]  ]
accumulate = []   # empty ⇒ reuse entry: same monthly compare adds a tranche while held
exit = [  [last_cross_dir(ml, grid=[50,60,70,80,90], down), compare(tvx,">",ml), compare(ms,">",ml)]  ]
```

Both scripts are **gross of costs** with PIT membership on NIFTY50, N=1,000,000, START=2020-01-01,
END=2026-08-30, RF=0.06, 252-day annualization, `yrs=(END-START).days/365.25`. The golden-master test
runs the engine with these exact settings and asserts the **daily equity curve is byte-identical** to
each script's `*_equity.csv`, then asserts CAGR/Sharpe/maxDD (computed with the scripts' formulas) match
the published numbers.
