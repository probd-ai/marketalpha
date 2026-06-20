# AI-Canvas Quant Terminal — Design (Slice 1: Conversational Backtest Loop)

**Date:** 2026-06-20
**Repo:** tvagent (production target; the terminal's home)

## Principle #1 — The UI is the AI's canvas, not a website

This is deliberately *unlike* a traditional web app. There is no nav-and-forms surface the human
operates. **The AI composes the surface**: it decides what to render, how to arrange it, which insight
to lead with, which caveat to flag. The human is the **consumer of canvas knowledge** — reading the
AI's synthesized understanding and steering lightly — not the operator clicking through pages.

Consequences that bind every decision below:
- An **artifact is a unit of understanding made visual**, not just a chart: result data **plus** the
  AI's framing, flagged caveats (e.g. survivorship bias), and suggested next step.
- The **AI controls composition** — ordering, grouping, what to surface/retire — not a fixed dashboard
  the user navigates. Traditional chrome (menus, route trees, settings forms) is minimized to near-zero.
- The human's inputs are **light steering** (consume → react → redirect), not command-entry.

## Runtime decision — Claude Code is the brain

The terminal is a **thin UI over a Claude Code session** (the agent — e.g. this one), which drives a
shared workspace. We do not embed a separate LLM agent; the agent *is* Claude Code, with its full tool
and skill set. "The interface for you" — literally — is how the agent renders knowledge to the human
and how the human steers it.

**Build approach (phased, no dead-end):** build the live render-canvas now with Claude Code as brain
behind a clean shared-state contract, such that a later unified **web-chat terminal** (chat + canvas in
one browser surface, driving a headless Claude Code session) reuses the same contract and renderers —
only *who reads/writes* changes.

## Scope — this spec is Slice 1 only

The full terminal decomposes into: (1) conversation↔artifact shell + shared state [**this slice**],
(2) quant capabilities as agent-callable tools, (3) supervision/approval layer, (4) persistence of
research sessions. Slice 1 proves the paradigm on the one capability that already works end-to-end:
the **backtest**.

## Architecture

**Shared state = the filesystem.** The one substrate both a human-clickable UI and the agent's file
tools touch with zero infra. This *is* the shared workspace state.

```
terminal/
  workspace/
    artifacts/<id>.json   # agent writes: a unit of canvas knowledge
    intents/<id>.json     # canvas writes: light steering from the human
    session.json          # AI-composed canvas state: ordered artifact ids, active strategy/params
  app/                    # the canvas (Next.js)
  lib/                    # artifact schema + validator (shared by agent helper and canvas)
```

**Artifact schema** (`render` drives the component; `knowledge` carries the AI's framing — principle #1):
```json
{
  "id": "backtest_001",
  "type": "backtest",
  "render": "equity_curve | metrics_card | holdings_table | bias_note",
  "title": "TrueVX momentum · NIFTY500 · monthly top-10",
  "params": { "strategy_id": "...", "universe": "NIFTY500", "max_holdings": 10, "...": "..." },
  "data": { "...": "render-specific payload" },
  "knowledge": { "lead_insight": "string", "caveats": ["string"], "suggested_next": ["string"] },
  "source": "market-hunt:/api/simulation/run",
  "created_at": "2026-06-20T00:00:00Z"
}
```

**Intent schema** (human steering → agent picks up):
```json
{ "id": "int_001", "action": "rerun", "artifact_id": "backtest_001", "changes": { "max_holdings": 5 } }
```

**session.json** is AI-composed: an ordered list of artifact ids (what's on the canvas and in what
order) + the active strategy/params context. The agent rewrites it to recompose the canvas.

**Engine-agnostic:** Slice 1 produces backtests by calling the **working market-hunt API**
(`POST localhost:3001/api/simulation/run`). The artifact contract knows nothing about the engine, so
swapping in tvagent's point-in-time Postgres engine later changes only the agent's tool call + `source`.

## The canvas app (thin Next.js)

- Watches `workspace/artifacts/` and streams changes to the browser over **SSE** (a file-watch API
  route). New/updated artifact → live render; removed → retire from canvas.
- Renders each artifact by `render` type. Slice-1 renderers:
  - `equity_curve` — portfolio vs Nifty 50 line chart (+ drawdown shading).
  - `metrics_card` — total return, alpha, max drawdown, Sharpe, trades, charge impact.
  - `holdings_table` — holdings/trades for the run.
  - `bias_note` — the AI's flagged caveat (e.g. survivorship), styled as knowledge, not an afterthought.
- Each artifact shows its `knowledge` (lead insight, caveats, suggested next) — the AI's understanding,
  not just the numbers.
- Layout order follows `session.json` (AI-composed), not a fixed grid. A chat rail is reserved
  (empty in Slice 1; chat stays in the Claude Code CLI) so the B web-chat surface drops in later.
- Light steering: editing a param on a pinned artifact writes an `intent`; the agent acts on it.

## The loop (Slice 1)

1. Human (CLI chat): "run the TrueVX strategy on Nifty 500, top 10 monthly."
2. Agent calls `/api/simulation/run`, gets results, composes `artifacts/backtest_001.json` —
   equity curve + metrics + holdings + a `bias_note` + `knowledge` framing — and updates `session.json`.
3. Canvas renders it live.
4. Human reacts: edits a param (→ intent) or just says "now top 5 and flag the bias gap."
5. Agent re-runs, writes a new/updated artifact, recomposes the canvas. Repeat.

## Components & boundaries (YAGNI)

**In:** the workspace contract + schema/validator; the canvas app with the 4 renderers + SSE live
update; reading one intent (`rerun`); an agent-side helper (`terminal/lib` + a small
`emit_artifact` path) to write valid artifacts; the agent calling the existing backtest API.

**Out (later slices):** embedded/headless web-chat (B); persistence/DB; other tools (data load,
screener, charts, scheduler); auth/multi-user; the point-in-time engine (separate tvagent track).

## Error handling

- The artifact validator rejects malformed artifacts before they reach the canvas; the agent helper
  validates on write.
- Unknown `render` type → the canvas shows a safe fallback card (raw JSON + title), never crashes.
- A failed backtest is itself an artifact (`render: bias_note`-style error card) so the failure is
  visible knowledge on the canvas, not a silent gap.

## Testing (TDD)

- **Unit:** artifact schema validator (valid/invalid cases); intent parser (`rerun` + changes merge).
- **Component:** an `equity_curve` artifact renders a chart with both series; unknown render → fallback.
- **Integration smoke:** write a sample artifact to `workspace/artifacts/` → the SSE/list endpoint
  serves it → (headless) canvas lists it.

## Out of scope / explicitly deferred

Point-in-time membership (the honesty gap proven earlier) is a **tvagent engine** track, not this UI
slice. This slice renders whatever engine it's pointed at; today that's market-hunt (current-membership,
optimistic) — the `bias_note` makes that limitation visible on the canvas by design.
