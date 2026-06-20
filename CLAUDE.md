# tvagent — Quantitative Trading Terminal

## Vision (the north star)

A production-grade quant terminal where **research you can trust becomes trading you can run** — the
same engine that backtests a strategy honestly is the one that eventually trades it live.

What "honest" means here, non-negotiable:
- **Point-in-time truth.** No survivorship bias, no look-ahead. Rank each date against the universe
  *as it stood then*. Costs, slippage, and constituent churn are modeled, not wished away.
- **Data integrity first.** Every price/indicator is keyed by its real identity, current to the last
  session, and validated with evidence before it's trusted. A wrong number is worse than no number.
- **Pluggable by design.** Strategies, indicators, universes, and data sources are interfaces, not
  hardcraft. Adding a signal or a benchmark shouldn't touch the engine.
- **Built to scale from day one.** 50 symbols today, 5000 tomorrow — no assumption of small N.

The terminal spans the full loop: data → indicators → strategy → backtest → analytics/dashboard →
[alerts → live execution → multi-user]. Research and execution share one truth.

## Architecture (two tracks)

- **tvagent** (this repo) — the clean production target. Python/FastAPI · Postgres + Parquet ·
  tvdatafeed · Next.js glass dashboard. Point-in-time correct. Built TDD, no shortcuts.
- **market-hunt/** — a working prototype (FastAPI monolith + MongoDB) adopted as the **stopgap**:
  run it now, harden it, port proven pieces into tvagent; reuse its frontend as the dashboard base.

## How to work here

- Production-grade from the first line. Close known issues **at the source**, never as "fix later"
  caveats. Correctness over speed.
- Brainstorm → spec → plan → TDD → verify-with-evidence. Don't claim "works" without running it.
- Don't fabricate APIs/results; flag assumptions and bias honestly (even when the backtest looks great).
- Commit only when asked; author "Arpitha B", no Co-Authored-By.
- Living state (what's loaded, what's built, open bugs) lives in auto-memory, not here. This file is
  the *why*; memory is the *where we are*.
