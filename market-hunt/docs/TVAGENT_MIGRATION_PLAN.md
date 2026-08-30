# tvagent Migration — Build Plan (Data Layer First)

> **Handoff doc.** This is the reference for migrating the proven market-hunt prototype into the
> production tvagent system. Read `RESEARCH_LOG.md` first (the *why* — what strategy we proved);
> this doc is the *how* (what we build). A fresh session should start here.

---

## 0 · What we proved (the reference — see RESEARCH_LOG.md for full detail)

The validated edge, point-in-time and survivorship-free:

- **Strategy = the "aligned-stack" gate:** `truevx_score>90 & mean_short>80 & mean_mid>60 & mean_long>50`,
  hold-all or top-K by short-horizon (20–22d) price ROC, monthly equal-weight rebalance.
- **Universe = point-in-time NIFTY500 ∪ Microcap250** (the broad 750), reconstructed from dated
  index changelogs — no survivorship.
- **Honest expectations (in-sample 2022–26, net):**
  - Reference portfolio **10 Large + 10 Mid + 15 Small&Micro (35 stocks):** ~**23% CAGR / Sharpe 1.07 / −25% maxDD** (≈3.5× Nifty). Lumpy: +43%/+59% in 2023–24 bull, −10% in 2025.
  - Tier truth: **Mid + Micro are the engines; Small is the dead zone; cap Micro by liquidity.**
    Optimal tilt ≈ **45% Mid / 30% Micro / ~22% Large / ~0–5% Small.**
  - Concentrated micro (top-10) hits ~35% but is illiquid / high-DD / overfit-prone — *not* the base case.
- **Hard lessons baked in:** non-PIT inflates returns +7–14 CAGR pts (survivorship); the strength
  gate structurally avoids dead/delisted names (so the price-coverage gap doesn't bias it); all
  numbers are smallcap-bull-flattered and need OOS / live confirmation.

---

## 1 · Storage decision: Postgres + Parquet (migrate off Mongo)

Aligns with tvagent's target stack (Postgres + Parquet). Rationale + split:

| data | store | why |
|---|---|---|
| indices, current constituents, **reconstitution changelog**, symbol mappings, stock metadata | **Postgres** | small, relational, **point-in-time-integrity-critical** (constraints, transactions) |
| prices (OHLC), indicators (TrueVX) — large time-series | **Parquet** (partitioned symbol/year) + Postgres catalog | columnar bulk-scan = fast universe backtests (Mongo per-symbol queries were the bottleneck) |

Migrate **piece by piece**; run Mongo in parallel until each piece is cut over. Aligns with the
existing `docs/superpowers/plans/2026-06-18-api-postgres-persistence.md` — connect to it.

---

## 2 · Phase 1 — Data Layer (BUILD FIRST)

The data layer has four services. We **prototyped all four** this session (scripts listed); Phase 1
hardens them into tvagent with Postgres+Parquet, TDD, point-in-time-correct.

### 1a. Index Metadata Loader (constituents) — *the first concrete build*
- **Input:** niftyindices constituent CSV URLs (auto-refreshable), e.g.
  `…/IndexConstituent/ind_niftymicrocap250_list.csv`, `…ind_nifty500list.csv`, etc.
- **Does:** fetch (note: niftyindices host blocks WebFetch from sandbox — use `urllib`/server-side
  fetch; it works), parse, upsert current constituents per index into Postgres `index_meta`.
- **Auto-refresh:** scheduled re-pull so current lists stay current.
- Prototype: current lists already in Mongo `index_meta` (NIFTY100/200/500, Midcap150, Smallcap250,
  **Microcap250**, TotalMarket, etc.).

### 1b. Reconstitution Loader (point-in-time changelog) — *integrity spine*
- **Input:** niftyindices semi-annual review press-release PDFs (`…/Press_Release/ind_prs<DDMMYYYY>.pdf`).
- **Does:** fetch via `urllib` + parse with `pypdf`, extract per-index added/removed lists + effective
  date, upsert into `index_reconstitution`. Validate by replaying backward → reproduce current list.
- Prototype: `scripts/parse_pr_microcap.py`, `scripts/capture_microcap_reconstitution.py`,
  `scripts/load_reconstitution.py`, doc `docs/capture_index_rebalance.md`. PIT engine: `membership.py`
  (`load_membership`/`members_as_of`/`union_over` — replays changelog backward from anchor).
- **State:** reconstitution loaded for NIFTY50/100/200, Midcap150, Smallcap250, **Microcap250 (2022+)**.
  Microcap250 launched ~2021 → only 2022+ is valid. Combined **NIFTY500MICRO750** index materialized
  (`scripts/materialize_broad_universe.py`, replay-validated exact).

### 1c. Price Dataload (tvfeed)
- **Does:** download full-history daily OHLC from TradingView via tvdatafeed → Parquet (was Mongo
  `prices_YYYY_YYYY`). **Anonymous tvfeed gives full history** (pass large `n_bars`, e.g. 5000) — no
  creds needed; **delisted symbols return nothing** (accepted residual gap; for full integrity later,
  source NSE bhavcopy which includes delisted names).
- **Gotcha:** loader requires a `symbol_mappings` row (insert synthetic if absent); Windows console
  needs `PYTHONIOENCODING=utf-8` (loader prints emojis → cp1252 crash).
- Prototype: `DataLoadManagement.py` (CLI `download-stock <SYM> --force-refresh`),
  `stock_data_manager.py`, `tv_price_client.py`, batch `scripts/microcap_fix_and_load.py`.
- **State:** ~594/631 ever-microcap members loaded; N500 fully loaded.

### 1d. Indicator Compute (TrueVX)
- **Does:** compute TrueVX (params s1=22/m2=66/l3=222, benchmark "Nifty 50") per symbol → store.
- Prototype: `scripts/compute_indicators.py` (`--symbols A,B,C`), `indicator_engine.py`.
- **State:** computed for ~1282 symbols (N500 + microcaps).

---

## 3 · Phase 2+ (after data layer)

1. **Strategy/backtest engine** — port the aligned-stack gate + top-K-by-ROC + monthly-EW simulator,
   point-in-time, net-of-cost. **Fix the engine's cost model** (it charges a flat 0.5 turnover
   *estimate* — flatters high-turnover books; charge *realized* turnover).
2. **Analytics/dashboard** — reuse market-hunt Next.js frontend; add proper universe selection
   (current dropdown is a fixed list; backend already accepts any index w/ reconstitution).
3. **Validation harness** — walk-forward + OOS (the in-sample numbers need live/OOS confirmation).
4. Later: alerts → live execution → multi-user.

---

## 4 · Prototype scripts (this session) — the working reference to port

| layer | scripts |
|---|---|
| Index metadata / reconstitution | `parse_pr_microcap.py`, `capture_microcap_reconstitution.py`, `materialize_broad_universe.py`, `load_reconstitution.py`, `membership.py` |
| Dataload / indicators | `microcap_load_batch.py`, `microcap_fix_and_load.py`, `microcap_feasibility.py`, `compute_indicators.py` |
| Strategy / backtest research | `aligned_stack_strategy.py`, `walkforward_champion.py`, `broad_universe_pit.py`, `decisive_matrix.py`, `tier_allocation_optimize.py`, `index_tier_topk.py`, `whole_universe_exact.py`, `combined_portfolio.py` |
| Integrity / survivorship | `data_integrity_audit.py`, `pit_vs_nonpit.py`, `pit_excess_source.py`, `riser_origin.py` |

---

## 5 · How to start the next session

1. Read `RESEARCH_LOG.md` (findings) + this doc (build plan).
2. Use **brainstorming → writing-plans** to detail **Phase 1a (Index Metadata Loader on Postgres)**
   as the first TDD task set.
3. Storage: Postgres for metadata/membership, Parquet for time-series; migrate piece-by-piece,
   Mongo in parallel until cut over.
4. Keep every number point-in-time and survivorship-free — that's the whole point.
