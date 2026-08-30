---
name: capture_index_rebalance
description: >
  Capture an NSE index reconstitution (semi-annual "rebalance") event from the
  authoritative niftyindices.com source, verify it, map company names to NSE
  tvdatafeed symbols, and upsert it into market_hunt.index_reconstitution. Also
  ensures price history exists for newly-added members. Call this whenever a new
  NIFTY rebalance is announced or becomes effective, or to backfill a historical
  reconstitution event. Keeps the point-in-time membership store correct so
  backtests stay survivorship-bias-free.
---

# capture_index_rebalance(index_name, effective_date="latest")

A self-contained procedure an LLM (or a person) can execute end-to-end. Treat it
as a callable function: given an index and a reconstitution date, it lands a
verified, queryable rebalance event in Mongo and guarantees the added stocks have
price data. It performs **no fabrication** — if a fact cannot be verified against
a real niftyindices source, it stops and reports rather than guessing.

## Signature

```
capture_index_rebalance(
    index_name: str,              # e.g. "NIFTY50", "NIFTY100", "NIFTYMIDCAP150", "NIFTYSMALLCAP250"
    effective_date: str = "latest" # "YYYY-MM-DD" of the reconstitution, or "latest"
) -> { stored: bool, added: [symbols], removed: [symbols], source_url: str, price_loaded: [symbols] }
```

## When to call

- A new NSE reconstitution is announced (niftyindices publishes the press release
  ~1 month before the effective date) or becomes effective (end of Mar / end of Sep).
- Backfilling a historical rebalance to extend point-in-time membership coverage.
- Standing up a new band (Midcap150, Smallcap250, NIFTY500) — call once per
  historical review in the window you need.

## Preconditions

- MongoDB up: `docker start mh-mongo` (`mongodb://localhost:27017/`, db `market_hunt`).
- Repo venv available: `market-hunt/.venv/Scripts/python.exe` (has `pymongo`, tvdatafeed).
- Web access to `niftyindices.com` for verification (the data source of truth).

## Where the data lives

Collection: **`market_hunt.index_reconstitution`** — one document per
`(index_name, effective_date)`. Schema:

```json
{
  "_id": "NIFTY50_2021-03-31",
  "index_name": "NIFTY50",
  "effective_date": ISODate("2021-03-31"),
  "added":   ["TATACONSUM"],
  "removed": ["GAIL"],
  "source_url": "https://www.niftyindices.com/Press_Release/ind_prs23022021.pdf",
  "source": "niftyindices_press_release",
  "verified": true,
  "loaded_at": ISODate(...)
}
```

Unique index on `(index_name, effective_date)`. Symbols are NSE tvdatafeed tickers,
in their **current canonical form** (see Step 3).

## Procedure

### Step 1 — Locate the authoritative source

NSE indices reconstitute semi-annually; changes take effect at the **end of March**
and **end of September** (plus occasional special changes for mergers/delistings).
niftyindices publishes a press-release PDF, named by announcement date:

```
https://www.niftyindices.com/Press_Release/ind_prs<DDMMYYYY>.pdf
```

For `effective_date="latest"`, find the most recent review's PR (announced ~1 month
prior). Fetch the PDF (WebFetch). If the direct PDF is unreachable, use WebSearch
for the niftyindices announcement of that review. One PR PDF typically lists the
add/remove sections for **multiple** indices — read the section for `index_name`.

### Step 2 — Extract the add/remove lists

From the PR, read, verbatim, the companies **added to** and **removed from**
`index_name` for that review. Do not infer from price moves or secondary sources
unless a reputable source (NSE / major financial press) specifically documents that
exact change.

> NO FABRICATION: if you cannot confirm the change from a real source, STOP and
> report the unverifiable review. A smaller fully-verified history beats an invented one.

Verify the extraction, don't trust it. Each table is **Sr. No. · Company Name ·
Symbol**, so the parsed Sr. numbers must come out contiguous `1..N` per list, and the
excluded/included counts must match the PDF. Notes and footnotes sit between tables —
make sure none of them were absorbed as rows.

**Capture on announcement, not on the effective date.** The PR lands ~1 month early, so
the event you store is routinely future-dated relative to the current constituent list.
That is correct and safe — `refresh_all.check_recon_consistency()` compares only
already-effective events, and `membership.py` replays backward from today's members, so
a future event's `added` are ignored and its `removed` correctly bound the exit date.

### Step 3 — Map names → NSE symbols (current canonical form)

Map each company to its NSE tvdatafeed ticker (the symbol used in
`market_hunt.symbol_mappings` / the price store). Two rules that matter for
point-in-time correctness:

- **Use the CURRENT canonical symbol**, even if the stock traded under a different
  ticker at the time. Examples learned in practice:
  - Zomato → `ETERNAL` (renamed 2025-04)
  - Tata Motors → `TMPV` (2025 demerger; the surviving Nifty constituent)
  - LTIMindtree is stored as `LTM` in this DB (not `LTIM`)
  Using the current symbol lets backward replay reconcile with the current
  constituent list.
- Derive tickers from the NSE scrip list / `symbol_mappings`, never guess. Verify
  the symbol exists in `market_hunt.symbol_mappings`; if missing, see Step 5.

### Step 4 — Store the event (idempotent)

Write a one-row changelog CSV (`effective_date,added,removed,source_url`, `;`-separated
symbols) and load it, or upsert directly. The loader enforces "no VERIFY tokens":

```bash
# from market-hunt repo root
./.venv/Scripts/python.exe scripts/load_reconstitution.py \
    --index NIFTY100 --csv /path/to/<index>_changelog.csv
```

Re-running is safe (upsert on `_id`). To verify:

```bash
docker exec mh-mongo mongosh --quiet --eval \
  'db.getSiblingDB("market_hunt").index_reconstitution.find({index_name:"NIFTY100"}).sort({effective_date:1}).toArray()'
```

### Step 5 — Ensure price data for ADDED members

A point-in-time backtest must be able to trade a stock from the moment it joins.
For each symbol in `added`, confirm prices exist; if not, load them:

```bash
# check
docker exec mh-mongo mongosh --quiet --eval \
  'db.getSiblingDB("market_hunt").getCollectionNames().filter(c=>c.startsWith("prices_")).forEach(c=>{const n=db.getSiblingDB("market_hunt")[c].countDocuments({symbol:"<SYM>"}); if(n)print(c+": "+n)})'

# load if missing (needs a symbol_mappings row; new names get a synthetic one)
./.venv/Scripts/python.exe DataLoadManagement.py download-stock <SYM> --force-refresh
```

If a freshly-added stock has no `symbol_mappings` entry (e.g. a recent IPO/rename
post-dating the scrip-master snapshot), insert a synthetic mapping first
(`match_confidence:"synthetic_tv"`, scrip code in the 9,000,000+ range), then
download. REMOVED members need no action — their data is retained.

### Step 6 — Report

Return: stored (bool), the added/removed symbols, the source_url, and which added
symbols required a price load. Note any review you could not verify.

## Integrity rules (binding)

- Authoritative source only (niftyindices); real `source_url` per event; no `VERIFY`
  placeholders ever committed.
- Current canonical symbols, derived from the scrip list — never guessed.
- Read-only on existing price data; this procedure only ADDS (events, and prices
  for new members).
- Idempotent: safe to re-run; keyed on `(index_name, effective_date)`.

## Consuming the data (why this exists)

`index_reconstitution` is the point-in-time membership spine. Combined with a
current constituent list, replaying these events backward reconstructs the index
membership as of any past date — which is what makes a backtest survivorship-bias
free. (tvagent's `IndexMembershipProvider` consumes the equivalent changelog;
this collection is the canonical, queryable home for that history in market_hunt.)

## Example invocations

- `capture_index_rebalance("NIFTY100", "2025-09-30")` — backfill the Sep-2025 N100 review.
- `capture_index_rebalance("NIFTYMIDCAP150", "latest")` — capture the newest Midcap150 rebalance.
- Bootstrap a band: call once per semi-annual review across the target window, then
  verify the forward-replay from the earliest baseline reproduces the current list.
