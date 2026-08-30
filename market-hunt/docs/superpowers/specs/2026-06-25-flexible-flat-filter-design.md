# Flexible Flat Filter (wave-parity rule kinds for the stateless engine) — Design

**Date:** 2026-06-25
**Repo:** market-hunt

## Problem

The stateless strategy filter only supports `(metric, operator, threshold)` rules, AND-joined, with the
RHS always a number. The wave builder is far more expressive (compare metric-vs-metric, last_cross_dir,
OR-groups). Users want to build stateless (non-wave) strategies "from all angles" with the same vocabulary.

## Decisions (from brainstorming)

1. **Full wave-parity, OR-groups.** Flat rules become signal **groups**: AND within a group, OR across
   groups — reusing the `wave_models` / `wave_signals` machinery.
2. **Kinds = `compare` (vs number OR metric) + `last_cross_dir`** (+ `negate`). The daily-edge `cross`
   is NOT offered in the stateless filter (it only fires on the exact crossing day → meaningful only
   with dynamic rebalance; stays a wave-only concept). `compare` and `last_cross_dir` work at any cadence.
3. **Additive + presence-routed.** New optional `filter` block; legacy `rules[]` path is byte-identical
   and untouched (golden numbers safe by construction).

## Section 1 — Config model + routing

`SimulationParams` and `Strategy` gain:
```
filter: Optional[List[List[Signal]]] = None   # NEW; Signal reused from wave_models
```
- `Signal` = the existing `wave_models.Signal` (cross/compare/last_cross_dir + negate). For the filter UI
  only compare/last_cross_dir are produced, but the model accepts the full type.
- Persisted on the strategy doc alongside `rules` and `wave` (additive; old docs load fine).
- **Routing:** in `run_strategy_simulation`, if the strategy/params carry a non-empty `filter` → qualify
  via the rich evaluator; else → legacy `apply_strategy_rules(strategy["rules"])` UNCHANGED. The handler
  injects `params.filter` onto the strategy doc when an ad-hoc filter is sent (UI runs saved strategies).

## Section 2 — Engine (reuse `wave_signals`)

The stateless daily loop (`api_server.py:4604`, runs every trading day) maintains per-symbol
`CrossState` (from `wave_signals`) when a filter is present, and replaces the qualification step:

```
# setup (once, when filter present): parse raw filter dicts → [[Signal,...],...];
#   collect (metric, tuple(grid)) pairs for last_cross_dir; cross_states = {} (lazy per symbol)
# per day, replacing apply_strategy_rules:
def qualify_by_filter(day_indicators, filter_groups, cross_states, grids):
    qualified = []
    for symbol, stock_data in day_indicators.items():
        cs = cross_states.setdefault(symbol, CrossState(grids))
        prev = cs.prev_snapshot()
        cs.update(stock_data)                      # advance daily state (last_cross_dir)
        if any_group_fires(filter_groups, stock_data, prev, cs.last_dirs):
            qualified.append(stock_data)
    return qualified
```
- `group_fires` = AND within group; `any_group_fires` = OR across groups; `negate` honored; missing
  metric → 0.0 (same evaluator the wave engine uses). `stock_data` already holds the metric keys.
- CrossState updates **every day** (qualification runs daily), so `last_cross_dir` is current at any
  rebalance date. A symbol entering the PIT universe mid-window starts cross-state at its first observed
  bar (no cross on bar one).
- Everything downstream (PIT gate, momentum select, equal/skewed rebalance, charges, benchmark, results)
  is unchanged — only *which stocks qualify* changes.
- **Golden-master safety:** `filter is None` → original `apply_strategy_rules`, zero changes.

## Section 3 — UI (reuse `SignalGroupEditor`)

- Generalize the wave's `SignalGroupEditor` with an `allowedKinds` prop. The wave keeps
  `[cross, compare, last_cross_dir]`; the filter uses `[compare, last_cross_dir]`.
- The create/edit form's **"Strategy Rules"** section becomes a **"Filter conditions"** signal-group
  editor (compare + last_cross_dir, OR-groups). The old `(metric,op,threshold)` row = a `compare`-vs-number
  signal — nothing lost.
- `buildFilterBlock()` serializes the editor → `filter` (or `undefined` if empty); attached to save like
  the wave block.
- **Edit hydration / legacy conversion:** if the strategy has `filter` → hydrate the editor from it;
  else if it has legacy `rules[]` → convert each rule to a `compare`-vs-number signal in one AND-group
  and show that (mathematically identical). Save emits `filter`.
- **Save validity:** name + (filter has ≥1 signal **or** wave has ≥1 entry signal).

## Section 4 — Testing (all via live API, `integration`-marked)

- **Equivalence (load-bearing):** a strategy with `rules=[mean_long>50, mean_long<60]` and one with
  `filter=[[compare(mean_long>50), compare(mean_long<60)]]` produce an **identical** summary — proves the
  rich path reproduces `apply_strategy_rules` for the simple case (legacy→filter conversion is safe).
- **Legacy regression:** an existing `rules`-only strategy run by id is byte-identical to today.
- **New capability:** a `filter` with metric-vs-metric (`truevx < mean_long`) and one with `last_cross_dir`
  each run end-to-end and return a sane summary (200 + expected keys).

## Non-goals

- No daily-edge `cross` in the stateless filter (wave-only).
- No change to the wave engine or the legacy `apply_strategy_rules`.
- No new evaluator module — reuse `wave_signals`.
