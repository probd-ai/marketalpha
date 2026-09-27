# tvagent — Fund Desk

A quantitative equity fund, run as a **model portfolio**. Every weekend the engine rebuilds five strategy sleeves from
point-in-time market data and publishes a **trade ticket**. You execute the ticket by hand at your broker on Monday and
Tuesday, then record the fills. The same engine that back-tested the strategies produces the live tickets.

- **Backend** — `market-hunt/`: FastAPI + MongoDB, port **3001**. It holds the data, the book engine and `/api/fund`.
- **Frontend** — `frontend/`: the Next.js trading desk, **http://localhost:3300**.
- **Full strategy report** — `market-hunt/scripts/research_grid/fund_report/Fund_Report_2026-09-27.pdf`.
- **Design and decisions** — `docs/specs/2026-09-27-fund-trading-desk-design.md`.

> All performance figures are **simulated** (point-in-time back-test, next-open fills, full costs), not realised returns.

---

## 1 · The fund at a glance

| Sleeve | Weight | Capital (₹45 L) | Universe / instrument | Style | Positions |
|---|---|---|---|---|---|
| **Value core** | 28% | ₹12.60 L | Nifty 50 | Grid-10: buy dips, let winners run | 10 |
| **Gold** | 8% | ₹3.60 L | GOLDBEES (ETF) | Crash hedge | 1 |
| **Nasdaq-100** | 4% | ₹1.80 L | MON100 (ETF) | Global / USD diversifier | 1 |
| **Growth** | 35% | ₹15.75 L | Nifty 200 | Weekly momentum tranches | ~18 (10–25) |
| **Satellite** | 25% | ₹11.25 L | Nifty 500 + Microcap 250 | Weekly momentum tranches | ~21 (15–28) |

**Back-test (Oct 2020 – Sep 2026), whole fund:**
- About 31%/yr, against 12.5% for Nifty 50. Worst fall about −19% to −21%. Sharpe about 1.6.
- Plan for less. 2020–26 was a strong period for Indian mid and small caps. A prudent expectation is **15–20%/yr
  before tax**, with falls of 20–25% in ordinary corrections and possibly 40–50% in a 2008-style crash.

## 2 · The rules

**Everything is mechanical.** Signals use the **previous close**; trades happen at or after the **next open**. There is
no discretionary override and no stop-loss.

### Value core — Grid-10 (Nifty 50)
- **Grid state.** Take the TrueVX `mean_long` of each stock (0–100, measured against Nifty 50) and split it into
  5-point bands. The state is the direction of the last band change: **up** or **down**.
- **Buy:** fill up to **10** names with the **lowest 22-day return** among stocks whose grid is **up**.
- **Sell:** a holding whose grid is **down** **and** whose 22-day return is **positive** (sell into the bounce).
  - A down-state stock with a negative month is kept until it bounces.
  - On the Books page, "Grid: down" plus a negative 22-day return means *on watch*.
- **Sizing:** money from sales goes to new names up to ~10%, then tops up laggards. **Winners are never trimmed.**

### Growth (Nifty 200) and Satellite (Nifty 500 + Microcap 250) — weekly tranches
- **Alpha filter:** `truevx_score > 90` and `mean_short > 80` and `mean_mid > 60` and `mean_long > 50`.
- **Every Monday**, the **top 5** Alpha-eligible stocks by 22-day return each get a new **2.5% slice**.
- Each slice is held **8 weekly checks**, then sold. If the stock is in the top 5 again that week, the slice rolls over
  with no trade.
- **Per-stock cap: 10%.** Nothing is bought past 10%; any holding above 10% is **trimmed back every Monday**.
- **Spare cash** (from expiries and trims) tops up holdings in rank order, up to 10% each.
- A stock that falls out of the top 5, or loses Alpha, is **not sold early**. It rides until its slices expire.
- There is **no limit on the number of names**. The count floats; at least 10 are needed to be fully invested.

### Gold and Nasdaq-100
Held as ETFs. They trade only when the fund rebalances between sleeves.

### Fund rebalance (money moving between sleeves)
- Restores the sleeves to **28 / 8 / 4 / 35 / 25** on the first Monday of **January and July**.
- **Safety band:** on any month's first Monday, rebalance early if a sleeve is **more than 5 points** from its target.
- Tested: the same return as monthly, with 2 heavy Mondays a year instead of 12. Never rebalancing deepened the worst
  fall to −24%.
- The months and band are configurable in **Settings**.

### Index changes
A stock that leaves its sleeve's index is sold at the next open.

## 3 · Weekly routine

| When | What you do |
|---|---|
| **Sunday** | Refresh data (§6), then Desk → **Compute this week's run** (≈1 min) → review checks and ticket → **Publish** |
| **Monday** | Execute **Value core, Gold, Nasdaq-100, Growth**. For each sleeve, sells first, then buys. Record the fills on the Ticket page. |
| **Tuesday** | Execute **Satellite**: sells, then buys. Record the fills. The ticket becomes **CLOSED**. |

**What to expect:**

| | Lines on a ticket |
|---|---|
| Normal week | **~20** (Growth ~10, Satellite ~11, Value ~3 — Value has no trades on over half of Mondays) |
| Fund-rebalance week (Jan/Jul, or when the band fires) | **~40–50** |
| First ticket of a model version (initial build) | Buys the whole book (~47 lines) |

- Limit prices are the reference close ±0.3%. On Tuesday, prices have moved since Friday, so use the market; keep the
  ticket quantities.
- If Monday is a market holiday, start on the next trading day. NSE holidays are not in the desk calendar yet.
- Only **one** run can be published per week.

## 4 · Using the desk (http://localhost:3300)

| Page | What it is for |
|---|---|
| **Desk** | This week's status: data freshness, the draft waiting, open tickets. Fund value, returns vs Nifty 50, back-test chart, sleeve weights. |
| **Runs → a run** | Pre-trade checks and the **ticket** — what to sell and buy, by sleeve, sells first, each line with its reason. **Publish** or **Discard**; download CSV. |
| **Ticket** | The ticket for execution: mark each line **done / partial / skipped** with your fill price, then Save. Print or CSV. |
| **Books** | Model holdings per sleeve: weight, value, shares, 22-day return, grid state (Value) or tranches / Alpha (Growth, Satellite). |
| **Tracking** | Your recorded positions vs the model, and the ₹ tracking difference. |
| **Cash** | **Deposits and withdrawals after go-live.** The next published run spreads a deposit across the sleeves at target weights, or sells a withdrawal pro-rata. |
| **Journal** | Append-only audit log: every run, publish, execution, cash flow and settings change. |
| **Settings** | Capital, model start, sleeve weights, fund-rebalance months and band, costs. Material changes start a new **model version**. |

### Pre-trade checks
- `PASS` is fine. `WARN` is information. `BLOCK` prevents publishing.
- Common `WARN`s:
  - **Coverage:** newly listed or demerged stocks have fewer than 222 sessions of history, so TrueVX isn't computed
    yet. They are simply not buyable yet.
  - **Liquidity:** an order is large versus traded value.
- `BLOCK` means stale data (refresh first), a data change under a published week (**Drift** — review, then
  acknowledge), a cap or cash breach, or a missing ETF price.

### Capital, go-live, and adding money
- **Capital** is the money the fund has at go-live. Publishing the first ticket marks go-live. From then on the fund's
  rupee size follows the model's returns.
- **Never change Capital after go-live** — it would start a new model and a new initial build. Add or withdraw money on
  the **Cash** page instead. Flows are accounted in units, so they change the fund's size but never its returns.
- **Model start** (Settings) sets how far back the model replays. An early date (default 2020-10-01) gives a mature
  book; today's date builds the books from cash.

## 5 · Setup (one time)

1. **MongoDB** running on `localhost:27017` with the `market_hunt` database loaded (prices, TrueVX indicators,
   `index_meta`, `index_reconstitution`).
2. **Backend environment** — `market-hunt/.venv` (Python 3.12+):
   `python -m venv .venv && .venv/Scripts/pip install -r requirements.txt`.
3. **Frontend** — `cd frontend && npm install && npm run build`.

### Start the servers (after every reboot)
```bash
# Terminal A — backend (port 3001, localhost only)
cd market-hunt
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe api_server.py

# Terminal B — frontend (port 3300, localhost only)
cd frontend
npm start
```
Open **http://localhost:3300**.

## 6 · Market data

```bash
cd market-hunt
.venv/Scripts/python.exe scripts/refresh_all.py        # constituents, mappings, prices, reconstitution, TrueVX, report
```

- Run it **after the NSE close** (15:30 IST); the weekly routine does it on Sunday.
- The `urls` stage needs the backend running. `--skip urls` runs without it.
- Read the final report. A non-zero exit means stale data.
- The desk's freshness check will also block publishing if data is behind.

Building the dataset from scratch (all from `market-hunt/`, every command supports `--help`):

| Step | Command | Writes |
|---|---|---|
| Index constituents | `IndexManagement.py process-urls` | `index_meta` |
| Symbol → NSE mappings | `DataLoadManagement.py refresh-mappings` | `symbol_mappings` |
| Prices | `DataLoadManagement.py download-index "NIFTY 100"` | `prices_YYYY_YYYY` |
| Reconstitution (point in time) | `scripts/load_reconstitution.py --index NIFTY100 --csv <changelog.csv>` | `index_reconstitution` |
| TrueVX indicators | `scripts/compute_indicators.py --store-from 2018-01-01` | `indicators` |

## 7 · Repository layout

```
frontend/                      trading desk (Next.js 15.5, React 19, Tailwind 4, TanStack Query, Recharts)
market-hunt/
  fund/                        fund desk backend
    engine/                    pure book engine — Grid-10, tranche, asset, fund blend, replay, projection
    data.py  store.py  service.py  ticket.py  checks.py  flows.py  config.py
  app/routers/fund.py          /api/fund endpoints
  tests/fund_desk/             unit, golden-master, service, security tests
  scripts/research_grid/       research harness behind every fund decision + fund report builder
  scripts/refresh_all.py       data refresh pipeline
  engine/stateless.py          the original simulator (market-hunt analytics UI)
docs/specs, docs/plans         design, decisions, build plan
```

## 8 · Tests

```bash
cd market-hunt
.venv/Scripts/python.exe -m pytest -q                              # unit suite (no database needed)
.venv/Scripts/python.exe -m pytest -m integration tests/fund_desk  # golden masters + lifecycle (needs MongoDB; ~6 min)

cd frontend
npm test                                                           # vitest
```

- The golden-master tests replay the engine and the frozen research harness side by side on identical data, and
  require identical results to 1e-9.
- A cross-hash-seed test proves the engine is deterministic.

## 9 · Security

- The fund API answers **localhost only**. The alternative is setting `MH_API_TOKEN`, then sending it as `X-API-Key`.
- State changes require the desk's `X-Fund-Desk` header and an allowed origin (a CSRF guard).
- The desk binds to `127.0.0.1` and proxies **only** `/api/fund/*`.
- Broker credentials live only in Windows Credential Manager (`scripts/credentials.py`), never in files.
- Security review findings and fixes are in the spec, §13.

## 10 · Troubleshooting

| Symptom | Fix |
|---|---|
| Backend crashes with a cp1252 / encode error | Start it with `PYTHONIOENCODING=utf-8`. |
| "Data freshness" BLOCK | Run `scripts/refresh_all.py`, then compute again. |
| "A run is already published for this week" | Only one ticket per week; wait for next Sunday. |
| "Cash flows changed after this run was computed" | Compute a new run so the ticket includes the flow. |
| Page shows old code after an edit | `npm run build`, then restart `npm start`. Never run `build` while `npm run dev` is live. |
| Port already in use | `netstat -ano \| grep :3300` → `taskkill //PID <pid> //F`. |
