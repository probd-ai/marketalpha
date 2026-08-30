# Live Portfolio — Run & Operate Guide

How to load data, start the servers, and use the **Live Portfolio** terminal end-to-end.

---

## 1 · What's running (architecture)

```
┌─ market-hunt/ ─────────────────┐        ┌─ frontend/ (tvagent) ────────┐
│ FastAPI backend  (port 3001)   │  HTTP  │ Next.js 15 glass terminal    │
│ + MongoDB (localhost:27017,    │ ◄────► │ (port 3000)                  │
│   db "market_hunt")            │ /api/* │ Portfolios · Live detail ·   │
│ data load · indicators ·       │        │ builder                      │
│ backtest / portfolio compose   │        │                              │
└────────────────────────────────┘        └──────────────────────────────┘
```

- **Backend = `market-hunt/`** — owns the data (prices, TrueVX indicators, index membership) and all
  compute (strategy simulation, portfolio composition). FastAPI on **`:3001`**, MongoDB on
  `mongodb://localhost:27017/` db **`market_hunt`** (hardcoded in `api_server.py`).
- **Frontend = `frontend/`** — the glass UI. Calls relative `/api/*`, which Next.js **proxies** to the
  backend (`API_PROXY_TARGET`, default `http://localhost:3001`).
- A **portfolio** = N capital-weighted **sleeves**; each sleeve = a saved strategy + execution params
  (universe, rebalance freq/date, holdings cap, ranking). "Live" = run each sleeve **to today** and
  compose — an honest **as-of-last-session, point-in-time** simulation (labeled `HYPOTHETICAL · PIT`).

---

## 2 · Prerequisites (one-time)

1. **MongoDB** running locally with the `market_hunt` data already loaded (prices, indicators,
   `index_meta`, `index_reconstitution`). Check it's up:
   ```bash
   # any Mongo client, or:
   cd market-hunt && .venv/Scripts/python.exe -c "from pymongo import MongoClient; print(MongoClient('mongodb://localhost:27017/',serverSelectionTimeoutMS=2000).admin.command('ping'))"
   ```
2. **Backend Python env** — `market-hunt/.venv` (Python 3.12+). If missing: `python -m venv .venv` then
   `.venv/Scripts/pip install -r requirements.txt` (or the project's deps).
3. **Frontend Node deps** — once: `cd frontend && npm install`.

> Windows note: commands below use the Git-Bash style `.venv/Scripts/python.exe`. In PowerShell use
> `.venv\Scripts\python.exe`. Always prefix backend runs with `PYTHONIOENCODING=utf-8` (the server logs
> emoji; without it Windows cp1252 crashes).

---

## 3 · Loading data (only if (re)building the dataset)

The dataset is **already loaded**. You only need this to refresh or add universes. All commands run from
`market-hunt/` with `.venv/Scripts/python.exe`. Full background: `market-hunt/docs/TVAGENT_MIGRATION_PLAN.md`.

| Step | Command | Writes |
|---|---|---|
| 1. Index constituents | `… IndexManagement.py process-urls` (URLs added via `add-url`; `list-urls` to see) | `index_meta` |
| 2. Symbol→NSE mappings | `… DataLoadManagement.py refresh-mappings` | `symbol_mappings` |
| 3. Prices (one index) | `… DataLoadManagement.py download-index "NIFTY 100" --max-concurrent 5` | `prices_YYYY_YYYY` |
| 3b. Prices (one stock) | `… DataLoadManagement.py download-stock RELIANCE` | `prices_YYYY_YYYY` |
| 4. Reconstitution (PIT) | `… scripts/load_reconstitution.py --index NIFTY100 --csv path/to/nifty100_changelog.csv` | `index_reconstitution` |
| 5. TrueVX indicators | `… scripts/compute_indicators.py --store-from 2018-01-01` (`--symbols A,B` / `--force` / `--limit N`) | `indicators` |
| — Check coverage | `… DataLoadManagement.py show-stats` · `check-gaps-index "NIFTY 100"` | — |

> Any command's flags: append `--help`. Prices come from TradingView (anonymous gives full history;
> delisted symbols return nothing — an accepted residual gap). Point-in-time correctness requires the
> reconstitution step (4); a universe without it will **fail loud** at run time (not silently bias).

---

## 4 · Start the servers

**Terminal A — backend** (from `market-hunt/`):
```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe api_server.py
# → Uvicorn running on http://0.0.0.0:3001
```

**Terminal B — frontend** (from `frontend/`). Use a **production** server (stable; avoids dev pitfalls):
```bash
npm run build
npm run start          # → http://localhost:3000  (proxies /api/* to :3001)
```
Then open **http://localhost:3000**.

- The frontend proxies `/api/*` to `API_PROXY_TARGET` (default `http://localhost:3001`). If your backend
  runs elsewhere: `API_PROXY_TARGET=http://localhost:<port> npm run start`.
- **Alternative (direct, bypasses the proxy):** build with `NEXT_PUBLIC_API_BASE=http://localhost:3001`
  so the browser calls the backend directly. Backend CORS already allows `:3000`/`:3300`. Not required —
  the proxy works because long runs use a short-poll job pattern (below).
- **Dev mode** (`npm run dev`) is fine for editing, but **never run `npm run build` while `npm run dev`
  is live** — they share `.next` and corrupt each other. For just *using* the app, prefer build + start.

---

## 5 · Use the Live Portfolio

1. **http://localhost:3000** → **Portfolios** home → **+ New portfolio**.
2. **Portfolio fields:** name · total capital (default ₹10,00,000) · inception date (default today−1y,
   floor **2022-01-01**) · PIT toggle (default **on**).
3. **Add sleeves** (one or more). Per sleeve:
   - **Strategy** — pick a saved one, or **＋ new** to define a rule inline (metric / operator / threshold,
     e.g. `truevx_score > 90`).
   - **Universe** (e.g. `NIFTY100`, `NIFTY500`, `NIFTYMIDCAP150`) · **rebalance frequency**
     (monthly/weekly/quarterly) · **rebalance date** (First / Mid / Last of month) · **max holdings** ·
     **weight %**.
   - **Weights across sleeves must sum to 100** (live indicator turns red otherwise).
4. **Save** → lands on the dense detail screen and **runs**:
   - **Command bar:** portfolio switcher · `HYPOTHETICAL · PIT` badge · date range (**1Y / Since 2022 /
     Custom**) · as-of date · **Run live** · **Edit**.
   - **₹ curve** of the portfolio vs **Nifty 50** (both start at the capital invested) · KPI strip
     (value, return, alpha, Sharpe, max DD) · **holdings** (sleeve-tagged) · **sleeve attribution**
     (+ PIT coverage) · **rebalance** (add/exit) · **capital allocation** bar.
5. **Date range** drives the run window: **1Y** = trailing year, **Since 2022** = full history, **Custom**
   = pick a start (≥2022). Changing it re-runs.

⏱ **Runs take time** (it's a real point-in-time backtest): ~10–15s for a 1-year single sleeve, ~25–30s
for a 2-sleeve Since-2022 run. The UI shows "Running… up to a minute," then renders. (Internally the run
is a background job the page polls, so it never times out.)

⚠ **What the numbers mean:** performance is a **hypothetical, point-in-time simulation** of what the
strategies *would* have done — there are no real fills. PIT is on by default (survivorship-bias-free);
coverage gaps (ever-members with no price data) are shown, not hidden.

---

## 6 · API reference (backend `:3001`)

Portfolio endpoints (added for this feature):

| Method · Path | Purpose |
|---|---|
| `POST /api/portfolio` | create (validates: ≥1 sleeve, weights=100, each weight 0–100, strategy exists, inception ≥2022) |
| `GET /api/portfolio` | list all |
| `GET /api/portfolio/{id}` | fetch one |
| `PUT /api/portfolio/{id}` · `DELETE /api/portfolio/{id}` | update / delete |
| `POST /api/portfolio/{id}/run?start_date=&end_date=` | **start** a run → `{job_id, status:"running"}` (returns instantly) |
| `GET /api/portfolio/run-status/{job_id}` | poll → `running` / `completed`(+`result`) / `failed`(+`error`) |

Reused engine endpoints: `GET /api/simulation/strategies` (+ POST/PUT/DELETE) · `POST /api/simulation/run`
· `GET /api/data/universes` · `POST /api/portfolio/{id}/run` composes sleeves via the existing
`run_strategy_simulation`. Health: `GET /health`.

---

## 7 · Tests

```bash
# Backend — pure unit (no Mongo/server needed):
cd market-hunt && .venv/Scripts/python.exe -m pytest test_portfolio_composition.py test_momentum_fast.py -q

# Backend — integration (needs a live server; point it at the running backend):
MH_API_BASE=http://localhost:3001 .venv/Scripts/python.exe -m pytest test_portfolio_api.py -m integration -q

# Frontend:
cd frontend && npm run test -- --run
```

---

## 8 · Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Backend crashes on start with a cp1252/encode error | Missing `PYTHONIOENCODING=utf-8` — the logger prints emoji. Always prefix it. |
| `Point-in-time membership unavailable for X` (HTTP 400) | That universe has no reconstitution data — load it (§3 step 4) or turn PIT off (biased). This is intentional fail-loud. |
| Frontend page 500s / `socket hang up` / a stale port | A leftover dev server is bound (often on IPv6 `[::]:3000`). Kill it: `netstat -ano | grep :3000` → `taskkill //PID <pid> //F`, then rebuild+start. |
| Run errors at ~30s | You're routing a long run through a *stale* proxy build. Use a current `npm run build` + `npm run start`; long runs are handled by the job/poll pattern. |
| Numbers look biased / too good | Confirm the `HYPOTHETICAL · PIT` badge and that PIT is ON; check sleeve PIT coverage. |
| Edited frontend code not reflected | Production server serves the build — rerun `npm run build && npm run start`. |

---

## 9 · Quick start (data already loaded)

```bash
# Terminal A
cd market-hunt && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe api_server.py

# Terminal B
cd frontend && npm run build && npm run start

# Browser → http://localhost:3000 → + New portfolio → add sleeves → Save → watch it run.
```
