# RS-Momentum Backtest — Production Vertical Slice (Sub-Project 1)

**Date:** 2026-06-18
**Status:** Approved design — pending spec review
**Project:** tvagent — production-grade quant research + trading platform
**Scope:** Sub-project 1 of a larger platform (see "Program Context")

---

## 1. Program Context

tvagent is a production-grade quantitative finance platform combining a research/backtesting
lab and a full platform (dashboard, alerts, eventual live execution + multi-user). It is built
**sub-project by sub-project**, each a production-quality vertical slice, on a stable architecture
designed ahead of its future.

**Full architecture (layers, bottom-up):**

```
UI · glass dashboard
API layer
Alerts / Scheduler        (future — designed-for, not built in SP1)
Execution / broker        (future — designed-for, not built in SP1)
Backtest engine           ← SP1
Strategy layer            ← SP1
Data layer (sources, cache, universe/constituents)  ← SP1
```

**Build order:** SP1 (this spec) → SP2 dashboard polish → persistence depth → scheduler/alerts →
live execution → multi-user. Each later layer attaches to `Strategy` + `BacktestEngine` via the
interfaces defined here, without rework.

## 2. Goal of Sub-Project 1

Validate the **RS-Momentum** strategy end-to-end, to production standard, as a full vertical slice:
**data → strategy → backtest → API → glass dashboard**. No throwaway code. Known correctness
issues (notably survivorship bias) are closed at the source from the start.

## 3. The RS-Momentum Strategy (exact specification)

1. **Universe:** point-in-time members of a chosen index (default **Nifty50**) over the backtest
   window. Survivorship-bias-free — see §5.
2. **Series:** daily **close** prices, ~5 years history, for every stock that was ever a member in
   the window (the *union universe*) plus the **NIFTY 50 index** level.
3. **RS line:** `RS_i(t) = close_i(t) / NIFTY(t)` for each stock `i` (rising RS ⇒ stock outperforming index).
4. **Strength + consistency score** over trailing **22 trading days**, computed per stock at each
   rebalance:
   - Fit OLS linear regression of `RS_i` over the 22-day window → **slope** and **R²**.
   - **Gate:** discard any stock with `slope ≤ 0` (falling RS cannot be "strong").
   - **Rank-blend** across the surviving stocks in the universe:
     `score_i = 0.5 · rank(slope_i) + 0.5 · rank(R²_i)` (higher rank = stronger / smoother).
   - Rank-based normalization (scale-free, outlier-robust) — chosen over z-score/min-max.
5. **Top 50%:** keep the top 50% of the *index's nominal size* by `score` (e.g. top 25 for Nifty50).
   The "50%" is of the index's standard constituent count, applied after the slope gate.
6. **Top 10 by ROC:** from those survivors, compute **22-day stock-price ROC**
   `ROC_i = close_i(t) / close_i(t-22) − 1`; select the **10 highest**.
7. **Portfolio:** equal-weight the 10 selected stocks (10% each), fully invested.
8. **Rebalance:** first trading day of each calendar month. Signals computed on the prior close;
   positions held until the next rebalance.
9. **Costs:** configurable transaction cost (brokerage + slippage); **default 0** for v1, fully
   plumbed so non-zero costs require no code change.

**Edge cases:** a stock lacking 22 trading days of history at a rebalance (newly listed, or not yet
a member) is excluded from ranking that month. If fewer than 10 stocks pass all filters, hold the
available count and record a run warning (never silently pad).

## 4. Defaults (confirmed)

| Item | Default |
|---|---|
| Rebalance timing | First trading day of each calendar month; signals on prior close |
| Weighting | Equal-weight 10 stocks (10% each), fully invested |
| Starting capital | ₹10,00,000 (configurable) |
| Transaction costs | Configurable; default 0 (plumbed in) |
| Lookback | 22 trading days; ~5 years daily history |
| Index series | NIFTY 50 index level via tvdatafeed (`NIFTY`, NSE) |
| Default universe | Nifty50 (provider also serves Nifty100, Nifty500, NiftySmallCap) |

## 5. Survivorship Bias — Closed at the Source

Using *current* constituents over history is biased. SP1 instead resolves **point-in-time index
membership**.

- **`index_membership`** table (versioned timeline): `index, symbol, effective_from, effective_to`.
  Membership as-of any date, and the union over any window, are clean queries.
- **`UniverseProvider`** is **multi-index by parameter** — a single `IndexMembershipProvider`
  serves **Nifty50, Nifty100, Nifty500, NiftySmallCap** simultaneously (and any future index added
  to the table). It is *not* swapped out per index; the index is an argument. This lets multiple
  portfolios/strategies be tested across different universes.
- **Seed data:** committed change-log per index (effective date, added, removed) **with a source URL
  per row**, plus a fetcher that keeps *current* membership fresh. The *union universe* for a
  backtest = every stock that was ever a member in the window (e.g. for Nifty50 this includes
  removed names such as Yes Bank, Vedanta, GAIL, IOC, Shree Cement, HDFC Ltd, UPL, Divi's Labs,
  LTIMindtree, BPCL, Britannia, Hero MotoCorp, IndusInd, Zee).

**Change-log verification (production bar):** the draft Nifty50 5-year change-log (below) was
compiled from secondary sources (Wikipedia, enrichmoney) and contains **known conflicts**. Before
the seed is committed, **every row must be verified against the authoritative source**
(`niftyindices.com` press releases / `archives.nseindia.com` circulars). No unverified row ships;
unverifiable rows are flagged, not guessed.

**Draft Nifty50 change-log (TO BE VERIFIED against niftyindices.com before commit):**

| Effective | Added | Removed | Note |
|---|---|---|---|
| 2020-09-25 | SBI Life | Zee Entertainment | both sources agree |
| 2021-03-31 | Tata Consumer Products | GAIL | agree |
| 2022-03-31 | Apollo Hospitals | Indian Oil (IOC) | agree |
| 2022-09-30 | Adani Enterprises | Shree Cement | agree |
| 2023-07-13 | LTIMindtree | HDFC Ltd (merged into HDFC Bank) | sources swap direction — verify (HDFC removed is believed correct) |
| 2024-03-28 | Shriram Finance | UPL | agree |
| 2024-09-30 | Bharat Electronics, Trent | Divi's Labs, LTIMindtree | agree |
| 2025-03-28 | Jio Financial, Eternal (= Zomato, renamed) | BPCL, Britannia | agree (name variant) |
| 2025-09-30 | IndiGo, Max Healthcare | Hero MotoCorp, IndusInd Bank | agree |

⚠ One secondary source injected a spurious 2023-09-30 event and an extra 2020-09-25 Bharti
Infratel/Divi's change not corroborated by the other — treated as suspect, to be confirmed/rejected
during verification. (Earlier 2020 events — Shree Cement/Yes Bank 2020-03-19, HDFC Life/Vedanta
2020-07-31 — are pre-window for a 5yr backtest started in 2026 but should be captured for accuracy.)

## 6. Repository Layout (monorepo)

```
tvagent/
  backend/                 # Python 3.12, FastAPI
    app/
      core/                # settings (pydantic-settings), logging, errors
      data/
        sources/           # DataSource interface + TvDatafeedSource
        universe/          # UniverseProvider + IndexMembershipProvider
        cache/             # Parquet read-through OHLC cache
        repository/        # Postgres (SQLAlchemy + Alembic migrations)
      strategies/          # Strategy interface + RSMomentumStrategy (+ pure fns)
      backtest/            # engine, portfolio, metrics
      api/                 # FastAPI routers
      schemas/             # Pydantic DTOs
    tests/                 # unit / integration / api
    pyproject.toml
    alembic/
  frontend/                # Next.js + React + Tailwind (glass theme)
    app/  components/  lib/
  docker-compose.yml       # postgres + backend + frontend
  docs/superpowers/specs/
```

## 7. Core Interfaces (swappable, independently testable)

| Interface | Contract | v1 impl |
|---|---|---|
| `DataSource` | `get_ohlc(symbol, exchange, interval, start, end) → DataFrame` | `TvDatafeedSource` |
| `UniverseProvider` | `members_as_of(index, date)` · `union_over(index, start, end)` | `IndexMembershipProvider` (multi-index, point-in-time) |
| `CacheStore` | read-through OHLC, Parquet columnar | `ParquetCache` |
| `Strategy` | `select(price_panel, as_of) → target_weights` | `RSMomentumStrategy` |
| `BacktestEngine` | walk rebalances → portfolio sim → results | single impl |
| `MetricsCalculator` | results → performance metrics | single impl |

**Strategy pure functions (each unit-tested in isolation):** `rs_line`, `rolling_slope_r2`,
`rank_blend_score`, `apply_slope_gate`, `select_top_pct`, `roc`, `select_top_n`.

## 8. Data Flow

```
config → API → BacktestEngine
  → UniverseProvider.union_over(index, window)        # point-in-time, bias-free
  → Cache/DataSource: OHLC for union universe + NIFTY  # read-through, minimizes feed calls
  → for each monthly rebalance: Strategy.select(panel, as_of)
  → Portfolio sim: daily mark-to-market, apply costs at rebalance
  → MetricsCalculator
  → persist (Postgres) → API → glass dashboard
```

## 9. Persistence Split

- **Parquet** — OHLC timeseries (fast columnar, cheap to rebuild from source).
- **Postgres** — relational state/results: `instruments`, `index_membership`, `backtest_runs`,
  `backtest_positions`, `equity_curve`, `metrics`. SQLAlchemy + Alembic.

## 10. API (FastAPI)

| Method · Path | Purpose |
|---|---|
| `POST /backtests` | Run with config (universe, dates, capital, costs, lookback, top-N) → `run_id`, async |
| `GET /backtests/{id}` | Status + summary |
| `GET /backtests/{id}/equity` | Equity curve (+ benchmark) |
| `GET /backtests/{id}/holdings` | Per-rebalance holdings |
| `GET /backtests/{id}/metrics` | Performance metrics |
| `GET /universe/{index}?as_of=` | Point-in-time members |
| `POST /data/sync` | Refresh OHLC / membership |

Long runs via FastAPI BackgroundTasks now; designed to move to a queue (Celery/RQ) later without
contract change. Config validated by Pydantic (422 on invalid).

## 11. Glass Dashboard (Next.js + React + Tailwind)

Frosted/backdrop-blur panels, dark base, subtle gradient, restrained neon accent. Components:
run-config panel · equity curve **vs NIFTY benchmark** · metric cards (CAGR, Sharpe, Sortino,
MaxDD, turnover, hit-rate) · per-rebalance holdings timeline. Full aesthetic polish is finalized
via the `frontend-design` skill; the glass shell + working views ship in SP1.

## 12. Metrics

Total return, CAGR, annualized volatility, Sharpe, Sortino, max drawdown, turnover, hit-rate, and
benchmark-relative performance (alpha vs NIFTY 50).

## 13. Error Handling

- Data gaps / missing symbol-date → skip + record run warning (never silent).
- tvdatafeed auth / rate-limit → retry with backoff; read-through cache minimizes calls.
- Insufficient lookback (newly listed) → excluded from that rebalance's ranking.
- Invalid config → Pydantic 422.
- All anomalies surfaced in run `warnings[]`.

## 14. Testing (TDD)

- **Unit:** every pure strategy function against deterministic synthetic fixtures.
- **Engine:** portfolio simulation against a hand-computed equity curve (known inputs → known output).
- **Integration:** full backtest smoke on a small cached sample OHLC set.
- **API:** endpoint contract tests.
- **Frontend:** component + smoke tests.
- **Data policy (org rule):** synthetic/anonymized fixtures only — no real PII, no secrets, no
  confidential data in tests.

## 15. Config & Secrets

`pydantic-settings` + `.env`. tvdatafeed credentials and all secrets via environment variables only,
never committed; sample files use placeholders. Connection strings/keys never logged.

## 16. Deferred but Designed-For

Live execution / broker abstraction and scheduler / alerts are **not** implemented in SP1 but the
`Strategy` and `BacktestEngine` interfaces are shaped so they attach later without rework.

## 17. Out of Scope (SP1)

Live/paper trading, scheduler/alerts, multi-user/auth, intraday data, options/derivatives,
non-NSE markets, persistence of OHLC in Postgres (Parquet handles it).
