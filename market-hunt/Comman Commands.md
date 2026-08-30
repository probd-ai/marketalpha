# Market Hunt — Setup & Execution Guide

This is the operational runbook. **Step 1 (load index/ETF URLs)** is fully scripted via
`scripts/url_loader.py`. Later steps (mappings → price download → indicators) use
`DataLoadManagement.py`.

> Run all commands from the repo root `market-hunt/`. On Windows the venv python is
> `.venv\Scripts\python`; on Linux/Mac `.venv/bin/python`. Examples below use `PY=`.

```bash
# pick one for your shell
PY=.venv/Scripts/python          # Windows (Git Bash / PowerShell)
PY=.venv/bin/python              # Linux / Mac
```

---

## 0. Services (must be running)

| Service  | Start                                                                 | URL / Port |
|----------|-----------------------------------------------------------------------|------------|
| MongoDB  | `docker start mh-mongo` (first time: `docker run -d --name mh-mongo -p 27017:27017 -v mh-mongo-data:/data/db mongo:7`) | `:27017`, db `market_hunt` |
| Backend  | `$PY -m uvicorn api_server:app --host 0.0.0.0 --port 3001`            | `:3001`, docs `/docs` |
| Frontend | `cd frontend && npm run dev`                                          | `:3000` |

> Backend deps not in `requirements.txt` (install once): `fastapi uvicorn[standard] motor aiohttp`.
> The `url_loader.py` script talks to the backend at `MH_API` (default `http://localhost:3001`).

---

## Step 1 — Load Index & ETF URLs  ← `scripts/url_loader.py`

Populates `index_meta_csv_urls` (URL configs) and `index_meta` (constituents).
**Run this for first-time load and again after every NSE rebalance** (≈monthly/quarterly)
to refresh constituents.

### 1a. First-time full load (all Nifty index constituents)
```bash
$PY scripts/url_loader.py discover --process
```
- Reads `ALL_URLS` (index *page* URLs), resolves each → its direct `IndexConstituent/*.csv`,
  saves it as a URL config (idempotent — re-running skips existing), then downloads all.
- Pages with no CSV link (ESG / Shariah / futures / a few strategy variants) are reported and skipped.

### 1b. Local seed CSVs (benchmarks + ETFs)
These live under the repo root; the script auto-serves the root over `:8000` while loading.
```bash
$PY scripts/url_loader.py load-local market_indexes.csv --index-name "MARKET INDEXES"
$PY scripts/url_loader.py build-etf --load          # rebuilds etfs.csv from NSE, then loads as ETF
# or, if etfs.csv already exists:
$PY scripts/url_loader.py load-local etfs.csv --index-name ETF
```

### 1c. Add a single new URL
```bash
# direct constituent CSV (index name auto-extracted from filename):
$PY scripts/url_loader.py add "https://niftyindices.com/IndexConstituent/ind_niftyXXXlist.csv"

# a niftyindices *page* — auto-resolve the CSV link inside it:
$PY scripts/url_loader.py add "https://niftyindices.com/indices/equity/.../some-index" --resolve

# with explicit name / tags:
$PY scripts/url_loader.py add "<url>" --index-name "MY INDEX" --tags custom,test
```

### 1d. Rebalance refresh (re-pull latest constituents for saved URLs)
```bash
$PY scripts/url_loader.py reload --all                 # every saved config
$PY scripts/url_loader.py reload --active-only          # only active ones
$PY scripts/url_loader.py reload --urls "https://niftyindices.com/IndexConstituent/ind_nifty50list.csv"
```
Re-download **replaces** that index's rows in `index_meta` (per-index clear+insert), so it is safe to repeat.
`reload` auto-starts the `:8000` server so local seeds (market_indexes / etfs) refresh too.

### 1e. Inspect
```bash
$PY scripts/url_loader.py list                          # all saved configs
```

### Underlying API (what the script calls)
```
POST /api/urls     {url, index_name, description, tags, is_active}   # save (auto-extracts name)
POST /api/process  {url_ids:[...]}                                   # download CSV(s) -> index_meta
GET  /api/urls                                                       # list configs
GET  /api/data/universes                                             # loaded universes + counts
```

> ⚠ Known prototype debt: the backend's auto index-name extractor is crude and collapses some
> distinct URLs to the same name (e.g. several `NIFTY` / `NIFTY ALPHA`). Constituent data is
> correct; only the label is off. Fix = a real slug→name map (deferred to the production rebuild).

---

## Step 2 — Symbol mappings (map constituents → NSE scrip codes)

**Mandatory after refreshing URLs.** Reads symbols from `index_meta`, maps to NSE scrip codes
into `symbol_mappings`.
```bash
$PY DataLoadManagement.py refresh-mappings
$PY DataLoadManagement.py list-indices        # indices available for download
```

## Step 3 — Download historical price data

> **Source = TradingView (tvdatafeed), not NSE.** NSE's charting API (`charting.nseindia.com/Charts/*`)
> is dead (404). OHLC now comes from `tv_price_client.TvPriceClient` (rongardF tvdatafeed fork),
> wired into `StockDataManager`. Equities/ETFs use `<SYMBOL>` on exchange `NSE`; index benchmarks
> map via `INDEX_TV_SYMBOLS` in `tv_price_client.py`. Anonymous works but is rate-limited — for
> full history across many symbols set `TV_USERNAME` / `TV_PASSWORD` env vars first.
> On Windows prefix commands with `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` (the CLI prints emoji).

```bash
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 $PY DataLoadManagement.py download-index "MARKET INDEXES" --force-refresh
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 $PY DataLoadManagement.py download-index "NIFTY 500" --force-refresh --max-concurrent 3
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 $PY DataLoadManagement.py download-stock HDFCBANK --force-refresh
# options: --start-date YYYY-MM-DD (default 2005-01-01) | --end-date | --force-refresh | --max-concurrent N
# delete:  $PY DataLoadManagement.py delete-stock 'NIFTY50 EQL Wgt' --confirm
```
Prices land in 5-year partitioned collections `prices_2005_2009 / 2010_2014 / 2015_2019 / 2020_2024 / 2025_2029`.

## Step 4 — Indicator calculations (TrueVX score)

Run the indicator engine to compute scores used by the strategy simulator.

---

## Screener (legacy Streamlit)
```bash
streamlit run frontend/pages/1_Stock_Screener.py
```

---

## Strategy research notes (user)

```
############### 2020-2025 ###############

######################## Nifty 50 ################################
#### Weekly .5 out of N50 Skewed
184 |  264
#### Monthy .5 out of N50 Skewed
268 |  288

###### Buy TOP 3 222D-PROC-MONTHLY###
261|280
###### Buy TOP 5 222D-PROC-MONTHLY###
283|303
###### Buy TOP 7 222D-PROC-MONTHLY###
267|286
###### Buy TOP 10 222D-PROC-MONTHLY###
247|265

######################## Nifty 100 ######################################
#### Weekly .5 out of N100 Skewed
506 |  642
#### Monthy .5 out of N100 Skewed
702 |  741
#### Monthy .10 out of N100 Skewed
716 |

###### Buy TOP 10 222D-PROC-MONTHLY--->66DaysROC###
777 | 813------>722|760
###### Buy TOP 7 222D-PROC-MONTHLY###
1000|1050
###### Buy TOP 5 222D-PROC-MONTHLY###
900|950
###### Buy TOP 3 222D-PROC-MONTHLY###
700|750
###### Buy TOP 2 222D-PROC-MONTHLY###
840|885
###### Buy TOP 1 222D-PROC-MONTHLY###
1950|2000

######################## Nifty 101-250 #############################
#### Weekly .10 out of N150 Skewed
282 |  362
#### Monthy .10 out of N150 Skewed
945 |  982
###### Buy TOP 10 222D-PROC-MONTHLY###
983|1023
###### Buy TOP 7 222D-PROC-MONTHLY###
776|812
###### Buy TOP 5 222D-PROC-MONTHLY###
1088|1163
###### Buy TOP 3 222D-PROC-MONTHLY###
1072|1120

######################## Nifty 251-500 ##########################
#### Monthly .15 out of N250 Skewd
881 | 919
###### Buy TOP 15 222D-PROC-MONTHLY###
1006|1061
###### Buy TOP 20 222D-PROC-MONTHLY###
900|930

######################### Nifty 500 #######
#### Monthly .30 out of N500 Skewd
992 | 1032

############# Sweet-Spot Holding ######
Nifty top 50 --> 4-6    peaked at 5-6
Nifty top 100--> 4-8    peaked at 4-5
Nifty top 200-->10-12   peaked at 8-9
Nifty 100-250-->10-12   peaked at 10
Nifty 250-500-->8-12    peaked at
Nifty total 500-20-27   peaked at
```
