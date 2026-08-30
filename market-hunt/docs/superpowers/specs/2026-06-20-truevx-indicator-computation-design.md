# TrueVX Indicator Computation — Design

**Date:** 2026-06-20
**Repo:** market-hunt (prototype/stopgap)
**Goal:** Pre-compute and store the TrueVX relative-strength indicator for every priced
symbol, so the strategy simulator (Step 5) can read scores instead of recomputing them.

## Context

Price data is loaded and current to 2026-06-19: 849 distinct priced symbols
(502 Nifty 500 equities + 327 ETFs + 20 market-index series). The TrueVX engine
(`indicator_engine.calculate_truevx_ranking`) and storage (`IndicatorDataManager`,
`indicators` collection) already exist and the math is validated by real-data tests.
Only the **orchestration** of a full-universe run is missing/inadequate.

## Decisions (locked with user)

- **Indicator:** TrueVX (`truevx`), benchmark `base_symbol = "Nifty 50"`.
- **Parameters:** engine defaults — `s1=22`, `m2=66`, `l3=222`, `strength=2`,
  `w_short=0.5`, `w_mid=1.0`, `w_long=1.5`, `deadband_frac=0.02`, `min_deadband=0.001`.
- **Universe:** all priced symbols **except** the benchmark `Nifty 50` itself (degenerate
  RS) → 848 symbols.
- **Warmup / range:** feed prices from **2016-01-01** (≈2yr warmup covers the 222-day
  lookback *and* the 222-day SMA of `mean_long`); **persist only scores dated ≥ 2018-01-01**.
  This keeps all TrueVX outputs valid from 2018-01-01 while skipping the heavy 2005–2015 span.

## Approach (chosen: clean standalone runner)

Reuse the proven engine math; do not rewrite or vectorize it. Replace the buggy
`batch_indicator_processor` path (duplicate `__init__`, broken `count_indicator_data`,
per-symbol benchmark re-fetch) with a focused CLI.

New file: `scripts/compute_indicators.py`

1. Fetch the `Nifty 50` benchmark **once** (2016-01-01 → latest), convert to the dict
   shape the engine expects, hold in memory.
2. Resolve the universe = union of distinct symbols across `prices_*` collections, minus
   `Nifty 50`.
3. For each symbol:
   - Fetch its prices 2016-01-01 → latest (ascending).
   - `calculate_truevx_ranking(target, benchmark, **DEFAULTS)` (module-level standalone
     function — takes benchmark directly, no re-fetch).
   - Filter result rows to `date >= 2018-01-01`.
   - `IndicatorDataManager.store_indicator_data(symbol, "truevx", "Nifty 50", rows, params)`
     (deletes-then-inserts that symbol's series → idempotent re-run).
4. **Resumable:** skip a symbol already present in `indicators` for this
   `(indicator_type, base_symbol)` unless `--force`. Requires fixing
   `IndicatorDataManager.count_indicator_data` (references a non-existent
   `self.indicator_data_collection`; should be `self.db[self.indicators_collection]`).
5. Per-symbol `try/except` → collect failures, print a summary, support a `--symbols`
   retry list — same operational pattern as the price loads.

### CLI flags

- `--start-fetch 2016-01-01` (warmup floor), `--store-from 2018-01-01` (persist floor).
- `--symbols A,B,...` (subset / retry), `--force` (recompute existing), `--limit N` (smoke test).

## Storage (unchanged)

`indicators` collection, unique `(symbol, indicator_type, base_symbol, date)`. Each row holds
`truevx_score` (0–100), `structural_score`, `trend_score`, `mean_short/mid/long`, plus the
params used. Expected volume ≈ 848 × ~2,000 trading days ≈ 1.7M docs. Symbol-keyed, so the
scrip-code collision class fixed earlier does not apply here.

## Error handling

- A symbol with too few aligned bars (< max lookback, 222) yields `[]` from the engine →
  recorded as skipped-insufficient-data, not a hard failure.
- TV/Mongo/calc exceptions are caught per symbol, logged, and added to a failure list printed
  at the end for targeted retry. The run never aborts mid-universe on one bad symbol.

## Verification (evidence before "done")

- Spot-check one liquid symbol (e.g. RELIANCE): score series exists, `truevx_score` ∈ [0,100],
  earliest stored date ≥ 2018-01-01, `mean_long` non-null from 2018-01-01.
- Universe coverage: count symbols with ≥1 indicator row vs the 848 target; list any missing.
- Idempotency: re-running a single symbol produces the same row count (delete-then-insert).
- Light unit test: the date-filter (drops < 2018-01-01) and the resume/skip predicate.

## Out of scope

- Engine vectorization (only if the run proves too slow).
- ROC / momentum ranking signals (computed at simulation time, Step 5).
- Incremental daily top-up of indicators (later; this is the initial full build).
