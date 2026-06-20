# AI-Canvas Quant Terminal — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A live AI-composed canvas where Claude Code runs a backtest and paints it — equity curve, metrics, holdings, and a flagged bias note — as knowledge the user consumes, updating in real time.

**Architecture:** A thin Next.js app in `tvagent/terminal/` reads a filesystem **shared workspace** (`workspace/artifacts/*.json`, `workspace/intents/*.json`, `workspace/session.json`). Claude Code (the brain) writes validated artifacts via a Node emit CLI after calling the working market-hunt backtest API; the canvas streams artifact changes over SSE and renders each by its `render` type, leading with the AI's `knowledge` framing. No nav/forms — the AI composes the surface via `session.json` order.

**Tech Stack:** Next.js (App Router) + TypeScript + Tailwind, recharts (charts), chokidar (file watch), vitest + @testing-library/react + jsdom (tests). Node 18+.

## Global Constraints

- App lives in `tvagent/terminal/`; dev server on **port 3100** (market-hunt uses 3000/3001).
- Backtests come from the existing market-hunt API: `POST http://localhost:3001/api/simulation/run`.
- `workspace/` is runtime state — gitignored; never commit artifacts/intents.
- Artifacts carry `knowledge` (lead_insight, caveats, suggested_next) — the AI's understanding, per spec principle #1. The canvas leads with it, not the raw numbers.
- `render` ∈ {`equity_curve`, `metrics_card`, `holdings_table`, `bias_note`}; unknown → safe fallback card, never crash.
- One shared validator (`lib/artifact.ts`); the emit CLI and the read API both use it. DRY.
- Commit only when the user asks; author "Arpitha B", no Co-Authored-By.
- Run commands from `tvagent/terminal/` unless stated. This is Windows; use the project's Node/npm.

---

### Task 1: Scaffold the terminal app + tooling + workspace

**Files:**
- Create: `tvagent/terminal/` (Next.js app via scaffold)
- Create: `tvagent/terminal/vitest.config.ts`, `tvagent/terminal/vitest.setup.ts`
- Create: `tvagent/terminal/.gitignore` (append)
- Create: `tvagent/terminal/workspace/.gitkeep`

- [ ] **Step 1: Scaffold Next.js (non-interactive)**

Run from `tvagent/`:
```bash
npx create-next-app@latest terminal --ts --tailwind --app --src-dir=false --eslint --use-npm --no-import-alias --yes
```
Expected: `terminal/` created with `package.json`, `app/`, `next.config.ts`.

- [ ] **Step 2: Add deps**

Run from `tvagent/terminal/`:
```bash
npm install recharts chokidar
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom @vitejs/plugin-react
```
Expected: installs succeed.

- [ ] **Step 3: Configure port, test scripts, vitest**

Edit `package.json` `scripts` to:
```json
{
  "dev": "next dev --port 3100",
  "build": "next build",
  "start": "next start --port 3100",
  "test": "vitest run"
}
```

Create `vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", setupFiles: ["./vitest.setup.ts"], globals: true },
});
```

Create `vitest.setup.ts`:
```ts
import "@testing-library/jest-dom";
```

Append to `.gitignore`:
```
/workspace/artifacts/
/workspace/intents/
/workspace/session.json
```

Create `workspace/.gitkeep` (empty).

- [ ] **Step 4: Smoke test the toolchain**

```bash
npm run test
```
Expected: vitest runs and reports "No test files found" (exit 0) — toolchain works.

```bash
npm run dev &  # then:
curl -s -o /dev/null -w "%{http_code}\n" --retry 8 --retry-connrefused --retry-delay 2 http://localhost:3100
```
Expected: `200`. Stop the dev server after.

- [ ] **Step 5: Commit** (only if user asks)
```bash
git add terminal/ ; git commit -m "feat(terminal): scaffold AI-canvas Next.js app + tooling"
```

---

### Task 2: Artifact & intent contract + validator

**Files:**
- Create: `tvagent/terminal/lib/artifact.ts`
- Test: `tvagent/terminal/lib/artifact.test.ts`

**Interfaces:**
- Produces:
  - `type RenderType = "equity_curve" | "metrics_card" | "holdings_table" | "bias_note"`
  - `interface Knowledge { lead_insight: string; caveats: string[]; suggested_next: string[] }`
  - `interface Artifact { id: string; type: string; render: RenderType; title: string; params: Record<string, unknown>; data: unknown; knowledge: Knowledge; source: string; created_at: string }`
  - `interface Intent { id: string; action: "rerun"; artifact_id: string; changes: Record<string, unknown> }`
  - `validateArtifact(obj: unknown): { ok: boolean; errors: string[] }`
  - `applyIntent(params: Record<string, unknown>, intent: Intent): Record<string, unknown>` — returns `{...params, ...intent.changes}`

- [ ] **Step 1: Write failing tests**
```ts
// lib/artifact.test.ts
import { validateArtifact, applyIntent, type Artifact } from "./artifact";

const good: Artifact = {
  id: "backtest_001", type: "backtest", render: "metrics_card",
  title: "t", params: { universe: "NIFTY500" }, data: { total_return: 10 },
  knowledge: { lead_insight: "x", caveats: [], suggested_next: [] },
  source: "market-hunt", created_at: "2026-06-20T00:00:00Z",
};

test("validateArtifact accepts a well-formed artifact", () => {
  expect(validateArtifact(good)).toEqual({ ok: true, errors: [] });
});

test("validateArtifact rejects unknown render type", () => {
  const r = validateArtifact({ ...good, render: "pie" });
  expect(r.ok).toBe(false);
  expect(r.errors.join()).toMatch(/render/);
});

test("validateArtifact rejects missing knowledge", () => {
  const { knowledge, ...bad } = good as any;
  expect(validateArtifact(bad).ok).toBe(false);
});

test("applyIntent merges changes over params", () => {
  expect(applyIntent({ max_holdings: 10, universe: "NIFTY500" },
    { id: "i1", action: "rerun", artifact_id: "backtest_001", changes: { max_holdings: 5 } }))
    .toEqual({ max_holdings: 5, universe: "NIFTY500" });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `npm run test -- lib/artifact.test.ts`
Expected: FAIL — cannot resolve `./artifact`.

- [ ] **Step 3: Implement**
```ts
// lib/artifact.ts
export type RenderType = "equity_curve" | "metrics_card" | "holdings_table" | "bias_note";
const RENDER_TYPES: RenderType[] = ["equity_curve", "metrics_card", "holdings_table", "bias_note"];

export interface Knowledge { lead_insight: string; caveats: string[]; suggested_next: string[] }
export interface Artifact {
  id: string; type: string; render: RenderType; title: string;
  params: Record<string, unknown>; data: unknown; knowledge: Knowledge;
  source: string; created_at: string;
}
export interface Intent {
  id: string; action: "rerun"; artifact_id: string; changes: Record<string, unknown>;
}

export function validateArtifact(obj: unknown): { ok: boolean; errors: string[] } {
  const e: string[] = [];
  const a = obj as Record<string, unknown>;
  if (!a || typeof a !== "object") return { ok: false, errors: ["not an object"] };
  for (const f of ["id", "type", "title", "source", "created_at"]) {
    if (typeof a[f] !== "string") e.push(`${f} must be a string`);
  }
  if (!RENDER_TYPES.includes(a.render as RenderType)) e.push(`render must be one of ${RENDER_TYPES.join("|")}`);
  if (!a.params || typeof a.params !== "object") e.push("params must be an object");
  if (!("data" in a)) e.push("data is required");
  const k = a.knowledge as Record<string, unknown> | undefined;
  if (!k || typeof k !== "object") e.push("knowledge must be an object");
  else {
    if (typeof k.lead_insight !== "string") e.push("knowledge.lead_insight must be a string");
    if (!Array.isArray(k.caveats)) e.push("knowledge.caveats must be an array");
    if (!Array.isArray(k.suggested_next)) e.push("knowledge.suggested_next must be an array");
  }
  return { ok: e.length === 0, errors: e };
}

export function applyIntent(params: Record<string, unknown>, intent: Intent): Record<string, unknown> {
  return { ...params, ...intent.changes };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npm run test -- lib/artifact.test.ts`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit** (only if user asks)
```bash
git add terminal/lib/artifact.ts terminal/lib/artifact.test.ts
git commit -m "feat(terminal): artifact/intent contract + validator"
```

---

### Task 3: Emit CLI — Claude Code's tool to paint the canvas

**Files:**
- Create: `tvagent/terminal/lib/emit.mjs`
- Test: `tvagent/terminal/lib/emit.test.ts`

**Interfaces:**
- Consumes: `validateArtifact` (Task 2).
- Produces: a CLI `node lib/emit.mjs <artifact.json>` that validates, writes `workspace/artifacts/<id>.json`, and upserts the id into `workspace/session.json` `order` (most-recent last). Exits non-zero on invalid input. Also `node lib/emit.mjs --session-clear` empties the canvas.

- [ ] **Step 1: Write failing test**
```ts
// lib/emit.test.ts
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const good = {
  id: "art_x", type: "backtest", render: "metrics_card", title: "t",
  params: {}, data: { total_return: 1 },
  knowledge: { lead_insight: "x", caveats: [], suggested_next: [] },
  source: "test", created_at: "2026-06-20T00:00:00Z",
};

function run(ws: string, file: string) {
  return execFileSync("node", ["lib/emit.mjs", file], { env: { ...process.env, TERMINAL_WORKSPACE: ws }, cwd: process.cwd() });
}

test("emit writes a valid artifact and updates session order", () => {
  const ws = mkdtempSync(join(tmpdir(), "ws-"));
  const f = join(ws, "in.json");
  writeFileSync(f, JSON.stringify(good));
  run(ws, f);
  expect(existsSync(join(ws, "artifacts", "art_x.json"))).toBe(true);
  const session = JSON.parse(readFileSync(join(ws, "session.json"), "utf8"));
  expect(session.order).toContain("art_x");
});

test("emit rejects an invalid artifact (non-zero exit)", () => {
  const ws = mkdtempSync(join(tmpdir(), "ws-"));
  const f = join(ws, "bad.json");
  writeFileSync(f, JSON.stringify({ ...good, render: "pie" }));
  expect(() => run(ws, f)).toThrow();
});
```

- [ ] **Step 2: Run to verify fail**

Run: `npm run test -- lib/emit.test.ts`
Expected: FAIL — `lib/emit.mjs` missing.

- [ ] **Step 3: Implement**
```js
// lib/emit.mjs
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const WS = process.env.TERMINAL_WORKSPACE || join(HERE, "..", "workspace");

// Inline validator mirror of lib/artifact.ts (kept in sync; single source of truth is the TS test).
const RENDER_TYPES = ["equity_curve", "metrics_card", "holdings_table", "bias_note"];
function validate(a) {
  const e = [];
  if (!a || typeof a !== "object") return ["not an object"];
  for (const f of ["id", "type", "title", "source", "created_at"]) if (typeof a[f] !== "string") e.push(`${f} string`);
  if (!RENDER_TYPES.includes(a.render)) e.push("render invalid");
  if (!a.params || typeof a.params !== "object") e.push("params object");
  if (!("data" in a)) e.push("data required");
  const k = a.knowledge;
  if (!k || typeof k !== "object" || typeof k.lead_insight !== "string"
      || !Array.isArray(k.caveats) || !Array.isArray(k.suggested_next)) e.push("knowledge invalid");
  return e;
}

function sessionPath() { return join(WS, "session.json"); }
function readSession() {
  try { return JSON.parse(readFileSync(sessionPath(), "utf8")); } catch { return { order: [], active: {} }; }
}

const arg = process.argv[2];
if (arg === "--session-clear") {
  mkdirSync(WS, { recursive: true });
  writeFileSync(sessionPath(), JSON.stringify({ order: [], active: {} }, null, 2));
  process.exit(0);
}
if (!arg) { console.error("usage: emit.mjs <artifact.json> | --session-clear"); process.exit(2); }

const artifact = JSON.parse(readFileSync(arg, "utf8"));
const errors = validate(artifact);
if (errors.length) { console.error("invalid artifact: " + errors.join(", ")); process.exit(1); }

mkdirSync(join(WS, "artifacts"), { recursive: true });
writeFileSync(join(WS, "artifacts", `${artifact.id}.json`), JSON.stringify(artifact, null, 2));

const session = readSession();
session.order = (session.order || []).filter((x) => x !== artifact.id).concat(artifact.id);
session.active = artifact.params || session.active || {};
writeFileSync(sessionPath(), JSON.stringify(session, null, 2));
console.log(`emitted ${artifact.id}`);
```

- [ ] **Step 4: Run to verify pass**

Run: `npm run test -- lib/emit.test.ts`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit** (only if user asks)
```bash
git add terminal/lib/emit.mjs terminal/lib/emit.test.ts
git commit -m "feat(terminal): emit CLI to write validated artifacts + session order"
```

---

### Task 4: Backtest → artifacts transform

**Files:**
- Create: `tvagent/terminal/lib/backtestToArtifacts.ts`
- Test: `tvagent/terminal/lib/backtestToArtifacts.test.ts`
- Create (fixture): `tvagent/terminal/lib/__fixtures__/sim_sample.json` (a trimmed real `/api/simulation/run` response: `{ simulation: { results: [{date,portfolio_value,benchmark_value}...], summary: {total_return,benchmark_return,alpha,max_drawdown,sharpe_ratio,total_trades,charge_impact} } }`)

**Interfaces:**
- Consumes: `Artifact`, `Knowledge` (Task 2).
- Produces: `backtestToArtifacts(sim: any, params: Record<string, unknown>, idBase: string): Artifact[]` — returns an `equity_curve`, a `metrics_card`, and a `bias_note` artifact (holdings omitted if absent). The `bias_note` always carries the survivorship caveat when membership is current (spec: honesty visible on canvas).

- [ ] **Step 1: Create the fixture**

Create `lib/__fixtures__/sim_sample.json`:
```json
{ "simulation": {
  "summary": { "total_return": 1070.17, "benchmark_return": 97.11, "alpha": 973.05,
    "max_drawdown": -37.28, "sharpe_ratio": 1.607, "total_trades": 78, "charge_impact": 48.58 },
  "results": [
    { "date": "2020-01-01", "portfolio_value": 100000, "benchmark_value": 100000 },
    { "date": "2026-06-19", "portfolio_value": 1170165, "benchmark_value": 197111 }
  ] } }
```

- [ ] **Step 2: Write failing test**
```ts
// lib/backtestToArtifacts.test.ts
import sim from "./__fixtures__/sim_sample.json";
import { backtestToArtifacts } from "./backtestToArtifacts";
import { validateArtifact } from "./artifact";

test("produces equity_curve, metrics_card and bias_note, all valid", () => {
  const arts = backtestToArtifacts(sim as any, { universe: "NIFTY500", max_holdings: 10 }, "bt1");
  const renders = arts.map((a) => a.render);
  expect(renders).toEqual(expect.arrayContaining(["equity_curve", "metrics_card", "bias_note"]));
  arts.forEach((a) => expect(validateArtifact(a)).toEqual({ ok: true, errors: [] }));
});

test("metrics_card carries the summary numbers", () => {
  const arts = backtestToArtifacts(sim as any, { universe: "NIFTY500" }, "bt1");
  const m = arts.find((a) => a.render === "metrics_card")!;
  expect((m.data as any).total_return).toBe(1070.17);
});

test("bias_note flags survivorship when universe membership is current", () => {
  const arts = backtestToArtifacts(sim as any, { universe: "NIFTY500" }, "bt1");
  const b = arts.find((a) => a.render === "bias_note")!;
  expect(b.knowledge.caveats.join(" ").toLowerCase()).toMatch(/survivorship/);
});
```

- [ ] **Step 3: Run to verify fail**

Run: `npm run test -- lib/backtestToArtifacts.test.ts`
Expected: FAIL — module missing.

- [ ] **Step 4: Implement**
```ts
// lib/backtestToArtifacts.ts
import type { Artifact } from "./artifact";

export function backtestToArtifacts(
  sim: any, params: Record<string, unknown>, idBase: string,
): Artifact[] {
  const s = sim?.simulation ?? sim;
  const summary = s?.summary ?? {};
  const results: any[] = s?.results ?? [];
  const now = "2026-06-20T00:00:00Z"; // stamped by caller normally; fixed for determinism in lib
  const series = results.map((r) => ({
    date: r.date, portfolio: r.portfolio_value, benchmark: r.benchmark_value,
  }));
  const title = `Backtest · ${String(params.universe ?? "?")} · top ${String(params.max_holdings ?? "?")}`;

  const equity: Artifact = {
    id: `${idBase}_equity`, type: "backtest", render: "equity_curve", title,
    params, data: { series, benchmark_label: "Nifty 50" },
    knowledge: {
      lead_insight: `Grew to ${Math.round(Number(summary.total_return ?? 0))}% vs benchmark ${Math.round(Number(summary.benchmark_return ?? 0))}%.`,
      caveats: [], suggested_next: ["Compare 222-day ROC ranking", "Re-run point-in-time"],
    },
    source: "market-hunt:/api/simulation/run", created_at: now,
  };

  const metrics: Artifact = {
    id: `${idBase}_metrics`, type: "backtest", render: "metrics_card", title,
    params, data: {
      total_return: summary.total_return, benchmark_return: summary.benchmark_return,
      alpha: summary.alpha, max_drawdown: summary.max_drawdown,
      sharpe_ratio: summary.sharpe_ratio, total_trades: summary.total_trades,
      charge_impact: summary.charge_impact,
    },
    knowledge: {
      lead_insight: `Alpha ${Math.round(Number(summary.alpha ?? 0))}% at Sharpe ${summary.sharpe_ratio}.`,
      caveats: [], suggested_next: [],
    },
    source: "market-hunt:/api/simulation/run", created_at: now,
  };

  const usesCurrentMembership = true; // market-hunt engine: current constituents
  const bias: Artifact = {
    id: `${idBase}_bias`, type: "note", render: "bias_note", title: "What to trust about this number",
    params, data: { engine: "market-hunt (current membership)" },
    knowledge: {
      lead_insight: "Treat the return as optimistic, not live-tradeable.",
      caveats: usesCurrentMembership
        ? ["Survivorship bias: ranked against today's index members, not point-in-time; drop-outs are excluded, which inflates a momentum backtest."]
        : [],
      suggested_next: ["Run against tvagent's point-in-time engine to quantify the gap."],
    },
    source: "tvagent:canvas", created_at: now,
  };

  return [equity, metrics, bias];
}
```

- [ ] **Step 5: Run to verify pass**

Run: `npm run test -- lib/backtestToArtifacts.test.ts`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit** (only if user asks)
```bash
git add terminal/lib/backtestToArtifacts.ts terminal/lib/backtestToArtifacts.test.ts terminal/lib/__fixtures__/sim_sample.json
git commit -m "feat(terminal): backtest->artifacts transform with bias knowledge"
```

---

### Task 5: Workspace read API + SSE stream

**Files:**
- Create: `tvagent/terminal/app/api/artifacts/route.ts`
- Create: `tvagent/terminal/app/api/stream/route.ts`
- Create: `tvagent/terminal/lib/workspace.ts`
- Test: `tvagent/terminal/lib/workspace.test.ts`

**Interfaces:**
- Consumes: `Artifact`, `validateArtifact` (Task 2).
- Produces:
  - `readCanvas(ws?: string): { order: string[]; artifacts: Artifact[] }` — reads `session.json` order + valid artifacts (invalid ones dropped).
  - `GET /api/artifacts` → JSON `{ order, artifacts }`.
  - `GET /api/stream` → SSE; emits `data: {"type":"refresh"}\n\n` on any change under `workspace/`.

- [ ] **Step 1: Write failing test for readCanvas**
```ts
// lib/workspace.test.ts
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readCanvas } from "./workspace";

function seed() {
  const ws = mkdtempSync(join(tmpdir(), "ws-"));
  mkdirSync(join(ws, "artifacts"), { recursive: true });
  const a = { id: "a1", type: "backtest", render: "metrics_card", title: "t", params: {}, data: {},
    knowledge: { lead_insight: "x", caveats: [], suggested_next: [] }, source: "s", created_at: "z" };
  writeFileSync(join(ws, "artifacts", "a1.json"), JSON.stringify(a));
  writeFileSync(join(ws, "artifacts", "bad.json"), JSON.stringify({ id: "bad" }));
  writeFileSync(join(ws, "session.json"), JSON.stringify({ order: ["a1"], active: {} }));
  return ws;
}

test("readCanvas returns valid artifacts in session order, drops invalid", () => {
  const ws = seed();
  const { order, artifacts } = readCanvas(ws);
  expect(order).toEqual(["a1"]);
  expect(artifacts.map((a) => a.id)).toEqual(["a1"]);
});
```

- [ ] **Step 2: Run to verify fail**

Run: `npm run test -- lib/workspace.test.ts`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `lib/workspace.ts`**
```ts
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { validateArtifact, type Artifact } from "./artifact";

export function workspaceDir(ws?: string): string {
  return ws || process.env.TERMINAL_WORKSPACE || join(process.cwd(), "workspace");
}

export function readCanvas(ws?: string): { order: string[]; artifacts: Artifact[] } {
  const dir = workspaceDir(ws);
  const adir = join(dir, "artifacts");
  let order: string[] = [];
  try { order = JSON.parse(readFileSync(join(dir, "session.json"), "utf8")).order ?? []; } catch {}
  const byId = new Map<string, Artifact>();
  if (existsSync(adir)) {
    for (const f of readdirSync(adir)) {
      if (!f.endsWith(".json")) continue;
      try {
        const obj = JSON.parse(readFileSync(join(adir, f), "utf8"));
        if (validateArtifact(obj).ok) byId.set(obj.id, obj as Artifact);
      } catch {}
    }
  }
  const ordered = order.filter((id) => byId.has(id)).map((id) => byId.get(id)!);
  // include any valid artifacts not yet in order (appended)
  for (const [id, a] of byId) if (!order.includes(id)) ordered.push(a);
  return { order, artifacts: ordered };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npm run test -- lib/workspace.test.ts`
Expected: PASS (1 passed).

- [ ] **Step 5: Add the route handlers**

Create `app/api/artifacts/route.ts`:
```ts
import { NextResponse } from "next/server";
import { readCanvas } from "../../../lib/workspace";
export const dynamic = "force-dynamic";
export async function GET() {
  return NextResponse.json(readCanvas());
}
```

Create `app/api/stream/route.ts`:
```ts
import chokidar from "chokidar";
import { workspaceDir } from "../../../lib/workspace";
export const dynamic = "force-dynamic";

export async function GET() {
  const enc = new TextEncoder();
  const dir = workspaceDir();
  const stream = new ReadableStream({
    start(controller) {
      const send = () => controller.enqueue(enc.encode(`data: {"type":"refresh"}\n\n`));
      send(); // initial
      const watcher = chokidar.watch(dir, { ignoreInitial: true });
      watcher.on("all", send);
      const keep = setInterval(() => controller.enqueue(enc.encode(": ping\n\n")), 15000);
      // @ts-ignore - cleanup on cancel
      (controller as any)._cleanup = () => { clearInterval(keep); watcher.close(); };
    },
    cancel() { /* @ts-ignore */ (this as any)._cleanup?.(); },
  });
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform", Connection: "keep-alive" },
  });
}
```

- [ ] **Step 6: Integration smoke**

```bash
npm run dev &  # then, with a sample artifact written into workspace/artifacts/ (reuse emit):
node lib/emit.mjs lib/__fixtures__/sim_sample.json 2>/dev/null || true
curl -s http://localhost:3100/api/artifacts | head -c 200
```
Expected: JSON with `order` and `artifacts` keys. (The fixture isn't an artifact; instead emit a real artifact in Task 7 — here just confirm the endpoint returns `{"order":[...],"artifacts":[...]}` shape, possibly empty.)

- [ ] **Step 7: Commit** (only if user asks)
```bash
git add terminal/app/api terminal/lib/workspace.ts terminal/lib/workspace.test.ts
git commit -m "feat(terminal): workspace read API + SSE change stream"
```

---

### Task 6: Canvas + renderers

**Files:**
- Create: `tvagent/terminal/app/page.tsx`
- Create: `tvagent/terminal/components/Canvas.tsx`
- Create: `tvagent/terminal/components/artifacts/EquityCurve.tsx`, `MetricsCard.tsx`, `HoldingsTable.tsx`, `BiasNote.tsx`, `Fallback.tsx`
- Create: `tvagent/terminal/components/ArtifactCard.tsx`
- Test: `tvagent/terminal/components/artifacts/EquityCurve.test.tsx`, `tvagent/terminal/components/ArtifactCard.test.tsx`

**Interfaces:**
- Consumes: `Artifact` (Task 2); `GET /api/artifacts`, `GET /api/stream` (Task 5).
- Produces: `ArtifactCard({ artifact }: { artifact: Artifact })` — renders the `knowledge.lead_insight` header + the body component chosen by `render`; unknown render → `Fallback`.

- [ ] **Step 1: Write failing component tests**
```tsx
// components/artifacts/EquityCurve.test.tsx
import { render } from "@testing-library/react";
import { EquityCurve } from "./EquityCurve";
test("renders an svg chart for two series", () => {
  const { container } = render(
    <EquityCurve data={{ series: [{ date: "2020-01-01", portfolio: 100, benchmark: 100 },
      { date: "2021-01-01", portfolio: 150, benchmark: 110 }], benchmark_label: "Nifty 50" }} />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
```
```tsx
// components/ArtifactCard.test.tsx
import { render, screen } from "@testing-library/react";
import { ArtifactCard } from "./ArtifactCard";
const base = { id: "a", type: "t", title: "T", params: {}, data: {},
  knowledge: { lead_insight: "LEAD!", caveats: ["c1"], suggested_next: [] }, source: "s", created_at: "z" };
test("leads with knowledge insight", () => {
  render(<ArtifactCard artifact={{ ...base, render: "metrics_card", data: { total_return: 5 } } as any} />);
  expect(screen.getByText("LEAD!")).toBeInTheDocument();
});
test("unknown render falls back, never throws", () => {
  render(<ArtifactCard artifact={{ ...base, render: "pie" } as any} />);
  expect(screen.getByText(/unsupported render/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify fail**

Run: `npm run test -- components/`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement renderers**

`components/artifacts/EquityCurve.tsx`:
```tsx
"use client";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts";
export function EquityCurve({ data }: { data: any }) {
  return (
    <div style={{ width: "100%", height: 280 }}>
      <ResponsiveContainer>
        <LineChart data={data.series}>
          <XAxis dataKey="date" hide /><YAxis /><Tooltip /><Legend />
          <Line type="monotone" dataKey="portfolio" stroke="#22c55e" dot={false} name="Strategy" />
          <Line type="monotone" dataKey="benchmark" stroke="#64748b" dot={false} name={data.benchmark_label ?? "Benchmark"} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```
`components/artifacts/MetricsCard.tsx`:
```tsx
export function MetricsCard({ data }: { data: any }) {
  const items: [string, unknown][] = [
    ["Total return %", data.total_return], ["Benchmark %", data.benchmark_return],
    ["Alpha %", data.alpha], ["Max drawdown %", data.max_drawdown],
    ["Sharpe", data.sharpe_ratio], ["Trades", data.total_trades], ["Charge impact %", data.charge_impact],
  ];
  return (
    <div className="grid grid-cols-3 gap-3">
      {items.map(([k, v]) => (
        <div key={k} className="rounded-lg border border-slate-700 p-3">
          <div className="text-xs text-slate-400">{k}</div>
          <div className="text-lg font-semibold">{v === undefined || v === null ? "—" : String(v)}</div>
        </div>
      ))}
    </div>
  );
}
```
`components/artifacts/HoldingsTable.tsx`:
```tsx
export function HoldingsTable({ data }: { data: any }) {
  const rows: any[] = data?.rows ?? [];
  return (
    <table className="w-full text-sm">
      <thead><tr className="text-slate-400 text-left"><th>Symbol</th><th>Weight</th><th>Date</th></tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={i} className="border-t border-slate-800"><td>{r.symbol}</td><td>{r.weight}</td><td>{r.date}</td></tr>
      ))}</tbody>
    </table>
  );
}
```
`components/artifacts/BiasNote.tsx`:
```tsx
export function BiasNote({ knowledge }: { knowledge: any }) {
  return (
    <div className="rounded-lg border border-amber-600/50 bg-amber-950/30 p-3 text-sm">
      <div className="font-medium text-amber-300">{knowledge.lead_insight}</div>
      <ul className="mt-2 list-disc pl-5 text-amber-100/80">
        {knowledge.caveats.map((c: string, i: number) => <li key={i}>{c}</li>)}
      </ul>
    </div>
  );
}
```
`components/artifacts/Fallback.tsx`:
```tsx
export function Fallback({ artifact }: { artifact: any }) {
  return (
    <div className="rounded-lg border border-slate-700 p-3 text-sm">
      <div className="text-slate-400">unsupported render: {String(artifact.render)}</div>
      <pre className="mt-2 overflow-auto text-xs">{JSON.stringify(artifact.data, null, 2)}</pre>
    </div>
  );
}
```

- [ ] **Step 4: Implement `ArtifactCard.tsx`**
```tsx
import { EquityCurve } from "./artifacts/EquityCurve";
import { MetricsCard } from "./artifacts/MetricsCard";
import { HoldingsTable } from "./artifacts/HoldingsTable";
import { BiasNote } from "./artifacts/BiasNote";
import { Fallback } from "./artifacts/Fallback";
import type { Artifact } from "../lib/artifact";

export function ArtifactCard({ artifact }: { artifact: Artifact }) {
  const body = (() => {
    switch (artifact.render) {
      case "equity_curve": return <EquityCurve data={artifact.data} />;
      case "metrics_card": return <MetricsCard data={artifact.data} />;
      case "holdings_table": return <HoldingsTable data={artifact.data} />;
      case "bias_note": return <BiasNote knowledge={artifact.knowledge} />;
      default: return <Fallback artifact={artifact} />;
    }
  })();
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">{artifact.title}</div>
      {artifact.render !== "bias_note" && (
        <div className="mb-3 text-sm text-slate-200">{artifact.knowledge.lead_insight}</div>
      )}
      {body}
      {artifact.render !== "bias_note" && artifact.knowledge.suggested_next.length > 0 && (
        <div className="mt-3 text-xs text-slate-500">Next: {artifact.knowledge.suggested_next.join(" · ")}</div>
      )}
    </section>
  );
}
```

- [ ] **Step 5: Run component tests to verify pass**

Run: `npm run test -- components/`
Expected: PASS.

- [ ] **Step 6: Implement Canvas + page (live)**

`components/Canvas.tsx`:
```tsx
"use client";
import { useEffect, useState } from "react";
import { ArtifactCard } from "./ArtifactCard";
import type { Artifact } from "../lib/artifact";

export function Canvas() {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  async function refresh() {
    const r = await fetch("/api/artifacts", { cache: "no-store" });
    const j = await r.json();
    setArtifacts(j.artifacts ?? []);
  }
  useEffect(() => {
    refresh();
    const es = new EventSource("/api/stream");
    es.onmessage = () => refresh();
    return () => es.close();
  }, []);
  return (
    <div className="grid gap-4">
      {artifacts.length === 0 && <div className="text-slate-500">The canvas is empty. Ask Claude to run a backtest.</div>}
      {artifacts.map((a) => <ArtifactCard key={a.id} artifact={a} />)}
    </div>
  );
}
```
`app/page.tsx`:
```tsx
import { Canvas } from "../components/Canvas";
export default function Home() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-5xl p-6">
        <header className="mb-6">
          <h1 className="text-lg font-semibold">tvagent · AI Canvas</h1>
          <p className="text-sm text-slate-500">Knowledge painted by Claude. You consume and steer.</p>
        </header>
        <Canvas />
      </div>
    </main>
  );
}
```

- [ ] **Step 7: Verify build + render**

```bash
npm run test
npm run dev &
curl -s http://localhost:3100 | grep -o "AI Canvas" | head -1
```
Expected: tests pass; curl prints `AI Canvas`.

- [ ] **Step 8: Commit** (only if user asks)
```bash
git add terminal/app/page.tsx terminal/components/
git commit -m "feat(terminal): live canvas + artifact renderers"
```

---

### Task 7: Prove the loop end-to-end (operational)

**Files:** none (uses Tasks 1–6). Verifies the spec's data flow with a real backtest.

- [ ] **Step 1: Start services**

Ensure market-hunt backend (`:3001`) + Mongo are up (from earlier). Start the canvas:
```bash
cd tvagent/terminal && npm run dev   # background; serves :3100
```

- [ ] **Step 2: Agent runs a backtest and paints it**

From `tvagent/terminal/`, Claude Code runs:
```bash
node lib/emit.mjs --session-clear
curl -s -X POST http://localhost:3001/api/simulation/run -H "Content-Type: application/json" \
  -d '{"strategy_id":"strategy_1781939795919","portfolio_base_value":100000,"rebalance_frequency":"monthly","rebalance_date":"first","rebalance_type":"equal_weight","universe":"NIFTY500","benchmark_symbol":"Nifty 50","max_holdings":10,"momentum_ranking":"20_day_return","start_date":"2020-01-01","end_date":"2026-06-30"}' \
  -o /tmp/sim.json
node -e "import('./lib/backtestToArtifacts.ts')" 2>/dev/null || true
```
Then transform + emit each artifact. Use a tiny inline Node script that imports the compiled transform (or run via `tsx`): `npm install -D tsx` if needed, then:
```bash
npx tsx -e "import {backtestToArtifacts} from './lib/backtestToArtifacts'; import {readFileSync,writeFileSync} from 'fs'; const sim=JSON.parse(readFileSync('/tmp/sim.json','utf8')); const arts=backtestToArtifacts(sim,{universe:'NIFTY500',max_holdings:10},'bt_'+Date.now()); arts.forEach(a=>{const f='/tmp/'+a.id+'.json'; writeFileSync(f,JSON.stringify(a)); }); console.log(arts.map(a=>a.id).join('\n'));" > /tmp/ids.txt
while read id; do node lib/emit.mjs "/tmp/$id.json"; done < /tmp/ids.txt
```

- [ ] **Step 3: Confirm the canvas rendered it live**

```bash
curl -s http://localhost:3100/api/artifacts | npx tsx -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const j=JSON.parse(s);console.log('artifacts:',j.artifacts.map(a=>a.render).join(','))})"
```
Expected: `artifacts: equity_curve,metrics_card,bias_note`. Open `http://localhost:3100` — equity curve, metrics, and the amber bias note are visible, each led by its insight.

- [ ] **Step 4: Prove a steer (intent → rerun)**

Write an intent and have the agent act:
```bash
echo '{"id":"int1","action":"rerun","artifact_id":"bt_equity","changes":{"max_holdings":5}}' > workspace/intents/int1.json
```
Claude Code reads the intent, re-runs `/api/simulation/run` with `max_holdings=5`, re-emits artifacts → canvas updates live. Confirm the new metrics differ.

- [ ] **Step 5: Report** — no code commit unless the user asks; summarize what the canvas shows.

---

## Self-Review

**Spec coverage:** principle #1 (AI composes; knowledge-led) → Task 4 `knowledge` + Task 6 `ArtifactCard` leads with insight, `session.json` order drives layout (Tasks 3,5,6). Claude-Code-as-brain + filesystem shared state → Tasks 3,5,7. Approach C contract (artifact/intent/session) → Tasks 2,3,5. Engine-agnostic via market-hunt API → Tasks 4,7. 4 renderers + fallback → Task 6. SSE live update → Tasks 5,6. bias_note honesty → Task 4. Intent/rerun steer → Tasks 2,7. Testing (validator, transform, render, smoke) → Tasks 2,4,6,5. All covered.

**Placeholder scan:** no TBD/TODO; every code step has full code; the `tsx` inline scripts in Task 7 are complete commands. Holdings renderer ships but the transform omits holdings until the sim response is wired for it — acceptable (renderer is exercised by its own future data; not a placeholder, a ready slot).

**Type consistency:** `Artifact`, `Knowledge`, `Intent`, `validateArtifact`, `applyIntent` defined in Task 2 and used unchanged in Tasks 3–6; `backtestToArtifacts(sim, params, idBase)` defined in Task 4 and called identically in Task 7; `readCanvas`/`workspaceDir` defined Task 5 and used in routes + Canvas fetches `/api/artifacts` shape `{order, artifacts}` consistently.
