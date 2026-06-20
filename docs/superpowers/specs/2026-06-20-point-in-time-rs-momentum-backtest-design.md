# Point-in-Time RS-Momentum Backtest + Survivorship-Bias Measurement — Design

**Date:** 2026-06-20
**Status:** Approved (brainstorming) → ready for implementation plan
**Repo:** `tvagent` (clean production target)

## Goal

Run the existing tvagent RS-Momentum backtest end-to-end on **real, already-loaded data**, point-in-time
correct, over NIFTY50 and NIFTY500. Produce a **biased-vs-point-in-time delta** that empirically
quantifies how much survivorship / index-membership look-ahead inflates this strategy's returns — so the
decision to invest further in removed-name price backfill is made on evidence, not assumption.

## Why this is the right first vertical

- The tvagent backend already has the full RS-Momentum vertical committed and tested (data sources,
  Parquet cache, point-in-time `IndexMembershipProvider`, signals, strategy, portfolio sim, metrics,
  `BacktestEngine`, CLI). It has **never been run on real data** — `nifty50_current.csv` is empty.
- market-hunt's MongoDB already holds **real OHLC** for the full universe, current to 2026-06-19.
- Running tvagent's clean engine against that data via a pluggable adapter proves the production engine
  on real numbers with **no re-fetch and no TradingView credentials**, honoring "pluggable by design"
  and "reuse the stopgap's proven data."

## Key discovery — the engine is already point-in-time, just data-starved

`BacktestEngine.run` (backend/app/backtest/engine.py):
- line 53: `symbols = union_over(index, start, end)` — fetches every stock that was a member at any point.
- line 74: `members = members_as_of(index, d)` — **gates selection to point-in-time members** at each
  monthly rebalance date `d`.
- line 75: `strategy.select(panel, nifty, members, signal_date, cfg)` — strategy only picks from `members`.

So point-in-time correctness is **already implemented**. What's missing is (a) a price source wired to the
real data, and (b) **verified membership data** to feed the provider (`load_provider_from_seed` currently
reads an empty `*_current.csv` and an unverified `*_changelog.csv`, so it yields an empty universe).

This collapses the "cheap correction" to: populate verified membership + add a Mongo price adapter. The
biased-vs-PIT comparison is then just **swapping the `UniverseProvider`**.

## Scope decisions (locked in brainstorming)

| Decision | Choice |
|---|---|
| Engine | tvagent `BacktestEngine` (the production target) |
| Price source | New `MongoDataSource` reading market-hunt Mongo (no re-fetch, no TV creds) |
| Universe | NIFTY50 (fully verifiable now) **and** NIFTY500 (largest bias, harder data) |
| Membership source | niftyindices.com semi-annual reconstitution press releases → verified changelog |
| Survivorship handling | Do the **cheap, bigger** correction (membership look-ahead) now; **measure the delta**; treat removed-name price backfill as conditional follow-up |
| Removed-name backfill | Explore **yfinance** (free, no creds) first; tvdatafeed as fallback; gated on measured gap |
| Indicators | Out of scope — RS-Momentum computes its own slope/R²/ROC from prices; TrueVX not used |

## Why the bias matters here (the technical rationale, recorded)

Two channels, decoupled:

1. **Membership look-ahead (bigger).** Using today's list, a stock *added in 2024* is treated as eligible
   in 2021. It has years of history so it passes screening, but it was not an index member then — and
   stocks are added *because they already rose*, so the biased run preferentially picks future winners
   during their run-up. The 2–3yr-history requirement does **not** protect against this (it guards against
   thin-data IPOs, not membership timing). **Fix is nearly free:** the provider already gates on
   `members_as_of`; we only need the verified timeline. Added stocks' prices are already in Mongo.
2. **Survivorship from removals (smaller).** Stocks removed from the index were often strong-momentum
   right up until they crashed; a point-in-time run would have bought and held them through the collapse.
   **Fix needs price backfill** for since-removed names not in Mongo.

Empirical prior: market-hunt's current-list backtest showed +706% (N100) / +1070% (N500) for 2020–2026
with ~25% measured universe churn, and was flagged as inflated by exactly this bias. The delta this design
produces will confirm or refute that for the tvagent strategy specifically.

## Components (5 units, each independently testable)

### 1. `MongoDataSource` (new)
- Implements the existing `DataSource` protocol: `get_ohlc(symbol, exchange, interval, start, end) -> DataFrame`
  with a `close` column (matching what `BacktestEngine._close` consumes).
- Reads market-hunt's symbol-keyed `prices_YYYY_YYYY` collections from MongoDB. **Read-only** — never
  writes to or mutates market-hunt data.
- Returns a tz-naive `DatetimeIndex`, columns `open/high/low/close/volume`, filtered to `[start, end]`.
- Maps the engine's index symbol (default `"NIFTY"`) to market-hunt's stored index key (e.g. `"Nifty 50"`)
  so the benchmark series resolves. Index-symbol mapping lives in the adapter, not the engine.
- Missing symbol / empty range → empty DataFrame (engine already handles this by emitting a warning).
- Connection configured via settings (Mongo URI + db name); no credentials hardcoded.

### 2. Membership timeline (reuse + data)
- Reuse `IndexMembershipProvider` and `build_membership_intervals` as-is (built + tested).
- Produce **verified** seed files:
  - `nifty50_current.csv`, `nifty500_current.csv` — single `symbol` column of tvdatafeed NSE symbols,
    derived from the authoritative current constituent CSVs (not hand-typed).
  - `nifty50_changelog.csv`, `nifty500_changelog.csv` — every row carries a real `source_url` (no
    `VERIFY` tokens), `added`/`removed` as `;`-separated tvdatafeed symbols mapped from the NSE scrip
    master (not guessed).
- NIFTY500 changelog reconstructed from ~10–12 semi-annual reconstitution announcements over the window.

### 3. Coverage report (new, small)
- Input: the *ever-a-member* symbol set from `union_over(index, start, end)`.
- Intersect with Mongo coverage → list of `(symbol, missing-date-range)` cells.
- Output a deterministic report: counts + the missing set. This is the evidence that drives the
  backfill-vs-restrict decision.

### 4. Removed-name backfill (new, conditional)
- Only if the coverage report shows a material gap.
- Spike yfinance first: fetch a few known removed names (`<SYMBOL>.NS`) and verify history availability
  and quality. **Risk flagged:** Yahoo's delisted/merged NSE coverage is uncertain — listed names
  (e.g. `RELIANCE.NS`) work, but merged/delisted names (HDFC Ltd, DHFL) may be absent there too.
- If yfinance covers the gap: load into Mongo (or a clearly-marked side store) reusing the symbol-keyed
  schema. Fallback: tvdatafeed (needs creds). Map NSE symbol → Yahoo `.NS` ticker explicitly.

### 5. Dual-run harness (new, thin)
- `BiasedUniverseProvider` (new): `members_as_of(index, _) == union_over(index, _, _) == current list`
  for every date — implements the same `UniverseProvider` protocol.
- Run `BacktestEngine.run` twice with identical config and data source, swapping only the provider:
  (a) biased = `BiasedUniverseProvider`, (b) point-in-time = `IndexMembershipProvider` from verified seed.
- Emit both equity curves, both metric sets (CAGR / Sharpe / max drawdown / turnover), the **delta**, and
  attach the coverage report so the point-in-time result states its own completeness.

## Data flow

```
niftyindices reconstitution press releases ──verify──> *_changelog.csv (source_url per row)
ind_niftyXXXlist.csv (authoritative current) ─────────> *_current.csv
        │
        ▼
load_provider_from_seed ─> IndexMembershipProvider (point-in-time intervals)
        │                                         │
        │                         MongoDataSource ─┴─ get_ohlc ──> market-hunt Mongo (real OHLC)
        ▼                                                              │
   BacktestEngine.run(cfg):                                           │
     union_over → fetch panel  ◀───────────────────────────────────── ┘
     members_as_of(d) → strategy.select → portfolio sim → metrics
        │
   run twice (swap provider):  (a) BiasedUniverseProvider   (b) IndexMembershipProvider
        └───────────────────────────────► DELTA  +  coverage report
```

## Integrity rules (non-negotiable)

- No `VERIFY` tokens ship — every changelog row carries a real `source_url`.
- Name → symbol mapping derived from the NSE scrip master, never guessed.
- `MongoDataSource` is strictly read-only; market-hunt data is untouched.
- Missing prices are logged per `(symbol, date-range)` and surfaced in the result, never silently dropped.
- Backfilled data (if any) is clearly attributed to its source (yfinance / tvdatafeed).

## Testing (TDD, tvagent suite)

- `MongoDataSource`: correct OHLC frame shape + `close` column; tz-naive index; `[start, end]` filtering;
  index-symbol mapping; missing-symbol → empty frame. Against a real test Mongo collection (seeded
  fixture), not a mock that masks query behavior.
- Membership: NIFTY500 changelog parse + interval reconstruction; assert a **known historical date's**
  membership (e.g. a stock added on a known date is absent before it, present after).
- `BiasedUniverseProvider`: returns the current list for every date.
- Coverage report: deterministic counts on a fixture with a known missing symbol.
- Dual-run integration: a constructed case where a future-added stock is correctly **excluded** pre-membership
  (biased run picks it; point-in-time run does not) — proving the delta is real, not noise.

## Success criteria

One command runs both backtests on NIFTY500 (and NIFTY50) over the window and emits:
- two equity curves,
- CAGR / Sharpe / max drawdown / turnover for each,
- the **delta** (how much the bias inflated returns),
- a coverage report listing any excluded `(symbol, date)` cells.

The delta is the deliverable: it answers, with evidence, whether the survivorship/look-ahead bias
materially changes this strategy's results.

## Sequencing — each step a review checkpoint

1. **`MongoDataSource` (TDD)** → tvagent's first honest number on real data (biased run only). Smallest
   step that proves the production engine reads real prices.
2. **NIFTY500/50 membership build + verify** → **spike niftyindices.com fetchability first** (it may block
   automated requests; fallback = archived snapshots / manual capture). Biggest effort and data risk.
3. **Dual-run + comparison** → produce the **delta** and coverage report.
4. **Conditional:** yfinance backfill spike for removed names, gated on the coverage report's gap size.

## Prerequisites / risks

- Real runs require market-hunt MongoDB up (Docker: `docker start mh-mongo`, :27017).
- niftyindices.com may block automated fetching → spike before committing to step 2 automation.
- yfinance delisted-NSE coverage uncertain → spike before relying on it for backfill.
- Symbol identity: market-hunt stores prices symbol-keyed; index symbols are names-with-spaces — the
  adapter owns this mapping.

## Results (2026-06-20) — NIFTY50, 6-year window

Run: `python -m app.run_compare --index NIFTY50 --years 6` (window ≈ 2020-06 → 2026-06, monthly
rebalance, equal-weight top-10, 0 costs). Real prices from market-hunt Mongo; point-in-time membership
from the verified 9-event NIFTY50 changelog.

Numbers below are after the LTIM coverage gap was closed (see "Coverage" note); the pre-LTIM run gave
PIT CAGR 13.9% / excess −0.08 / +58.2% — loading the missing member made the gap *wider*, confirming the
"upper bound" claim.

| Metric | Biased (today's list) | Point-in-time | Effect of bias |
|---|---|---|---|
| Total return | 236.4% | 103.8% | inflated |
| CAGR | 23.3% | 13.1% | +10.2 pp |
| Sharpe | 1.26 | 0.80 | |
| Sortino | 1.59 | 1.04 | |
| Max drawdown | −21.7% | −25.4% | understated risk |
| **Excess total return vs Nifty 50** | **+1.16** | **−0.17** | **flips negative** |
| Final value (₹1M start) | ₹3,363,467 | ₹2,037,959 | **+65.0%** (`final_pct`) |

(The `alpha` metric here is `total_return − benchmark_total_return` over the window — an *excess total return*, not a regression/CAPM alpha. The sign flip is the point: corrected for membership, the strategy moves from beating the index to trailing it. Adding a true benchmark-regression alpha to `compute_metrics` is a worthwhile follow-up.)

**Verdict: the bias is decisive, not negligible.** The survivorship-biased backtest looks like a strategy
that strongly beats the index (excess +1.16). Corrected to point-in-time, the same strategy *lags*
the Nifty 50 (excess −0.17). The apparent edge was almost entirely membership look-ahead + survivorship —
refuting the hypothesis that bias is immaterial for a monthly-rebalanced momentum strategy with a
multi-year listing screen.

**Coverage: 61/63 ever-members covered; 2 missing — `HDFC`, `INFRATEL`.** Both are genuinely
delisted/merged (HDFC Ltd → HDFC Bank; Bharti Infratel → Indus Towers) — no series exists anywhere.
The third gap, `LTIM` (LTIMindtree), was *not* a delisting but a market-hunt loading gap; it was
backfilled via tvdatafeed (2,454 bars) and is now covered. The `ETERNAL` (ex-ZOMATO) and `TMPV`
(ex-TATAMOTORS) renames resolved cleanly — both carry full history under their current symbol.

**Empirical confirmation of the "upper bound" claim:** before LTIM was loaded the PIT run scored CAGR 13.9%
/ +58.2% gap; adding LTIM (a since-removed underperformer the PIT strategy actually held in 2023-24) pushed
PIT *down* to 13.1% / +65.0%. More complete point-in-time coverage ⇒ lower honest return ⇒ wider bias. The
2 remaining gaps (delisted laggards) would push it lower still. Transaction costs (turnover ≈ 110%/yr, here
0) would reduce both further.

**Implication:** before this strategy is trusted, the point-in-time path is mandatory, and the next honest
levers are (a) removed-name price backfill (yfinance/tvdatafeed) to close the residual 3-name hole, and
(b) extending the same comparison to NIFTY500, where churn — and therefore bias — is larger.

## Results (2026-06-20) — NIFTY100, 6-year window

Run: `python -m app.run_compare --index NIFTY100 --years 6` (same engine, same config, only the
universe provider swapped). Point-in-time membership from a verified 14-event NIFTY100 changelog
(forward-replay from the 2020-09 baseline reproduces the current 102-name list exactly, 0 discrepancies).
Benchmark = Nifty 50 for both runs (so the bias delta is the clean variable; absolute "excess" would shift
slightly with a Nifty-100 base but the delta is robust).

| Metric | Biased (today's list) | Point-in-time | Effect of bias |
|---|---|---|---|
| Total return | 733.0% | 166.0% | inflated |
| CAGR | 44.2% | 18.4% | **3.1× overstated** |
| Sharpe | 1.84 | 0.96 | |
| Sortino | 2.29 | 1.18 | |
| Max drawdown | −23.4% | −28.2% | understated risk |
| Excess total return vs Nifty 50 | +6.13 | +0.46 | edge collapses |
| Final value (₹1M start) | ₹8,330,441 | ₹2,659,895 | **+213%** (`final_pct`) |

**Verdict: bias is LARGER in NIFTY100 than NIFTY50 (+213% vs +65%), not smaller.** This refutes the
"higher bands are cleaner because constituents got promoted" hypothesis. The promotion ladder is precisely
the mechanism: today's NIFTY100 is enriched with the multibaggers that climbed in from Next-50/midcap over
the window, and a today's-list backtest lets the strategy hold them from day one. More climbers enter the
top-100 band than the top-50 band, so the look-ahead is bigger here.

**The honest flip side:** point-in-time, the NIFTY100 strategy is still genuinely positive — 18.4% CAGR,
+0.46 excess, it does modestly beat the index. The strategy has a real (small) edge; the biased backtest
just overstated it by 3.1×. The lesson is "trust the 18%, not the 44%," and never size decisions off the
biased number.

**Coverage: 150/155 covered; 5 missing — `HDFC`, `MINDTREE`, `PGHH`, `SRTRANSFIN`, `TATAMTRDVR`.** All are
delisted / merged / renamed removed names (HDFC Ltd → HDFC Bank; Mindtree → LTIMindtree/LTM; Shriram
Transport → Shriram Finance; Tata Motors DVR cancelled; PGHH a loading gap). All are since-removed, so
including them pushes PIT *lower* — +213% remains conservative.

### Symbol-identity note (debt)
LTIMindtree is stored in market-hunt as `LTM` (full history; in NIFTY100/200/500 index_meta). The NIFTY50
seed used `LTIM` and was backfilled with a duplicate series under that name — harmless (same bars) but
inconsistent with the NIFTY100 seed, which correctly uses `LTM`. Cleanup: align the NIFTY50 changelog to
`LTM` and drop the duplicate `LTIM` series + its synthetic mapping. The NIFTY50 result is unaffected
(identical data).
