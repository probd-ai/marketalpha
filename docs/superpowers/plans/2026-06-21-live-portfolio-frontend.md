# Live Portfolio — Frontend Implementation Plan (Plan 2 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the new `tvagent/frontend/` glass quant terminal — a Portfolios home, a dense Live Portfolio detail screen (₹ curve vs Nifty 50, holdings, sleeve attribution, rebalance), and a portfolio builder — consuming the Plan 1 backend.

**Architecture:** New Next.js 15 (App Router) + React 19 + TypeScript app. Data via TanStack Query over a thin typed `fetch` client that calls **relative `/api/*`**, proxied to the backend by Next.js rewrites (avoids CORS, no backend change). Recharts for the ₹ curve. Glass design system via Tailwind 4 CSS-first `@theme` tokens + a `.glass` utility. Pure formatting/validation helpers are unit-tested with Vitest; components render-tested with React Testing Library against fixture payloads.

**Tech Stack:** Next.js 15 · React 19 · TypeScript 5 · Tailwind CSS 4 · @tanstack/react-query 5 · Recharts 2 · Vitest + @testing-library/react + jsdom. Backend contract: `docs/superpowers/plans/2026-06-21-live-portfolio-backend.md`. Design: `docs/superpowers/specs/2026-06-21-live-portfolio-design.md` §5.

## Global Constraints

- **All frontend work is in `tvagent/frontend/`**, committed in the **tvagent** git repo (repo root `D:\gs\workspace\tvagent`). Run `npm` from inside `tvagent/frontend/`.
- **Node/npm** (use `npm`, matching market-hunt's frontend). Package manager: npm, lockfile committed.
- **API access:** the client calls **relative** paths (`/api/portfolio`, …). `next.config.ts` rewrites `/api/:path*` → `${API_PROXY_TARGET}/api/:path*`, `API_PROXY_TARGET` defaults to `http://localhost:3001`. **Never** hardcode `localhost:3001` in components.
- **Honesty in UI (non-negotiable):** every screen that shows portfolio performance displays the **`HYPOTHETICAL · PIT`** badge and the **as-of date**; PIT coverage gaps are shown as a chip, never hidden; a backend 400 (PIT-unavailable) renders its detail message, never a silent/biased fallback.
- **Currency:** Indian ₹ formatting with lakh/crore grouping; the curve plots **actual ₹** (both portfolio and Nifty 50 start at `total_capital`), never rebased to 100.
- **Inception:** default `today − 1 year`; date picker **floor `2022-01-01`**.
- **Design tokens only** — colors/blur/radius come from the `@theme` tokens + `.glass` utility (Task 1); no per-component hardcoded hex. Palette: pos `#34d399`, neg `#fb7185`, primary `#7c9cff`, sleeve `#34d399 / #7c9cff / #c084fc / #f5b45a / #2dd4bf`, muted `#93a0bd`, bg `#0a0d18`.
- **Commits** authored `Arpitha B`, empty email, **no `Co-Authored-By`**: `git -c commit.gpgsign=false commit --author="Arpitha B <>"`. Commit from repo root with explicit `frontend/...` paths (or `cd frontend`). Branch off the current branch.
- **Tests are green before commit.** `npm run test -- --run` passes; `npm run build` passes at the gate task.

---

## File Structure

```
tvagent/frontend/
  package.json · tsconfig.json · next.config.ts · postcss.config.mjs · vitest.config.ts · .env.local.example
  app/
    layout.tsx                 # shell: bg gradient+glows, nav, QueryProvider
    globals.css                # tailwind import + @theme glass tokens + .glass utility
    providers.tsx              # TanStack QueryClientProvider
    page.tsx                   # Portfolios home
    portfolio/[id]/page.tsx    # Live Portfolio detail
    portfolio/new/page.tsx     # builder (create)
    portfolio/[id]/edit/page.tsx # builder (edit)
  components/
    nav.tsx                    # left nav + portfolio switcher
    ui/glass.tsx               # GlassPanel, PanelTitle, Badge, Stat
    portfolio-card.tsx         # home card w/ sparkline
    detail/command-bar.tsx · kpi-strip.tsx · perf-curve.tsx
    detail/holdings-table.tsx · sleeve-attribution.tsx · rebalance-panel.tsx · allocation-bar.tsx
    form/portfolio-form.tsx · sleeve-row.tsx · strategy-picker.tsx
  lib/
    types.ts · api.ts · queries.ts · format.ts · validation.ts
  test/
    setup.ts · format.test.ts · validation.test.ts · api.test.ts
    portfolio-card.test.tsx · perf-curve.test.tsx · detail-states.test.tsx · portfolio-form.test.tsx
```

---

### Task 1: Scaffold app + glass tokens + test harness

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/next.config.ts`, `frontend/postcss.config.mjs`, `frontend/vitest.config.ts`, `frontend/.env.local.example`
- Create: `frontend/app/globals.css`, `frontend/app/providers.tsx`, `frontend/app/layout.tsx`, `frontend/app/page.tsx` (placeholder)
- Create: `frontend/test/setup.ts`, `frontend/test/smoke.test.ts`

**Interfaces:**
- Produces: a buildable Next app with the `.glass` utility + `@theme` tokens, a TanStack `Providers` wrapper, and a working Vitest harness (`npm run test`).

- [ ] **Step 1: Write `package.json`**
```json
{
  "name": "tvagent-frontend",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "test": "vitest"
  },
  "dependencies": {
    "next": "^15.1.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "@tanstack/react-query": "^5.62.0",
    "recharts": "^2.13.0"
  },
  "devDependencies": {
    "typescript": "^5.6.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@types/node": "^22.0.0",
    "tailwindcss": "^4.0.0",
    "@tailwindcss/postcss": "^4.0.0",
    "postcss": "^8.4.0",
    "vitest": "^2.1.0",
    "@vitejs/plugin-react": "^4.3.0",
    "@testing-library/react": "^16.1.0",
    "@testing-library/jest-dom": "^6.6.0",
    "jsdom": "^25.0.0"
  }
}
```

- [ ] **Step 2: Write config files**

`frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022", "lib": ["dom", "dom.iterable", "ES2022"],
    "module": "esnext", "moduleResolution": "bundler", "jsx": "preserve",
    "strict": true, "noEmit": true, "esModuleInterop": true, "skipLibCheck": true,
    "incremental": true, "resolveJsonModule": true, "isolatedModules": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```
`frontend/next.config.ts`:
```ts
import type { NextConfig } from "next";
const target = process.env.API_PROXY_TARGET ?? "http://localhost:3001";
const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
};
export default nextConfig;
```
`frontend/postcss.config.mjs`:
```js
export default { plugins: { "@tailwindcss/postcss": {} } };
```
`frontend/vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", globals: true, setupFiles: ["./test/setup.ts"] },
  resolve: { alias: { "@": resolve(__dirname, ".") } },
});
```
`frontend/.env.local.example`:
```
# Backend proxy target for Next.js rewrites (server-side)
API_PROXY_TARGET=http://localhost:3001
```

- [ ] **Step 3: Write `app/globals.css` (glass tokens + utility)**
```css
@import "tailwindcss";

@theme {
  --color-bg: #0a0d18;
  --color-pos: #34d399;
  --color-neg: #fb7185;
  --color-primary: #7c9cff;
  --color-muted: #93a0bd;
  --color-sleeve-1: #34d399;
  --color-sleeve-2: #7c9cff;
  --color-sleeve-3: #c084fc;
  --color-sleeve-4: #f5b45a;
  --color-sleeve-5: #2dd4bf;
  --font-sans: Inter, system-ui, -apple-system, sans-serif;
}

html, body { min-height: 100%; }
body {
  color: #e7edf6;
  font-family: var(--font-sans);
  background:
    radial-gradient(900px 500px at 12% -10%, #2a3566 0%, transparent 55%),
    radial-gradient(800px 500px at 100% 0%, #3a2a5e 0%, transparent 50%),
    radial-gradient(700px 600px at 70% 120%, #123a4a 0%, transparent 55%),
    linear-gradient(160deg, #0c1020, #0a0d18 70%);
  background-attachment: fixed;
}
.num { font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }

@utility glass {
  background: linear-gradient(180deg, rgba(255,255,255,.085), rgba(255,255,255,.03));
  border: 1px solid rgba(255,255,255,.10);
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.16);
  backdrop-filter: blur(14px) saturate(140%);
}
```

- [ ] **Step 4: Write `app/providers.tsx`, `app/layout.tsx`, placeholder `app/page.tsx`**

`app/providers.tsx`:
```tsx
"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
  }));
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
```
`app/layout.tsx`:
```tsx
import "./globals.css";
import type { Metadata } from "next";
import { Providers } from "./providers";
import { Nav } from "@/components/nav";

export const metadata: Metadata = { title: "tvagent — Live Portfolio" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <div style={{ display: "flex", minHeight: "100vh" }}>
            <Nav />
            <main style={{ flex: 1, padding: "18px 22px", maxWidth: 1500, margin: "0 auto" }}>
              {children}
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
```
`app/page.tsx` (placeholder, replaced in Task 5):
```tsx
export default function Home() {
  return <div className="num">tvagent</div>;
}
```
Note: `Nav` is created in Task 9. For Task 1 to compile, create a temporary stub `components/nav.tsx`:
```tsx
export function Nav() { return <nav style={{ width: 150 }} />; }
```

- [ ] **Step 5: Write test harness** (`test/setup.ts`, `test/smoke.test.ts`)
```ts
// test/setup.ts
import "@testing-library/jest-dom/vitest";
```
```ts
// test/smoke.test.ts
import { describe, it, expect } from "vitest";
describe("harness", () => { it("runs", () => { expect(1 + 1).toBe(2); }); });
```

- [ ] **Step 6: Install + verify build + tests**

Run (from `frontend/`): `npm install`
Then: `npm run test -- --run`
Expected: smoke test PASSES.
Then: `npm run build`
Expected: build SUCCEEDS (compiles layout + placeholder home).

- [ ] **Step 7: Write `.gitignore` and commit**

Create `frontend/.gitignore`:
```
node_modules/
.next/
.env.local
*.tsbuildinfo
next-env.d.ts
```
```bash
git add frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/next.config.ts frontend/postcss.config.mjs frontend/vitest.config.ts frontend/.env.local.example frontend/.gitignore frontend/app frontend/components/nav.tsx frontend/test
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(frontend): scaffold Next 15 glass app + tailwind tokens + vitest"
```

---

### Task 2: Types + API client

**Files:**
- Create: `frontend/lib/types.ts`, `frontend/lib/api.ts`
- Test: `frontend/test/api.test.ts`

**Interfaces:**
- Produces TypeScript types mirroring the backend contract: `StrategyRule`, `Strategy`, `Universe`, `Sleeve`, `Portfolio`, `RunResult` (with `summary`, `curve: CurvePoint[]`, `current_book: Holding[]`, `sleeves: SleeveAttribution[]`, `rebalance_actions`).
- Produces an `api` object with async functions: `listPortfolios()`, `getPortfolio(id)`, `createPortfolio(body)`, `updatePortfolio(id, body)`, `deletePortfolio(id)`, `runPortfolio(id, endDate?)`, `listStrategies()`, `createStrategy(body)`, `listUniverses()`. Each throws `ApiError(status, detail)` on non-2xx (so the UI can show the backend's 400 detail).

- [ ] **Step 1: Write the failing test** (`test/api.test.ts`)
```ts
import { describe, it, expect, vi, afterEach } from "vitest";
import { api, ApiError } from "@/lib/api";

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300, status,
    json: async () => body, text: async () => JSON.stringify(body),
  });
}
afterEach(() => vi.restoreAllMocks());

describe("api client", () => {
  it("listPortfolios returns the portfolios array", async () => {
    vi.stubGlobal("fetch", mockFetch(200, { success: true, portfolios: [{ id: "p1", name: "A" }] }));
    const out = await api.listPortfolios();
    expect(out[0].id).toBe("p1");
    expect((fetch as any).mock.calls[0][0]).toBe("/api/portfolio");
  });

  it("runPortfolio passes end_date and unwraps the run payload", async () => {
    vi.stubGlobal("fetch", mockFetch(200, { success: true, summary: { total_return: 42 }, curve: [], current_book: [], sleeves: [], rebalance_actions: { add: [], exit: [] }, portfolio: {} }));
    const out = await api.runPortfolio("p1", "2026-06-20");
    expect(out.summary.total_return).toBe(42);
    expect((fetch as any).mock.calls[0][0]).toContain("end_date=2026-06-20");
  });

  it("throws ApiError with backend detail on 400", async () => {
    vi.stubGlobal("fetch", mockFetch(400, { detail: "sleeve weights must sum to 100 (got 90)" }));
    await expect(api.createPortfolio({} as any)).rejects.toMatchObject({
      status: 400, detail: expect.stringContaining("sum to 100"),
    });
    expect(ApiError).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- --run test/api.test.ts`
Expected: FAIL — cannot resolve `@/lib/api`.

- [ ] **Step 3: Write `lib/types.ts`**
```ts
export interface StrategyRule { id: string; metric: string; operator: string; threshold: number; name: string; }
export interface Strategy { id: string; name: string; description: string; rules: StrategyRule[]; }
export interface Universe { value: string; label: string; stock_count: number; }

export interface Sleeve {
  id: string; label: string; strategy_id: string; weight: number; universe: string;
  rebalance_frequency: string; rebalance_date: string; rebalance_type: string;
  max_holdings: number; momentum_ranking: string; include_brokerage: boolean; exchange: string;
}
export interface Portfolio {
  id?: string; name: string; description: string; total_capital: number; benchmark_symbol: string;
  inception_date: string; point_in_time_membership: boolean; sleeves: Sleeve[];
  created_at?: string; last_modified?: string;
}

export interface CurvePoint { date: string; portfolio_value: number; benchmark_value: number; }
export interface Holding {
  symbol: string; sleeve: string; quantity: number; current_price: number;
  market_value: number; weight: number; pnl: number; pnl_percent: number;
}
export interface SleeveAttribution {
  id: string; label: string; weight: number; total_return: number; contribution: number;
  holdings_count: number; coverage: { total_ever_members: number; covered: number; missing: string[] } | null;
}
export interface RunSummary {
  total_value: number; total_pnl: number; total_return: number; benchmark_return: number;
  alpha: number; max_drawdown: number; sharpe_ratio: number;
}
export interface RunResult {
  portfolio: { id: string; name: string; as_of: string; inception_date: string | null; point_in_time: boolean };
  summary: RunSummary; curve: CurvePoint[]; current_book: Holding[]; sleeves: SleeveAttribution[];
  rebalance_actions: { next_due: string | null; add: { symbol: string; sleeve: string; target_weight: number | null }[]; exit: { symbol: string; sleeve: string }[] };
}
```

- [ ] **Step 4: Write `lib/api.ts`**
```ts
import type { Portfolio, RunResult, Strategy, Universe } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) { super(detail); this.name = "ApiError"; }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep default */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  async listPortfolios(): Promise<Portfolio[]> {
    return (await req<{ portfolios: Portfolio[] }>("/api/portfolio")).portfolios;
  },
  async getPortfolio(id: string): Promise<Portfolio> {
    return (await req<{ portfolio: Portfolio }>(`/api/portfolio/${id}`)).portfolio;
  },
  async createPortfolio(body: Portfolio): Promise<{ portfolio_id: string }> {
    return req("/api/portfolio", { method: "POST", body: JSON.stringify(body) });
  },
  async updatePortfolio(id: string, body: Portfolio): Promise<{ portfolio_id: string }> {
    return req(`/api/portfolio/${id}`, { method: "PUT", body: JSON.stringify(body) });
  },
  async deletePortfolio(id: string): Promise<{ success: boolean }> {
    return req(`/api/portfolio/${id}`, { method: "DELETE" });
  },
  async runPortfolio(id: string, endDate?: string): Promise<RunResult> {
    const q = endDate ? `?end_date=${endDate}` : "";
    return req<RunResult>(`/api/portfolio/${id}/run${q}`, { method: "POST" });
  },
  async listStrategies(): Promise<Strategy[]> {
    return (await req<{ strategies: Strategy[] }>("/api/simulation/strategies")).strategies;
  },
  async createStrategy(body: { name: string; description: string; rules: Strategy["rules"] }): Promise<{ strategy_id: string }> {
    return req("/api/simulation/strategies", { method: "POST", body: JSON.stringify(body) });
  },
  async listUniverses(): Promise<Universe[]> {
    return (await req<{ universes: Universe[] }>("/api/data/universes")).universes;
  },
};
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- --run test/api.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**
```bash
git add frontend/lib/types.ts frontend/lib/api.ts frontend/test/api.test.ts
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(frontend): typed API client + backend contract types"
```

---

### Task 3: Formatting + validation helpers

**Files:**
- Create: `frontend/lib/format.ts`, `frontend/lib/validation.ts`
- Test: `frontend/test/format.test.ts`, `frontend/test/validation.test.ts`

**Interfaces:**
- Produces `format.ts`: `formatINR(n: number): string` (₹ + Indian lakh/crore grouping, e.g. `₹14,21,000`), `formatLakh(n): string` (compact, e.g. `₹14.21L` / `₹1.20Cr`), `formatPct(n, withSign=true): string`, `defaultInception(): string` (today−1y, YYYY-MM-DD), `EARLIEST_INCEPTION = "2022-01-01"`.
- Produces `validation.ts`: `sleeveWeightSum(sleeves: {weight:number}[]): number`, `validatePortfolioForm(p): string[]` (mirrors backend: ≥1 sleeve, weights sum 100±0.5, inception ≥ floor, name non-empty, each sleeve has strategy_id + universe).

- [ ] **Step 1: Write the failing tests**

`test/format.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { formatINR, formatLakh, formatPct } from "@/lib/format";

describe("format", () => {
  it("formats INR with Indian grouping", () => { expect(formatINR(1421000)).toBe("₹14,21,000"); });
  it("formats compact lakh/crore", () => {
    expect(formatLakh(1421000)).toBe("₹14.21L");
    expect(formatLakh(12000000)).toBe("₹1.20Cr");
  });
  it("formats percent with sign", () => {
    expect(formatPct(42.1)).toBe("+42.1%");
    expect(formatPct(-4.2)).toBe("-4.2%");
  });
});
```
`test/validation.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { sleeveWeightSum, validatePortfolioForm } from "@/lib/validation";

const base = {
  name: "P", inception_date: "2024-01-01",
  sleeves: [{ strategy_id: "s1", universe: "NIFTY500", weight: 60 },
            { strategy_id: "s2", universe: "NIFTYMIDCAP150", weight: 40 }],
};

describe("validation", () => {
  it("sums sleeve weights", () => { expect(sleeveWeightSum(base.sleeves)).toBe(100); });
  it("accepts a valid form", () => { expect(validatePortfolioForm(base as any)).toEqual([]); });
  it("flags weight sum != 100", () => {
    const bad = { ...base, sleeves: [{ ...base.sleeves[0], weight: 50 }, base.sleeves[1]] };
    expect(validatePortfolioForm(bad as any).some(e => e.includes("100"))).toBe(true);
  });
  it("flags early inception", () => {
    expect(validatePortfolioForm({ ...base, inception_date: "2021-01-01" } as any)
      .some(e => e.includes("2022"))).toBe(true);
  });
  it("flags empty name and no sleeves", () => {
    expect(validatePortfolioForm({ name: "", inception_date: "2024-01-01", sleeves: [] } as any).length)
      .toBeGreaterThanOrEqual(2);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- --run test/format.test.ts test/validation.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write `lib/format.ts`**
```ts
export const EARLIEST_INCEPTION = "2022-01-01";

export function formatINR(n: number): string {
  return "₹" + Math.round(n).toLocaleString("en-IN");
}
export function formatLakh(n: number): string {
  if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(2)}Cr`;
  if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(2)}L`;
  return formatINR(n);
}
export function formatPct(n: number, withSign = true): string {
  const s = withSign && n > 0 ? "+" : "";
  return `${s}${n.toFixed(1)}%`;
}
export function defaultInception(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}
```

- [ ] **Step 4: Write `lib/validation.ts`**
```ts
import { EARLIEST_INCEPTION } from "./format";

interface SleeveLike { strategy_id?: string; universe?: string; weight: number; }
interface FormLike { name: string; inception_date: string; sleeves: SleeveLike[]; }

export function sleeveWeightSum(sleeves: { weight: number }[]): number {
  return Math.round(sleeves.reduce((a, s) => a + (Number(s.weight) || 0), 0) * 100) / 100;
}

export function validatePortfolioForm(p: FormLike): string[] {
  const errors: string[] = [];
  if (!p.name?.trim()) errors.push("name is required");
  if (!p.sleeves?.length) errors.push("add at least one sleeve");
  if (p.sleeves?.length) {
    const sum = sleeveWeightSum(p.sleeves);
    if (Math.abs(sum - 100) > 0.5) errors.push(`sleeve weights must sum to 100 (got ${sum})`);
    for (const s of p.sleeves) {
      if (!s.strategy_id) errors.push("each sleeve needs a strategy");
      if (!s.universe) errors.push("each sleeve needs a universe");
    }
  }
  if (!p.inception_date || p.inception_date < EARLIEST_INCEPTION)
    errors.push(`inception must be on or after ${EARLIEST_INCEPTION}`);
  return errors;
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test -- --run test/format.test.ts test/validation.test.ts`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**
```bash
git add frontend/lib/format.ts frontend/lib/validation.ts frontend/test/format.test.ts frontend/test/validation.test.ts
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(frontend): ₹ formatting + portfolio form validation helpers"
```

---

### Task 4: Query hooks + glass UI primitives

**Files:**
- Create: `frontend/lib/queries.ts`, `frontend/components/ui/glass.tsx`
- Test: `frontend/test/portfolio-card.test.tsx` is Task 5; here add no new test file (primitives are exercised by later render tests). Add a minimal render test `frontend/test/glass.test.tsx`.

**Interfaces:**
- Produces `queries.ts` hooks: `usePortfolios()`, `usePortfolio(id)`, `useRunPortfolio(id, endDate)`, `useStrategies()`, `useUniverses()` (all `useQuery`); `useCreatePortfolio()`, `useUpdatePortfolio()`, `useDeletePortfolio()`, `useCreateStrategy()` (all `useMutation`, invalidating relevant keys). Query keys: `["portfolios"]`, `["portfolio", id]`, `["run", id, endDate]`, `["strategies"]`, `["universes"]`.
- Produces `glass.tsx`: `<GlassPanel>` (div with `glass` class + padding), `<PanelTitle>` (uppercase micro-label), `<Badge tone>` (`warn|pos|neg|muted`), `<Stat label value sub tone>`.

- [ ] **Step 1: Write the failing test** (`test/glass.test.tsx`)
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Stat, Badge } from "@/components/ui/glass";

describe("glass primitives", () => {
  it("Stat shows label and value", () => {
    render(<Stat label="Sharpe" value="1.07" />);
    expect(screen.getByText("Sharpe")).toBeInTheDocument();
    expect(screen.getByText("1.07")).toBeInTheDocument();
  });
  it("Badge renders its text", () => {
    render(<Badge tone="warn">HYPOTHETICAL · PIT</Badge>);
    expect(screen.getByText("HYPOTHETICAL · PIT")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- --run test/glass.test.tsx`
Expected: FAIL — `@/components/ui/glass` not found.

- [ ] **Step 3: Write `components/ui/glass.tsx`**
```tsx
import type { ReactNode } from "react";

type Tone = "pos" | "neg" | "muted" | "warn" | "default";
const toneColor: Record<Tone, string> = {
  pos: "var(--color-pos)", neg: "var(--color-neg)", muted: "var(--color-muted)",
  warn: "#f3c277", default: "#e7edf6",
};

export function GlassPanel({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return <div className="glass" style={{ padding: "13px 15px", ...style }}>{children}</div>;
}

export function PanelTitle({ children }: { children: ReactNode }) {
  return <div style={{ fontSize: 9, letterSpacing: ".10em", textTransform: "uppercase",
    color: "var(--color-muted)", fontWeight: 600, marginBottom: 8 }}>{children}</div>;
}

export function Badge({ children, tone = "muted" }: { children: ReactNode; tone?: Tone }) {
  const warn = tone === "warn";
  return <span style={{ fontSize: 9, padding: "3px 9px", borderRadius: 999, letterSpacing: ".04em",
    color: toneColor[tone], background: warn ? "rgba(245,180,90,.14)" : "rgba(255,255,255,.06)",
    border: `1px solid ${warn ? "rgba(245,180,90,.28)" : "rgba(255,255,255,.10)"}` }}>{children}</span>;
}

export function Stat({ label, value, sub, tone = "default" }:
  { label: string; value: string; sub?: string; tone?: Tone }) {
  return (
    <div style={{ padding: "12px 14px" }}>
      <div style={{ fontSize: 9, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--color-muted)" }}>{label}</div>
      <div className="num" style={{ fontSize: 18, fontWeight: 700, marginTop: 3, color: toneColor[tone] }}>{value}</div>
      {sub && <div className="num" style={{ fontSize: 10, color: "var(--color-muted)" }}>{sub}</div>}
    </div>
  );
}
```

- [ ] **Step 4: Write `lib/queries.ts`**
```tsx
"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { Portfolio } from "./types";

export const usePortfolios = () => useQuery({ queryKey: ["portfolios"], queryFn: api.listPortfolios });
export const usePortfolio = (id: string) =>
  useQuery({ queryKey: ["portfolio", id], queryFn: () => api.getPortfolio(id), enabled: !!id });
export const useRunPortfolio = (id: string, endDate?: string) =>
  useQuery({ queryKey: ["run", id, endDate ?? "today"], queryFn: () => api.runPortfolio(id, endDate), enabled: !!id });
export const useStrategies = () => useQuery({ queryKey: ["strategies"], queryFn: api.listStrategies });
export const useUniverses = () => useQuery({ queryKey: ["universes"], queryFn: api.listUniverses });

export function useCreatePortfolio() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: Portfolio) => api.createPortfolio(b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolios"] }) });
}
export function useUpdatePortfolio(id: string) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: Portfolio) => api.updatePortfolio(id, b),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["portfolios"] }); qc.invalidateQueries({ queryKey: ["portfolio", id] }); } });
}
export function useDeletePortfolio() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => api.deletePortfolio(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolios"] }) });
}
export function useCreateStrategy() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.createStrategy,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }) });
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- --run test/glass.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**
```bash
git add frontend/lib/queries.ts frontend/components/ui/glass.tsx frontend/test/glass.test.tsx
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(frontend): query hooks + glass UI primitives"
```

---

### Task 5: Portfolios home

**Files:**
- Create: `frontend/components/portfolio-card.tsx`
- Modify: `frontend/app/page.tsx` (replace placeholder)
- Test: `frontend/test/portfolio-card.test.tsx`

**Interfaces:**
- Consumes: `usePortfolios` (Task 4), `Portfolio` type, `GlassPanel`, `formatLakh`.
- Produces: `<PortfolioCard portfolio={Portfolio} />` — glass card showing name, sleeve count, sleeve-color dots, total capital, and a "Open" link to `/portfolio/{id}`. `app/page.tsx` — `"use client"` page rendering a responsive grid of cards, a `+ New portfolio` link to `/portfolio/new`, an **empty state** ("Create your first portfolio"), and a loading skeleton.

- [ ] **Step 1: Write the failing test** (`test/portfolio-card.test.tsx`)
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PortfolioCard } from "@/components/portfolio-card";
import type { Portfolio } from "@/lib/types";

const p: Portfolio = {
  id: "p1", name: "Growth Book", description: "", total_capital: 1000000,
  benchmark_symbol: "Nifty 50", inception_date: "2024-01-01", point_in_time_membership: true,
  sleeves: [
    { id: "s1", label: "Mid", strategy_id: "x", weight: 60, universe: "NIFTYMIDCAP150",
      rebalance_frequency: "monthly", rebalance_date: "first", rebalance_type: "equal_weight",
      max_holdings: 10, momentum_ranking: "20_day_return", include_brokerage: true, exchange: "NSE" },
    { id: "s2", label: "Micro", strategy_id: "y", weight: 40, universe: "NIFTYMICROCAP250",
      rebalance_frequency: "weekly", rebalance_date: "first", rebalance_type: "equal_weight",
      max_holdings: 10, momentum_ranking: "20_day_return", include_brokerage: true, exchange: "NSE" },
  ],
};

describe("PortfolioCard", () => {
  it("shows name, sleeve count and links to detail", () => {
    render(<PortfolioCard portfolio={p} />);
    expect(screen.getByText("Growth Book")).toBeInTheDocument();
    expect(screen.getByText(/2 sleeves/)).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/portfolio/p1");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- --run test/portfolio-card.test.tsx`
Expected: FAIL — `@/components/portfolio-card` not found.

- [ ] **Step 3: Write `components/portfolio-card.tsx`**
```tsx
import Link from "next/link";
import type { Portfolio } from "@/lib/types";
import { GlassPanel } from "@/components/ui/glass";
import { formatLakh } from "@/lib/format";

const SLEEVE_COLORS = ["var(--color-sleeve-1)", "var(--color-sleeve-2)", "var(--color-sleeve-3)",
  "var(--color-sleeve-4)", "var(--color-sleeve-5)"];

export function PortfolioCard({ portfolio }: { portfolio: Portfolio }) {
  return (
    <Link href={`/portfolio/${portfolio.id}`} style={{ textDecoration: "none", color: "inherit" }}>
      <GlassPanel style={{ minHeight: 110 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{portfolio.name}</div>
        <div style={{ display: "flex", gap: 4, margin: "10px 0" }}>
          {portfolio.sleeves.map((s, i) => (
            <span key={s.id} title={s.label} style={{ width: 9, height: 9, borderRadius: 2,
              background: SLEEVE_COLORS[i % SLEEVE_COLORS.length] }} />
          ))}
        </div>
        <div className="num" style={{ fontSize: 12, color: "var(--color-muted)" }}>
          {portfolio.sleeves.length} sleeves · {formatLakh(portfolio.total_capital)}
        </div>
      </GlassPanel>
    </Link>
  );
}
```

- [ ] **Step 4: Write `app/page.tsx`**
```tsx
"use client";
import Link from "next/link";
import { usePortfolios } from "@/lib/queries";
import { PortfolioCard } from "@/components/portfolio-card";
import { GlassPanel } from "@/components/ui/glass";

export default function Home() {
  const { data: portfolios, isLoading, error } = usePortfolios();
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, flex: 1 }}>Portfolios</h1>
        <Link href="/portfolio/new" style={{ textDecoration: "none" }}>
          <span style={{ fontSize: 12, padding: "7px 13px", borderRadius: 8, color: "#dce6ff",
            background: "rgba(124,156,255,.20)", border: "1px solid rgba(124,156,255,.34)" }}>+ New portfolio</span>
        </Link>
      </div>

      {isLoading && <GlassPanel><span style={{ color: "var(--color-muted)" }}>Loading…</span></GlassPanel>}
      {error && <GlassPanel><span style={{ color: "var(--color-neg)" }}>Failed to load portfolios.</span></GlassPanel>}
      {portfolios && portfolios.length === 0 && (
        <GlassPanel style={{ textAlign: "center", padding: "40px" }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>No portfolios yet</div>
          <div style={{ color: "var(--color-muted)", margin: "8px 0 16px" }}>Create your first portfolio to start.</div>
          <Link href="/portfolio/new" style={{ color: "#dce6ff" }}>+ New portfolio</Link>
        </GlassPanel>
      )}
      {portfolios && portfolios.length > 0 && (
        <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))" }}>
          {portfolios.map((p) => <PortfolioCard key={p.id} portfolio={p} />)}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- --run test/portfolio-card.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 6: Commit**
```bash
git add frontend/components/portfolio-card.tsx frontend/app/page.tsx frontend/test/portfolio-card.test.tsx
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(frontend): Portfolios home — cards, empty + loading states"
```

---

### Task 6: Live Portfolio detail — command bar, KPIs, ₹ curve

**Files:**
- Create: `frontend/components/detail/command-bar.tsx`, `frontend/components/detail/kpi-strip.tsx`, `frontend/components/detail/perf-curve.tsx`
- Create: `frontend/app/portfolio/[id]/page.tsx`
- Test: `frontend/test/perf-curve.test.tsx`, `frontend/test/detail-states.test.tsx`

**Interfaces:**
- Consumes: `useRunPortfolio`, `usePortfolio` (Task 4), `RunResult`/`CurvePoint` types, `formatLakh`/`formatPct`, glass primitives, `ApiError`.
- Produces:
  - `<PerfCurve curve={CurvePoint[]} />` — Recharts area chart, two ₹ series (portfolio = pos color, benchmark = muted), ₹-axis via `formatLakh`, tooltip. Renders a `data-testid="perf-curve"` wrapper.
  - `<KpiStrip summary={RunSummary} />` — 4 `Stat`s (Invested→Now, Nifty same-₹, Alpha, Sharpe·MaxDD).
  - `<CommandBar name as_of onRangeChange range />` — title, `HYPOTHETICAL · PIT` Badge, range segmented control (`1Y` | `Since 2022` | `Custom`), as-of, Run-live + Edit buttons.
  - `app/portfolio/[id]/page.tsx` — orchestrates: range state → `endDate`, calls `useRunPortfolio`, renders command bar + a two-column grid (left: KPI strip, curve, and a holdings placeholder filled in Task 7). Handles **loading** (skeleton), **error** (shows `ApiError.detail`, esp. PIT-unavailable), and renders coverage info from `sleeves`.

- [ ] **Step 1: Write the failing tests**

`test/perf-curve.test.tsx`:
```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
// Recharts needs sizing; mock ResponsiveContainer to a fixed box.
vi.mock("recharts", async (orig) => {
  const actual = await orig<typeof import("recharts")>();
  return { ...actual, ResponsiveContainer: ({ children }: any) =>
    <div style={{ width: 600, height: 300 }}>{children}</div> };
});
import { PerfCurve } from "@/components/detail/perf-curve";

describe("PerfCurve", () => {
  it("renders a chart wrapper for given points", () => {
    render(<PerfCurve curve={[
      { date: "2024-01-01", portfolio_value: 1000000, benchmark_value: 1000000 },
      { date: "2024-06-30", portfolio_value: 1421000, benchmark_value: 1163000 },
    ]} />);
    expect(screen.getByTestId("perf-curve")).toBeInTheDocument();
  });
});
```
`test/detail-states.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { KpiStrip } from "@/components/detail/kpi-strip";

describe("KpiStrip", () => {
  it("renders return and alpha", () => {
    render(<KpiStrip summary={{ total_value: 1421000, total_pnl: 421000, total_return: 42.1,
      benchmark_return: 16.3, alpha: 25.8, max_drawdown: -24.6, sharpe_ratio: 1.07 }} />);
    expect(screen.getByText("+42.1%")).toBeInTheDocument();
    expect(screen.getByText("+25.8%")).toBeInTheDocument();
    expect(screen.getByText("1.07")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- --run test/perf-curve.test.tsx test/detail-states.test.tsx`
Expected: FAIL — components not found.

- [ ] **Step 3: Write `components/detail/perf-curve.tsx`**
```tsx
"use client";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { CurvePoint } from "@/lib/types";
import { formatLakh } from "@/lib/format";

export function PerfCurve({ curve }: { curve: CurvePoint[] }) {
  return (
    <div data-testid="perf-curve" style={{ width: "100%", height: 240 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={curve} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="pf" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#34d399" stopOpacity={0.34} />
              <stop offset="1" stopColor="#34d399" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="rgba(255,255,255,.06)" vertical={false} />
          <XAxis dataKey="date" tick={{ fill: "#93a0bd", fontSize: 10 }} minTickGap={40} stroke="rgba(255,255,255,.1)" />
          <YAxis tickFormatter={(v) => formatLakh(v)} tick={{ fill: "#93a0bd", fontSize: 10 }}
            width={60} stroke="rgba(255,255,255,.1)" domain={["auto", "auto"]} />
          <Tooltip formatter={(v: number) => formatLakh(v)}
            contentStyle={{ background: "#11162a", border: "1px solid rgba(255,255,255,.12)", borderRadius: 8, fontSize: 12 }} />
          <Area type="monotone" dataKey="benchmark_value" name="Nifty 50" stroke="#7c8bb0" strokeWidth={1.6} fill="none" />
          <Area type="monotone" dataKey="portfolio_value" name="Portfolio" stroke="#34d399" strokeWidth={2} fill="url(#pf)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 4: Write `components/detail/kpi-strip.tsx`**
```tsx
import type { RunSummary } from "@/lib/types";
import { Stat } from "@/components/ui/glass";
import { formatLakh, formatPct } from "@/lib/format";

export function KpiStrip({ summary }: { summary: RunSummary }) {
  return (
    <div className="glass" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", padding: 0, overflow: "hidden" }}>
      <Stat label="Invested → Now" value={formatLakh(summary.total_value)} sub={formatPct(summary.total_return)} tone={summary.total_return >= 0 ? "pos" : "neg"} />
      <Stat label="Nifty 50 (same ₹)" value={formatLakh(summary.total_value / (1 + summary.total_return / 100) * (1 + summary.benchmark_return / 100))} sub={formatPct(summary.benchmark_return)} tone="muted" />
      <Stat label="Alpha vs Nifty 50" value={formatPct(summary.alpha)} tone={summary.alpha >= 0 ? "pos" : "neg"} />
      <Stat label="Sharpe · MaxDD" value={summary.sharpe_ratio.toFixed(2)} sub={formatPct(summary.max_drawdown, false)} tone="default" />
    </div>
  );
}
```
Note: the Nifty-same-₹ value derives the benchmark's current ₹ from `total_value` and the two returns — equivalent to `total_capital × (1 + benchmark_return/100)`; this avoids needing `total_capital` separately in the strip.

- [ ] **Step 5: Write `components/detail/command-bar.tsx`**
```tsx
"use client";
import Link from "next/link";
import { Badge } from "@/components/ui/glass";

export type RangeKey = "1Y" | "SINCE_2022" | "CUSTOM";

export function CommandBar({ name, asOf, range, onRange, onRun, editHref }:
  { name: string; asOf: string; range: RangeKey; onRange: (r: RangeKey) => void; onRun: () => void; editHref: string }) {
  const seg = (key: RangeKey, label: string) => (
    <span onClick={() => onRange(key)} style={{ padding: "3px 9px", borderRadius: 6, cursor: "pointer",
      color: range === key ? "#dce6ff" : "#9fb0d4",
      background: range === key ? "rgba(124,156,255,.28)" : "transparent" }}>{label}</span>
  );
  return (
    <div className="glass" style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: 15, fontWeight: 700 }}>{name}</div>
      <Badge tone="warn">HYPOTHETICAL · PIT</Badge>
      <span style={{ flex: 1 }} />
      <div style={{ display: "flex", gap: 2, padding: 2, borderRadius: 8, fontSize: 10,
        background: "rgba(255,255,255,.05)", border: "1px solid rgba(255,255,255,.08)" }}>
        {seg("1Y", "1Y")}{seg("SINCE_2022", "Since 2022")}{seg("CUSTOM", "Custom")}
      </div>
      <span className="num" style={{ fontSize: 10, color: "var(--color-muted)" }}>As of {asOf}</span>
      <button onClick={onRun} style={{ fontSize: 11, padding: "5px 11px", borderRadius: 8, cursor: "pointer",
        color: "#dce6ff", background: "rgba(124,156,255,.20)", border: "1px solid rgba(124,156,255,.34)" }}>↻ Run live</button>
      <Link href={editHref} style={{ fontSize: 11, padding: "5px 11px", borderRadius: 8, color: "#cdd7ee",
        background: "rgba(255,255,255,.06)", border: "1px solid rgba(255,255,255,.12)", textDecoration: "none" }}>Edit</Link>
    </div>
  );
}
```

- [ ] **Step 6: Write `app/portfolio/[id]/page.tsx`**
```tsx
"use client";
import { use, useState } from "react";
import { usePortfolio, useRunPortfolio } from "@/lib/queries";
import { CommandBar, type RangeKey } from "@/components/detail/command-bar";
import { KpiStrip } from "@/components/detail/kpi-strip";
import { PerfCurve } from "@/components/detail/perf-curve";
import { GlassPanel } from "@/components/ui/glass";
import { defaultInception, EARLIEST_INCEPTION } from "@/lib/format";
import { ApiError } from "@/lib/api";

export default function PortfolioDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [range, setRange] = useState<RangeKey>("1Y");
  // Range maps to the run window; backend uses portfolio.inception_date as start, so range
  // here only drives the as-of end_date today (1Y/Since-2022 affect inception via Edit). For v1,
  // end_date is always today; range is a display affordance + future hook.
  const { data: portfolio } = usePortfolio(id);
  const { data: run, isLoading, error } = useRunPortfolio(id);

  return (
    <div>
      <CommandBar name={portfolio?.name ?? "Portfolio"} asOf={run?.portfolio.as_of ?? "—"}
        range={range} onRange={setRange} onRun={() => location.reload()} editHref={`/portfolio/${id}/edit`} />

      {isLoading && <GlassPanel><span style={{ color: "var(--color-muted)" }}>Running sleeves… this can take a few seconds.</span></GlassPanel>}
      {error && (
        <GlassPanel>
          <div style={{ color: "var(--color-neg)", fontWeight: 600 }}>Run failed</div>
          <div style={{ color: "var(--color-muted)", marginTop: 6 }}>
            {error instanceof ApiError ? error.detail : "Unexpected error."}
          </div>
        </GlassPanel>
      )}

      {run && (
        <div style={{ display: "grid", gridTemplateColumns: "2.1fr 1fr", gap: 12 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <KpiStrip summary={run.summary} />
            <GlassPanel>
              <div style={{ fontSize: 9, letterSpacing: ".10em", textTransform: "uppercase",
                color: "var(--color-muted)", fontWeight: 600, marginBottom: 8 }}>
                Portfolio ₹ vs Nifty 50 · since {run.portfolio.inception_date}
              </div>
              <PerfCurve curve={run.curve} />
            </GlassPanel>
            <div id="holdings-slot" />{/* filled in Task 7 */}
          </div>
          <div id="right-slot" style={{ display: "flex", flexDirection: "column", gap: 12 }} />
          {/* right column panels added in Task 7 */}
        </div>
      )}
    </div>
  );
}
```
Note on range/inception: v1 always runs to *today* with the portfolio's stored `inception_date`; the range control is wired for display and is the hook for a future custom-window run. `EARLIEST_INCEPTION` is imported for the builder reuse and to keep the floor referenced; if unused here, drop the import to satisfy lint.

- [ ] **Step 7: Run tests to verify they pass**

Run: `npm run test -- --run test/perf-curve.test.tsx test/detail-states.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 8: Commit**
```bash
git add frontend/components/detail/command-bar.tsx frontend/components/detail/kpi-strip.tsx frontend/components/detail/perf-curve.tsx frontend/app/portfolio frontend/test/perf-curve.test.tsx frontend/test/detail-states.test.tsx
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(frontend): Live Portfolio detail — command bar, KPIs, ₹ curve"
```

---

### Task 7: Detail — holdings, attribution, rebalance, allocation

**Files:**
- Create: `frontend/components/detail/holdings-table.tsx`, `frontend/components/detail/sleeve-attribution.tsx`, `frontend/components/detail/rebalance-panel.tsx`, `frontend/components/detail/allocation-bar.tsx`
- Modify: `frontend/app/portfolio/[id]/page.tsx` (fill holdings slot + right column)
- Test: `frontend/test/detail-states.test.tsx` (append)

**Interfaces:**
- Consumes: `Holding`, `SleeveAttribution`, `RunResult["rebalance_actions"]` types; glass primitives; `formatLakh`/`formatPct`.
- Produces:
  - `<HoldingsTable book={Holding[]} />` — table with client-side sort (default by market_value desc), columns Symbol/Sleeve/Wt/Mkt value/P&L; P&L colored.
  - `<SleeveAttributionPanel sleeves={SleeveAttribution[]} />` — rows weight·return·contribution + a coverage chip per the worst coverage.
  - `<RebalancePanel actions={...} />` — add (pos) / exit (neg) rows tagged by sleeve; "next due" or "—".
  - `<AllocationBar sleeves={SleeveAttribution[]} totalCapital={number} />` — stacked weight bar + legend.

- [ ] **Step 1: Write the failing test** (append to `test/detail-states.test.tsx`)
```tsx
import { HoldingsTable } from "@/components/detail/holdings-table";
import { SleeveAttributionPanel } from "@/components/detail/sleeve-attribution";

describe("HoldingsTable", () => {
  it("renders holdings sorted by market value", () => {
    render(<HoldingsTable book={[
      { symbol: "BSE", sleeve: "Mid", quantity: 1, current_price: 1, market_value: 39780, weight: 2.8, pnl: 100, pnl_percent: 8.3 },
      { symbol: "DIXON", sleeve: "Mid", quantity: 1, current_price: 1, market_value: 44020, weight: 3.1, pnl: 500, pnl_percent: 12.4 },
    ]} />);
    const rows = screen.getAllByRole("row");
    // header + 2 data rows; first data row is DIXON (higher mkt value)
    expect(rows[1]).toHaveTextContent("DIXON");
  });
});

describe("SleeveAttributionPanel", () => {
  it("shows contribution and coverage", () => {
    render(<SleeveAttributionPanel sleeves={[
      { id: "s1", label: "Mid", weight: 60, total_return: 58, contribution: 34.8, holdings_count: 10,
        coverage: { total_ever_members: 200, covered: 192, missing: ["X","Y","Z","W","V","U","T","B"] } },
    ]} />);
    expect(screen.getByText(/\+34.8/)).toBeInTheDocument();
    expect(screen.getByText(/coverage/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- --run test/detail-states.test.tsx`
Expected: FAIL — new components not found.

- [ ] **Step 3: Write the four components**

`components/detail/holdings-table.tsx`:
```tsx
"use client";
import { useState } from "react";
import type { Holding } from "@/lib/types";
import { PanelTitle, GlassPanel } from "@/components/ui/glass";
import { formatINR, formatPct } from "@/lib/format";

export function HoldingsTable({ book }: { book: Holding[] }) {
  const [desc, setDesc] = useState(true);
  const rows = [...book].sort((a, b) => (desc ? b.market_value - a.market_value : a.market_value - b.market_value));
  const th = { textAlign: "right" as const, fontSize: 8.5, letterSpacing: ".06em", textTransform: "uppercase" as const, color: "#7d8cae", padding: "0 0 7px" };
  return (
    <GlassPanel>
      <PanelTitle>Current book · {book.length} holdings</PanelTitle>
      <table className="num" style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
        <thead><tr>
          <th style={{ ...th, textAlign: "left" }}>Symbol</th>
          <th style={th}>Sleeve</th>
          <th style={{ ...th, cursor: "pointer" }} onClick={() => setDesc(!desc)}>Wt</th>
          <th style={th}>Mkt value</th><th style={th}>P&amp;L</th>
        </tr></thead>
        <tbody>
          {rows.map((h) => (
            <tr key={h.symbol}>
              <td style={{ padding: "6px 0", borderTop: "1px solid rgba(255,255,255,.055)", fontWeight: 600 }}>{h.symbol}</td>
              <td style={{ textAlign: "right", color: "var(--color-muted)", borderTop: "1px solid rgba(255,255,255,.055)" }}>{h.sleeve}</td>
              <td style={{ textAlign: "right", borderTop: "1px solid rgba(255,255,255,.055)" }}>{h.weight.toFixed(1)}%</td>
              <td style={{ textAlign: "right", borderTop: "1px solid rgba(255,255,255,.055)" }}>{formatINR(h.market_value)}</td>
              <td style={{ textAlign: "right", borderTop: "1px solid rgba(255,255,255,.055)", color: h.pnl >= 0 ? "var(--color-pos)" : "var(--color-neg)" }}>{formatPct(h.pnl_percent)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </GlassPanel>
  );
}
```
`components/detail/sleeve-attribution.tsx`:
```tsx
import type { SleeveAttribution } from "@/lib/types";
import { GlassPanel, PanelTitle } from "@/components/ui/glass";
import { formatPct } from "@/lib/format";

export function SleeveAttributionPanel({ sleeves }: { sleeves: SleeveAttribution[] }) {
  const worst = sleeves.reduce<{ covered: number; total: number; missing: number }>((acc, s) => {
    if (!s.coverage) return acc;
    return { covered: acc.covered + s.coverage.covered, total: acc.total + s.coverage.total_ever_members,
      missing: acc.missing + s.coverage.missing.length };
  }, { covered: 0, total: 0, missing: 0 });
  const pct = worst.total ? Math.round(worst.covered / worst.total * 100) : null;
  return (
    <GlassPanel>
      <PanelTitle>Sleeve attribution</PanelTitle>
      <table className="num" style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
        <tbody>
          {sleeves.map((s) => (
            <tr key={s.id}>
              <td style={{ padding: "5px 0", fontWeight: 600 }}>{s.label}</td>
              <td style={{ textAlign: "right", color: "var(--color-muted)" }}>{s.weight}%</td>
              <td style={{ textAlign: "right", color: s.total_return >= 0 ? "var(--color-pos)" : "var(--color-neg)" }}>{formatPct(s.total_return)}</td>
              <td style={{ textAlign: "right", color: s.contribution >= 0 ? "var(--color-pos)" : "var(--color-neg)" }}>{formatPct(s.contribution)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {pct !== null && (
        <div style={{ fontSize: 10, color: "var(--color-muted)", marginTop: 8 }}>
          PIT coverage {pct}% · {worst.missing} ever-members unpriced
        </div>
      )}
    </GlassPanel>
  );
}
```
`components/detail/rebalance-panel.tsx`:
```tsx
import type { RunResult } from "@/lib/types";
import { GlassPanel, PanelTitle } from "@/components/ui/glass";

export function RebalancePanel({ actions }: { actions: RunResult["rebalance_actions"] }) {
  const Row = ({ op, sym, sleeve, tone }: { op: string; sym: string; sleeve: string; tone: string }) => (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 9px", borderRadius: 8,
      background: "rgba(255,255,255,.04)", border: "1px solid rgba(255,255,255,.06)" }}>
      <span style={{ fontWeight: 700, width: 14, textAlign: "center", color: tone }}>{op}</span>
      <span style={{ fontWeight: 600 }}>{sym}</span>
      <span style={{ fontSize: 10, color: "var(--color-muted)" }}>{sleeve}</span>
    </div>
  );
  return (
    <GlassPanel>
      <PanelTitle>Rebalance{actions.next_due ? ` · next due ${actions.next_due}` : ""}</PanelTitle>
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {actions.add.map((a, i) => <Row key={`a${i}`} op="+" sym={a.symbol} sleeve={a.sleeve} tone="var(--color-pos)" />)}
        {actions.exit.map((e, i) => <Row key={`e${i}`} op="−" sym={e.symbol} sleeve={e.sleeve} tone="var(--color-neg)" />)}
        {actions.add.length === 0 && actions.exit.length === 0 &&
          <span style={{ fontSize: 11, color: "var(--color-muted)" }}>No changes at the latest rebalance.</span>}
      </div>
    </GlassPanel>
  );
}
```
`components/detail/allocation-bar.tsx`:
```tsx
import type { SleeveAttribution } from "@/lib/types";
import { GlassPanel, PanelTitle } from "@/components/ui/glass";
import { formatLakh } from "@/lib/format";

const COLORS = ["var(--color-sleeve-1)", "var(--color-sleeve-2)", "var(--color-sleeve-3)",
  "var(--color-sleeve-4)", "var(--color-sleeve-5)"];

export function AllocationBar({ sleeves, totalCapital }: { sleeves: SleeveAttribution[]; totalCapital: number }) {
  return (
    <GlassPanel>
      <PanelTitle>Capital allocation</PanelTitle>
      <div style={{ display: "flex", height: 14, borderRadius: 7, overflow: "hidden", marginBottom: 8 }}>
        {sleeves.map((s, i) => <div key={s.id} style={{ width: `${s.weight}%`, background: COLORS[i % COLORS.length] }} />)}
      </div>
      <div className="num" style={{ fontSize: 10, color: "var(--color-muted)" }}>
        {sleeves.map((s) => `${s.label} ${s.weight}%`).join(" · ")} = {formatLakh(totalCapital)}
      </div>
    </GlassPanel>
  );
}
```

- [ ] **Step 4: Wire them into `app/portfolio/[id]/page.tsx`**

Add imports:
```tsx
import { HoldingsTable } from "@/components/detail/holdings-table";
import { SleeveAttributionPanel } from "@/components/detail/sleeve-attribution";
import { RebalancePanel } from "@/components/detail/rebalance-panel";
import { AllocationBar } from "@/components/detail/allocation-bar";
```
Replace the `<div id="holdings-slot" />` line with:
```tsx
            <HoldingsTable book={run.current_book} />
```
Replace the empty right-column div with:
```tsx
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <SleeveAttributionPanel sleeves={run.sleeves} />
            <RebalancePanel actions={run.rebalance_actions} />
            <AllocationBar sleeves={run.sleeves} totalCapital={portfolio?.total_capital ?? run.summary.total_value} />
          </div>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test -- --run test/detail-states.test.tsx`
Expected: PASS (3 tests total in file).

- [ ] **Step 6: Commit**
```bash
git add frontend/components/detail frontend/app/portfolio/[id]/page.tsx frontend/test/detail-states.test.tsx
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(frontend): detail — holdings, sleeve attribution, rebalance, allocation"
```

---

### Task 8: Portfolio builder / editor

**Files:**
- Create: `frontend/components/form/strategy-picker.tsx`, `frontend/components/form/sleeve-row.tsx`, `frontend/components/form/portfolio-form.tsx`
- Create: `frontend/app/portfolio/new/page.tsx`, `frontend/app/portfolio/[id]/edit/page.tsx`
- Test: `frontend/test/portfolio-form.test.tsx`

**Interfaces:**
- Consumes: `useStrategies`, `useUniverses`, `useCreatePortfolio`, `useUpdatePortfolio`, `useCreateStrategy` (Task 4); `validatePortfolioForm`, `sleeveWeightSum`, `defaultInception`, `EARLIEST_INCEPTION`; `Portfolio`/`Sleeve` types.
- Produces:
  - `<StrategyPicker value onChange />` — select from `useStrategies` + an "＋ new" inline mini-form (name + one rule metric/operator/threshold) that calls `useCreateStrategy` and selects the result.
  - `<SleeveRow sleeve onChange onRemove strategies universes />` — editable row: label, strategy picker, universe select, rebalance freq/type, max_holdings, momentum_ranking, weight.
  - `<PortfolioForm initial? onSaved />` — full form: name, total_capital, inception (floor `EARLIEST_INCEPTION`), PIT toggle, sleeve rows (+add), live weight-sum indicator, inline error list from `validatePortfolioForm`, Save (create or update). Used by both pages.

- [ ] **Step 1: Write the failing test** (`test/portfolio-form.test.tsx`)
```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PortfolioForm } from "@/components/form/portfolio-form";

vi.mock("@/lib/queries", async (orig) => {
  const actual = await orig<typeof import("@/lib/queries")>();
  return {
    ...actual,
    useStrategies: () => ({ data: [{ id: "s1", name: "Aligned", description: "", rules: [] }] }),
    useUniverses: () => ({ data: [{ value: "NIFTY500", label: "NIFTY500 (500)", stock_count: 500 }] }),
    useCreatePortfolio: () => ({ mutateAsync: vi.fn().mockResolvedValue({ portfolio_id: "p9" }), isPending: false }),
    useUpdatePortfolio: () => ({ mutateAsync: vi.fn(), isPending: false }),
    useCreateStrategy: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;
}

describe("PortfolioForm", () => {
  it("blocks save when weights do not sum to 100", () => {
    render(wrap(<PortfolioForm onSaved={() => {}} />));
    // default form has one sleeve at weight 100 → add a second sleeve to break the sum
    fireEvent.click(screen.getByText(/add sleeve/i));
    fireEvent.click(screen.getByText(/save/i));
    expect(screen.getByText(/sum to 100/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- --run test/portfolio-form.test.tsx`
Expected: FAIL — `@/components/form/portfolio-form` not found.

- [ ] **Step 3: Write `components/form/strategy-picker.tsx`**
```tsx
"use client";
import { useState } from "react";
import { useStrategies, useCreateStrategy } from "@/lib/queries";

const SELECT = { padding: "5px 8px", borderRadius: 7, background: "rgba(255,255,255,.06)",
  border: "1px solid rgba(255,255,255,.12)", color: "#e7edf6", fontSize: 12 } as const;

export function StrategyPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const { data: strategies } = useStrategies();
  const createStrategy = useCreateStrategy();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("truevx_score");
  const [op, setOp] = useState(">");
  const [threshold, setThreshold] = useState(90);

  async function add() {
    const res = await createStrategy.mutateAsync({ name, description: "",
      rules: [{ id: "r1", metric, operator: op, threshold, name: `${metric} ${op} ${threshold}` }] });
    onChange(res.strategy_id);
    setAdding(false); setName("");
  }

  if (adding) {
    return (
      <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
        <input value={name} placeholder="name" onChange={(e) => setName(e.target.value)} style={{ ...SELECT, width: 90 }} />
        <select value={metric} onChange={(e) => setMetric(e.target.value)} style={SELECT}>
          {["truevx_score", "mean_short", "mean_mid", "mean_long"].map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select value={op} onChange={(e) => setOp(e.target.value)} style={SELECT}>
          {[">", "<", ">=", "<="].map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <input type="number" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} style={{ ...SELECT, width: 60 }} />
        <button onClick={add} disabled={!name} style={SELECT}>✓</button>
      </span>
    );
  }
  return (
    <span style={{ display: "inline-flex", gap: 4 }}>
      <select value={value} onChange={(e) => onChange(e.target.value)} style={SELECT}>
        <option value="">— strategy —</option>
        {strategies?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
      <button onClick={() => setAdding(true)} style={SELECT}>＋ new</button>
    </span>
  );
}
```

- [ ] **Step 4: Write `components/form/sleeve-row.tsx`**
```tsx
"use client";
import type { Sleeve, Universe, Strategy } from "@/lib/types";
import { StrategyPicker } from "./strategy-picker";

const F = { padding: "5px 8px", borderRadius: 7, background: "rgba(255,255,255,.06)",
  border: "1px solid rgba(255,255,255,.12)", color: "#e7edf6", fontSize: 12 } as const;

export function SleeveRow({ sleeve, onChange, onRemove, universes }:
  { sleeve: Sleeve; onChange: (s: Sleeve) => void; onRemove: () => void;
    universes: Universe[] | undefined; strategies?: Strategy[] }) {
  const set = (patch: Partial<Sleeve>) => onChange({ ...sleeve, ...patch });
  return (
    <div className="glass" style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", padding: 10 }}>
      <input value={sleeve.label} placeholder="label" onChange={(e) => set({ label: e.target.value })} style={{ ...F, width: 110 }} />
      <StrategyPicker value={sleeve.strategy_id} onChange={(id) => set({ strategy_id: id })} />
      <select value={sleeve.universe} onChange={(e) => set({ universe: e.target.value })} style={F}>
        <option value="">— universe —</option>
        {universes?.map((u) => <option key={u.value} value={u.value}>{u.value}</option>)}
      </select>
      <select value={sleeve.rebalance_frequency} onChange={(e) => set({ rebalance_frequency: e.target.value })} style={F}>
        {["monthly", "weekly", "quarterly"].map((f) => <option key={f} value={f}>{f}</option>)}
      </select>
      <select value={sleeve.rebalance_type} onChange={(e) => set({ rebalance_type: e.target.value })} style={F}>
        {["equal_weight", "skewed"].map((t) => <option key={t} value={t}>{t}</option>)}
      </select>
      <input type="number" value={sleeve.max_holdings} title="max holdings" onChange={(e) => set({ max_holdings: Number(e.target.value) })} style={{ ...F, width: 60 }} />
      <input type="number" value={sleeve.weight} title="weight %" onChange={(e) => set({ weight: Number(e.target.value) })} style={{ ...F, width: 60 }} />
      <span style={{ color: "var(--color-muted)" }}>%</span>
      <button onClick={onRemove} style={{ ...F, marginLeft: "auto", color: "var(--color-neg)" }}>✕</button>
    </div>
  );
}
```

- [ ] **Step 5: Write `components/form/portfolio-form.tsx`**
```tsx
"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Portfolio, Sleeve } from "@/lib/types";
import { useUniverses, useStrategies, useCreatePortfolio, useUpdatePortfolio } from "@/lib/queries";
import { SleeveRow } from "./sleeve-row";
import { GlassPanel } from "@/components/ui/glass";
import { validatePortfolioForm, sleeveWeightSum } from "@/lib/validation";
import { defaultInception, EARLIEST_INCEPTION } from "@/lib/format";
import { ApiError } from "@/lib/api";

let SEQ = 0;
const newSleeve = (weight: number): Sleeve => ({
  id: `sleeve_${Date.now()}_${SEQ++}`, label: "Sleeve", strategy_id: "", weight, universe: "",
  rebalance_frequency: "monthly", rebalance_date: "first", rebalance_type: "equal_weight",
  max_holdings: 10, momentum_ranking: "20_day_return", include_brokerage: true, exchange: "NSE",
});

const F = { padding: "6px 9px", borderRadius: 8, background: "rgba(255,255,255,.06)",
  border: "1px solid rgba(255,255,255,.12)", color: "#e7edf6", fontSize: 13 } as const;

export function PortfolioForm({ initial, onSaved }: { initial?: Portfolio; onSaved: (id: string) => void }) {
  const router = useRouter();
  const { data: universes } = useUniverses();
  const { data: strategies } = useStrategies();
  const create = useCreatePortfolio();
  const update = useUpdatePortfolio(initial?.id ?? "");

  const [form, setForm] = useState<Portfolio>(initial ?? {
    name: "", description: "", total_capital: 1000000, benchmark_symbol: "Nifty 50",
    inception_date: defaultInception(), point_in_time_membership: true, sleeves: [newSleeve(100)],
  });
  const [errors, setErrors] = useState<string[]>([]);
  const [apiError, setApiError] = useState<string | null>(null);
  const sum = sleeveWeightSum(form.sleeves);

  const setSleeve = (i: number, s: Sleeve) =>
    setForm({ ...form, sleeves: form.sleeves.map((x, j) => (j === i ? s : x)) });

  async function save() {
    const errs = validatePortfolioForm(form);
    setErrors(errs);
    if (errs.length) return;
    try {
      const res = initial?.id ? await update.mutateAsync(form) : await create.mutateAsync(form);
      onSaved((res as { portfolio_id: string }).portfolio_id ?? initial!.id!);
    } catch (e) {
      setApiError(e instanceof ApiError ? e.detail : "Save failed");
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 900 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700 }}>{initial ? "Edit portfolio" : "New portfolio"}</h1>
      <GlassPanel>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <input value={form.name} placeholder="Portfolio name" onChange={(e) => setForm({ ...form, name: e.target.value })} style={{ ...F, flex: 1, minWidth: 200 }} />
          <input type="number" value={form.total_capital} title="total capital ₹" onChange={(e) => setForm({ ...form, total_capital: Number(e.target.value) })} style={{ ...F, width: 140 }} />
          <input type="date" value={form.inception_date} min={EARLIEST_INCEPTION} onChange={(e) => setForm({ ...form, inception_date: e.target.value })} style={F} />
          <label style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--color-muted)", fontSize: 12 }}>
            <input type="checkbox" checked={form.point_in_time_membership} onChange={(e) => setForm({ ...form, point_in_time_membership: e.target.checked })} /> PIT
          </label>
        </div>
      </GlassPanel>

      {form.sleeves.map((s, i) => (
        <SleeveRow key={s.id} sleeve={s} universes={universes} strategies={strategies}
          onChange={(ns) => setSleeve(i, ns)}
          onRemove={() => setForm({ ...form, sleeves: form.sleeves.filter((_, j) => j !== i) })} />
      ))}

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <button onClick={() => setForm({ ...form, sleeves: [...form.sleeves, newSleeve(0)] })} style={F}>+ Add sleeve</button>
        <span className="num" style={{ color: Math.abs(sum - 100) > 0.5 ? "var(--color-neg)" : "var(--color-pos)" }}>
          weights = {sum}%
        </span>
        <span style={{ flex: 1 }} />
        <button onClick={save} disabled={create.isPending || update.isPending}
          style={{ ...F, color: "#dce6ff", background: "rgba(124,156,255,.20)", border: "1px solid rgba(124,156,255,.34)" }}>
          {create.isPending || update.isPending ? "Saving…" : "Save"}
        </button>
      </div>

      {errors.length > 0 && (
        <GlassPanel><ul style={{ color: "var(--color-neg)", margin: 0, paddingLeft: 18 }}>
          {errors.map((e) => <li key={e}>{e}</li>)}
        </ul></GlassPanel>
      )}
      {apiError && <GlassPanel><span style={{ color: "var(--color-neg)" }}>{apiError}</span></GlassPanel>}
    </div>
  );
}
```

- [ ] **Step 6: Write the two pages**

`app/portfolio/new/page.tsx`:
```tsx
"use client";
import { useRouter } from "next/navigation";
import { PortfolioForm } from "@/components/form/portfolio-form";

export default function NewPortfolio() {
  const router = useRouter();
  return <PortfolioForm onSaved={(id) => router.push(`/portfolio/${id}`)} />;
}
```
`app/portfolio/[id]/edit/page.tsx`:
```tsx
"use client";
import { use } from "react";
import { useRouter } from "next/navigation";
import { usePortfolio } from "@/lib/queries";
import { PortfolioForm } from "@/components/form/portfolio-form";
import { GlassPanel } from "@/components/ui/glass";

export default function EditPortfolio({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { data: portfolio } = usePortfolio(id);
  if (!portfolio) return <GlassPanel><span style={{ color: "var(--color-muted)" }}>Loading…</span></GlassPanel>;
  return <PortfolioForm initial={portfolio} onSaved={(pid) => router.push(`/portfolio/${pid}`)} />;
}
```

- [ ] **Step 7: Run test to verify it passes**

Run: `npm run test -- --run test/portfolio-form.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 8: Commit**
```bash
git add frontend/components/form frontend/app/portfolio/new frontend/app/portfolio/[id]/edit frontend/test/portfolio-form.test.tsx
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(frontend): portfolio builder/editor with sleeves + strategy picker"
```

---

### Task 9: Nav shell + portfolio switcher + full gate

**Files:**
- Modify: `frontend/components/nav.tsx` (replace stub)
- Test: none new (covered by build + existing suite); this task is the integration gate.

**Interfaces:**
- Consumes: `usePortfolios`. Produces: `<Nav>` — left glass rail with `tvagent` wordmark, a "Portfolios" link (active), stubbed "Research"/"Backtest" (disabled, future), and a portfolio quick-switch list (links to each `/portfolio/{id}`).

- [ ] **Step 1: Replace `components/nav.tsx`**
```tsx
"use client";
import Link from "next/link";
import { usePortfolios } from "@/lib/queries";

export function Nav() {
  const { data: portfolios } = usePortfolios();
  const item = (label: string, href?: string, active = false) => href ? (
    <Link href={href} style={{ textDecoration: "none", color: active ? "#dce6ff" : "#9fb0d4",
      fontWeight: active ? 700 : 400, padding: "5px 8px", borderRadius: 6, display: "block",
      background: active ? "rgba(124,156,255,.18)" : "transparent" }}>{label}</Link>
  ) : (
    <span style={{ color: "#5a6b7b", padding: "5px 8px", display: "block" }}>{label}</span>
  );
  return (
    <nav className="glass" style={{ width: 180, margin: 12, padding: 12, height: "fit-content",
      display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ fontWeight: 700, fontSize: 16, padding: "4px 8px 10px" }}>tvagent</div>
      {item("Portfolios", "/", true)}
      {item("Research")}
      {item("Backtest")}
      <div style={{ fontSize: 9, letterSpacing: ".08em", textTransform: "uppercase",
        color: "var(--color-muted)", margin: "12px 8px 4px" }}>Your books</div>
      {portfolios?.map((p) => item(p.name, `/portfolio/${p.id}`))}
    </nav>
  );
}
```

- [ ] **Step 2: Run the full test suite**

Run (from `frontend/`): `npm run test -- --run`
Expected: PASS — all tests across the suite (api, format, validation, glass, portfolio-card, perf-curve, detail-states, portfolio-form).

- [ ] **Step 3: Production build gate**

Run: `npm run build`
Expected: build SUCCEEDS with no type errors. Fix any TS/lint issues surfaced (e.g. unused `EARLIEST_INCEPTION` import in the detail page — remove if flagged).

- [ ] **Step 4: Manual smoke (live backend on :3001)**

Start backend (`market-hunt/`): `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe api_server.py`
Start frontend (`frontend/`): `npm run dev` → open `http://localhost:3000`.
Verify: home lists portfolios (or empty state) → create a portfolio with 2 sleeves (weights 60/40) → save → detail screen runs and shows the ₹ curve vs Nifty 50, KPIs, holdings, sleeve attribution, rebalance, allocation, with the `HYPOTHETICAL · PIT` badge and as-of date. Confirm a PIT-unavailable universe surfaces the backend's 400 detail (not a silent result).

- [ ] **Step 5: Commit**
```bash
git add frontend/components/nav.tsx
git -c commit.gpgsign=false commit --author="Arpitha B <>" -m "feat(frontend): nav shell + portfolio switcher; full build/test gate"
```

---

## Self-Review

**Spec coverage (design §5):**
- §5.1 tech stack (Next 15 / React 19 / Tailwind 4 / TanStack Query / Recharts) → Task 1–2,4,6 ✓ (TanStack Table dropped in favor of a plain sortable table — documented in Task 7; simpler, fewer deps, same UX)
- §5.2 screen 1 Portfolios home (cards, empty) → Task 5 ✓
- §5.2 screen 2 Live detail (command bar, KPIs, ₹ curve, holdings, attribution, rebalance, allocation) → Tasks 6–7 ✓
- §5.2 screen 3 builder/editor (strategy pick + inline create, universe, weight-sum) → Task 8 ✓
- §5.3 states (empty/loading/error/partial coverage) → Task 5 (empty/loading), Task 6 (loading/error incl. PIT-400), Task 7 (coverage chip) ✓
- §5.4 glass design tokens (.glass, palette, tabular-nums, micro-labels) → Task 1 (tokens) + primitives Task 4 ✓
- §5.5 tests (client typing, builder validation, state renders) → Tasks 2,3,5,6,7,8 ✓
- Honesty (HYPOTHETICAL·PIT badge, as-of, coverage, loud 400) → Task 6 badge + error, Task 7 coverage ✓
- ₹ curve from capital, Nifty same-₹, inception default/floor → Task 6 curve, Task 3 `defaultInception`/`EARLIEST_INCEPTION`, Task 8 date min ✓

**Documented deviations / deferrals:** TanStack Table → plain sortable table (Task 7). Range control (1Y/Since-2022/Custom) is wired for display in v1; the run always ends *today* using the stored `inception_date` (Task 6 note) — changing the window is done via Edit (inception_date). `rebalance_actions.next_due`/`target_weight` may be null (per backend Plan 1).

**Placeholder scan:** No "TBD/TODO". Every component/test step shows complete code.

**Type consistency:** `RunResult`/`Holding`/`SleeveAttribution`/`CurvePoint`/`RunSummary` (Task 2) are consumed unchanged by KpiStrip, PerfCurve, HoldingsTable, SleeveAttributionPanel, RebalancePanel, AllocationBar (Tasks 6–7). `Portfolio`/`Sleeve` (Task 2) flow through queries (Task 4), PortfolioForm/SleeveRow (Task 8), PortfolioCard (Task 5). Hook names (`usePortfolios`, `usePortfolio`, `useRunPortfolio`, `useStrategies`, `useUniverses`, `useCreatePortfolio`, `useUpdatePortfolio`, `useCreateStrategy`) defined in Task 4 match every call site and the Task 8 test mock. `api.*` method names (Task 2) match `queries.ts` (Task 4) and `api.test.ts` (Task 2). `ApiError` thrown in Task 2 is caught in Task 6 (detail) and Task 8 (form). Glass primitives (`GlassPanel`,`PanelTitle`,`Badge`,`Stat`) defined Task 4, used Tasks 5–8.

## Execution prerequisites
- Tasks 1–8 unit/render tests need **no backend** (fetch + queries mocked).
- Task 9 Step 4 manual smoke needs the **Plan 1 backend live on :3001** + Mongo + loaded NIFTY data + ≥1 saved strategy. Recommend executing **Plan 1 before Plan 2 Task 9**.

## Done
A user can create multi-sleeve portfolios, run them live, and read ₹ performance vs Nifty 50 with per-sleeve attribution — in the locked glass terminal.
