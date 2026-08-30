---
name: market-refresh
description: Refresh all market-hunt data end-to-end — index/ETF constituents (URL reload), symbol mappings, OHLCV prices via tvdatafeed (incremental, with full re-download for stocks that had a split/bonus so unadjusted history never fabricates a crash), reconstitution (rebalance) freshness for point-in-time correctness, TrueVX indicators, and a verified freshness report. Use when the user says data is old/stale, asks to refresh/update market data, prices, indicators, or rebalance/reconstitution data, or before running simulations that need current data.
---

# market-refresh — one-command market-hunt data refresh

Everything is wrapped in a single pipeline: `market-hunt/scripts/refresh_all.py`.
Run it, read its verdict, handle the two things it cannot do itself (start services,
capture new rebalance press releases). Verified end-to-end 2026-07-03.

## Preconditions (check before running)

1. **MongoDB** on `:27017` (`docker start mh-mongo` if down).
2. **Backend** on `:3001` — required only by the `urls` stage:
   `cd market-hunt && .venv/Scripts/python.exe -m uvicorn api_server:app --port 3001`
   (run in background). If you skip `urls`, the backend isn't needed.
3. Run **after NSE close** (15:30 IST). Mid-session runs store a partial bar for today.
4. Everything runs **from the `market-hunt/` repo root** with the repo venv
   (`.venv/Scripts/python.exe`). The venv python does NOT resolve from other CWDs —
   always `cd` first.

## Run

```bash
cd market-hunt
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/refresh_all.py
```

Stages in order: `urls → mappings → prices → recon → indicators → report`.
Useful variants:

```bash
--skip urls                 # constituents unchanged since last run (no rebalance)
--only recon,report         # quick health check, no downloads (~1 min)
--only prices,indicators    # daily top-up without touching constituents
--stale-tolerance 60        # default; delisted/unmappable names count as stale
```

Full run ≈ 45–90 min (indicators recompute is ~30 min — the engine has no incremental
mode; `--force` is intentional). Exit 0 = all stages OK. A JSON audit trail is written
to `scripts/refresh_all_last_run.json` — read it when anything reports PROBLEM.

## Reading the result

- `report` prints per-symbol freshness histograms for prices and indicators. Expected
  latest = most recent NSE trading day. ~50-60 permanently-dead symbols (delisted,
  merged, or too new for NSE masters) always show stale — that's what the tolerance
  absorbs. Verify freshness **per-symbol** (`$max date` grouped by symbol), never by
  collection max — one fresh symbol hides a thousand stale ones.
- **`prices` handles corporate actions before it downloads anything** (added 2026-08-30).
  A split/bonus makes tvdatafeed re-issue the *whole* adjusted history, so an
  incremental top-up would splice adjusted bars onto unadjusted ones and manufacture a
  crash. The stage now calls `fetch_corporate_actions()` (NSE, 120-day window), splits
  the universe, and runs **full re-download (`force_refresh=True`) for affected symbols**
  and incremental for the rest — then **recomputes indicators for whatever it
  re-downloaded**, because the phantom bar otherwise stays baked into `truevx` for up to
  222 sessions (the `mean_long` window). That indicator step is part of the stage's
  `ok`: if prices are fixed but the recompute fails, the stage fails.
  - Summary JSON gains `corporate_actions`, `force_refreshed`, `reindexed`, and
    `unexplained_price_jumps`.
  - `unexplained_price_jumps` is **report-only, and must not be auto-repaired.** Real
    crashes and bonuses overlap in magnitude — IEX's 3:2 bonus and a genuine India VIX
    move are both ratio 1.42, INDUSINDBK's real −27% is 1.37 — so no threshold separates
    them. Auto-refreshing on it would re-download INDUSINDBK forever. Review by hand: a
    genuine crash legitimately stays; an unadjusted split does not.
  - Historical backlog (actions older than the feed window) is **not** healed by this.
    Use `scripts/repair_corporate_actions.py` — scan is read-only, `--apply` repairs:
    ```bash
    .venv/Scripts/python.exe scripts/repair_corporate_actions.py               # scan
    .venv/Scripts/python.exe scripts/repair_corporate_actions.py --apply       # fix
    ```
    Run 2026-08-30 over 2018→: 55 suspects, 52 re-downloaded + reindexed. Splits
    (KIRLPNU 1:2, TDPOWERSYS 1:2, TRIVENI 3:2, ZFCVINDIA, JLHL, IEX, ZEEL, 1:10 ETFs)
    are now continuous; the ~45 that still flag are genuine crashes (YESBANK, RPOWER,
    RELINFRA, IDEA, INDUSINDBK, RECLTD, the March-2020 COVID cohort) and correctly
    remain. Still unadjusted: `AXISGOLD`, `AXISNIFTY`, `GROWWSLVR` — tvdatafeed would
    not serve them; ETFs only, no strategy impact.
  - ⚠ **Freshness is not correctness.** A split leaves the symbol perfectly current, so
    the `report` stage goes green while the series is wrong. Only the corporate-action
    check catches this class of error.
- `prices` does one retry pass automatically; tvdatafeed transient failures (~5-10%
  per pass, even for liquid names) almost all recover. Symbols still failing after
  retry are split in the summary JSON into two lists — the distinction matters:
  - `source_gaps` — the source genuinely has nothing (delisted, merged, zero-price
    demerger dummies, or tvdatafeed doesn't carry it). Expected; tolerated. As of
    2026-08-01 this is 25 symbols: 9 Axis ETFs, `DUMMY*` dummies, GSPL/GUJGASLTD,
    and the 12 benchmark indices with no `INDEX_TV_SYMBOLS` mapping.
  - `blocked_by_mapping` — the symbol never reached the price source. **This fails
    the stage on purpose and always means a pipeline bug, never market attrition.**
    Must be empty. If it isn't, something is gating downloads on `nse_scrip_code`
    again (see below).
- Tell a silent freeze from normal attrition by the *shape* of the staleness: a cohort
  whose prices all stop on the **date of the previous refresh**, and which no amount of
  retry pacing recovers, is blocked upstream of the network — not throttled.
- **The automatic retry pass is not sufficient, and its failures mimic a freeze.** It
  runs back-to-back with the first pass, so whatever throttled pass 1 usually still
  throttles pass 2. On 2026-08-16 seven liquid names (MRF, SAREGAMA, ALPHA, EIEL,
  PUNJABCHEM, PURVA, SHAKTIPUMP) failed both passes and sat frozen at the previous
  refresh date — the exact signature above — yet every one recovered on a single manual
  `download-stock` minutes later. So **always hand-retry the reported stale list before
  concluding anything is blocked**:
  ```bash
  for s in <stale symbols>; do .venv/Scripts/python.exe DataLoadManagement.py download-stock $s; done
  ```
  `Records Inserted: 0` (no error) means the source genuinely has nothing new — a real
  gap. Then **recompute indicators for whatever recovered**, since the indicators stage
  ran against the stale prices:
  `.venv/Scripts/python.exe scripts/compute_indicators.py --start-fetch 2016-01-01 --store-from 2018-01-01 --force --symbols A,B,C`
  and re-run `--only report`. A *spaced* second retry pass in `stage_prices` is the
  real fix and is still open.

## When `recon` flags something (rebalance DB — critical for honest simulations)

`recon` gates on: (a) the latest **already-effective** reconstitution event per tracked
index being consistent with the freshly pulled constituents, and (b) no niftyindices
press release newer than the stored events that is neither captured nor reviewed.

**Capture a review as soon as it is announced, even though it takes effect later.**
niftyindices publishes ~1 month ahead (e.g. announced 2026-08-10, effective 2026-09-30),
so a correctly captured event routinely post-dates today's `index_meta`. That is
expected and safe: `check_recon_consistency()` in `scripts/refresh_all.py` compares only
effective events (while still counting a future event's `source_url` as captured, which
is what dedups the PR scan), and `membership.py` replays backward from today's members,
so a future event's adds are ignored and its removals correctly bound the exit date.
Do **not** postpone capture to the effective date, and do **not** silence a pending
review with `--mark-pr-reviewed` — that flag means "no tracked index affected", and
using it otherwise permanently hides a real membership change.

If it lists **UNCAPTURED press releases**, run the capture procedure in
`market-hunt/docs/capture_index_rebalance.md` for each:

1. Download the PDF (browser User-Agent required — plain fetch gets reset):
   `curl -A "Mozilla/5.0 ..." -o press_release/<name>.pdf https://www.niftyindices.com/Press_Release/<name>.pdf`
2. Extract text (venv has pypdf) and read which indexes changed. Only these matter:
   NIFTY50/100/200/500, NIFTYMIDCAP150, NIFTYSMALLCAP250, NIFTYMICROCAP250, and
   NIFTY500MICRO750 (= "Nifty Total Market" sections in PRs).
3. **Tracked index affected** → write a changelog CSV row
   (`effective_date,added,removed,source_url`, `;`-separated symbols, current
   canonical NSE symbols, never guessed) and load:
   `.venv/Scripts/python.exe scripts/load_reconstitution.py --index <NAME> --csv <file>`
   Also append the same rows to `tvagent/backend/data/seed/<index>_changelog.csv`
   when that seed file exists (it is tvagent's canonical changelog).
4. **No tracked index affected** (Shariah/ESG/SME/IPO/sectoral/corporate-group only) →
   `.venv/Scripts/python.exe scripts/refresh_all.py --mark-pr-reviewed <name>.pdf --note "<why>"`
   so it never flags again.
5. **Load prices for the added members** (capture doc Step 5) — a point-in-time sim
   must be able to trade a stock the day it joins. New microcap/total-market entrants
   usually have no `symbol_mappings` row; that is fine (mappings are advisory), just
   `download-stock <SYM>` each, retry the failures once, then compute indicators for
   them. Names with very little history report `insufficient (0 rows)` from
   `compute_indicators` — expected until the warmup window fills.
6. Re-run `--only recon` — must come back OK.

Verify the extraction rather than trusting the parse: PR tables give **Sr. No.,
Company Name, Symbol**, so check the Sr. numbers come out contiguous `1..N` and that
excluded/included counts match the PDF. Symbols come from the PR verbatim — the only
name→symbol mapping judgment is for renames (use the *current* canonical symbol).

No fabrication, ever: every event needs the real niftyindices `source_url`; if a
change can't be verified from the PR text, stop and report it instead of guessing.
The same rule covers tickers: if a newly added name returns no data from tvdatafeed,
leave it unresolved and report it — never substitute a plausible-looking symbol.

Corporate actions arrive as PRs too (not just semi-annual reviews): mergers swap
symbols (GSPL→CIEINDIA style replacements), demergers add zero-price dummies that are
later excluded (Vedanta 2026 pattern: dummies in Apr 30, real entities VAML/VEDPOWER/
VOGL/VISL excluded Jun 19/23/24 after listing). Record adds with the final listed
symbols.

⚠ This paragraph is about **index membership** — which stocks are constituents. It is a
different concern from **price adjustment** (splits/bonuses re-pricing a series), which
the `prices` stage handles separately via `fetch_corporate_actions()`. Conflating the
two is how the KIRLPNU split went unnoticed for three weeks: the recon stage's
corporate-action handling was mistaken for price-adjustment coverage that did not exist
on this branch. Both are needed; neither substitutes for the other.

## Known standing issues

- `recon` reports "historical replay gaps" as info: pre-2026 events store pre-rename
  symbols (ZOMATO→ETERNAL, NIITTECH→COFORGE, SRTRANSFIN→SHRIRAMFIN, …) and a few
  2024-25 mid-cycle specials were never captured (CGCL/ERIS/LEMONTREE/UCOBANK
  re-entries). Point-in-time sims across those windows carry this debt; repairing it
  is a separate deliberate pass over the PR archive.
- 12 MARKET INDEXES benchmarks (NIFTY HEALTHCARE, NIFTY50 EQL Wgt, Nifty EV, …) have
  never had prices — missing `INDEX_TV_SYMBOLS` mappings in `tv_price_client.py`.
  Resolving them authoritatively needs `TV_USERNAME`/`TV_PASSWORD` (`search_symbol` is
  blocked anonymously); do not guess tickers — CNX100 once wrongly matched the
  equal-weight index.
- Symbol mappings are **advisory, not a precondition** (fixed 2026-08-01). The price
  source resolves by symbol and prices are keyed `_id=<symbol>_<date>`, so
  `nse_scrip_code` is a lookup field. Never reintroduce a `mapped_only=True` gate on
  the download path, and never let `refresh-mappings` overwrite a known code with
  `None` — either one silently freezes every symbol the `nsecodes.csv` snapshot can't
  match (it froze ~64, including the Nifty 100 / Midcap 150 / Smallcap 250 /
  Microcap 250 benchmarks, before the fix). Pinned by
  `tests/test_symbol_mapping_resilience.py`.
- The 4 benchmark series `Nifty 100` / `Nifty Midcap 150` / `Nifty Smallcap 250` /
  `Nifty Microcap 250` exist only in `prices_*`, not in `index_meta['MARKET INDEXES']`,
  so `refresh-mappings` never creates mapping docs for them. Harmless now that
  mappings are advisory — but don't "fix" it by re-adding a mapping requirement.
- Anonymous tvdatafeed is fine for incremental top-ups; set `TV_USERNAME`/`TV_PASSWORD`
  env vars only for full-history reloads.
- Never chain pipeline stages manually with `&&` — stages exit nonzero on partial
  failure by design; the pipeline already isolates them.
- **niftyindices throttles by IP** after heavy use (a full reload is 75 CSV fetches).
  Symptom: every URL in the first chunk fails / direct curl times out. The `urls`
  stage fast-aborts in that case. Don't retry in a loop — existing `index_meta` rows
  stay intact until a reload succeeds, so just re-run `--only urls` a few hours later.
  One reload per day is plenty; constituents only move on rebalances/corporate actions
  (which `recon` catches independently).
