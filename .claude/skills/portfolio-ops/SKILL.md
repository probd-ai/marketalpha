---
name: portfolio-ops
description: Reconcile a portfolio's live broker holdings (Dhan/Kite CSV exports) against what the system says it should hold — resolve broker company names to tickers, report quantity gaps, mark discretionary positions, lock the agreed baseline after a rebalance, and keep the journal. Also prepares for an upcoming rebalance. Use when the user asks whether their live portfolio is in sync, uploads/points at a broker holdings file, says they have rebalanced, asks what to trade, or wants to prepare for the next rebalance.
---

# portfolio-ops — bookkeeper and intern for a live book

The strategy picks. `research-analyst` contextualises. **This skill keeps the books**:
what the system says you should hold, what the broker says you do hold, the gap
between them, and a durable record of what you decided and why.

**The user is the executioner.** You never place an order and you never lock a
baseline. You read, resolve, compute, explain and recommend — a human presses the
button.

## Three rules that are not negotiable

1. **Never guess a symbol.** Dhan exports identify stocks by display name, and our
   `nse_name` is abbreviated to the point of uselessness — "Great Eastern Shipping
   Company" is stored as `THE GE SHPG.LTD`. A wrong guess reconciles the wrong stock
   and becomes a wrong order. Propose a mapping, get it confirmed, store the alias.
   The API refuses any alias whose symbol is unknown to `symbol_mappings`/`index_meta`.
2. **Never do the arithmetic yourself.** `/status` returns computed gaps. Quote them;
   do not recompute, adjust or "sanity-check" them by reasoning. A reasoning slip
   would be indistinguishable from a genuine gap.
3. **Never lock.** `POST /lock` is the human gate. Prepare it, explain every
   discrepancy, recommend a disposition — then ask.

## Tools

All localhost-only unless `MH_API_TOKEN` is set (then `X-API-Key`).

| call | use |
|---|---|
| `GET /api/holdings/{pid}/status` | the workhorse: read file → resolve → reconcile → state |
| `GET/POST /api/holdings/aliases` | the broker-name → ticker table you populate |
| `PUT /api/holdings/{pid}/config` | `holdings_path`, and `pin_account:true` to bind the account SET |
| `POST /api/holdings/automap` | `{root}` — wires every sub-folder whose name matches a portfolio |
| `PUT /api/holdings/{pid}/discretionary` | `{SYMBOL: qty}` the user owns privately |
| `POST /api/holdings/{pid}/lock` | human-approved baseline (needs `snapshot_id`) |
| `GET /api/holdings/{pid}/journal` | lock history |
| `GET /api/portfolio/{pid}/last-run` | current system book, sleeves, allocation |
| `GET /api/portfolio/{pid}/preview` | probable adds/exits at the next rebalance |

`/status` is safe to call repeatedly — unchanged files are hash-deduped, so no
snapshot is written unless the content actually changed.

## The states, and what each one means for the user

```
NO_PATH        no folder configured              → run /config, or /automap
NO_FILE        folder set, nothing readable      → user must export from the broker
WRONG_ACCOUNT  an export is from an account not  → STOP. Do not reconcile.
               pinned to this portfolio
AMBIGUOUS      2+ exports with no account id     → STOP. Cannot tell two accounts from
                                                    one account exported twice; summing
                                                    would double a position.
UNRESOLVED     names not mapped to tickers       → resolve first; gaps NOT computable
RUN_STALE      the portfolio's cached run is     → STOP. Re-run the portfolio; its book
               older than the price data           predates the latest rebalance.
REBALANCE_DUE  file predates the last rebalance  → STOP. Ask for a fresh export.
UNLOCKED       no baseline yet                   → review, then offer to lock
LOCKED_DRIFT   differs from the locked baseline  → something moved outside the system
LOCKED_CLEAN   matches the baseline              → nothing to do
```

⚠ **`LOCKED_DRIFT` is currently overloaded.** After a rebalance the user simply has
trades outstanding, which is not the same as the book drifting unexpectedly — but both
show as DRIFT. A `REBALANCE_PENDING` state is designed and NOT yet built. Until it is,
check `last_rebalance` against the lock's date before calling drift a problem: if a
rebalance happened after the lock, the gaps are the user's pending execution list.

⚠ **`REBALANCE_DUE` and `UNRESOLVED` are hard stops, not warnings to narrate past.**
In both, the gap numbers are meaningless — a stale file compares today's target to
last month's holdings. Say "cannot judge", never "looks fine".

## Workflow A — "is my portfolio in sync?"

1. `GET /status`. Lead with the state, not the numbers.
2. If `UNRESOLVED` → Workflow B, then re-run.
3. If gaps exist, present them as a table: symbol · action · quantity · target ·
   observed. Order by quantity (the API already does).
4. Anything in `unclassified` is a position the system never targeted. **Never call
   it a SELL** — ask whether it is discretionary (then `PUT /discretionary` with the
   quantity) or a leftover to exit.
5. State the file's age and where the timestamp came from. Dhan states its own
   download time (`as_of_source: broker`); Kite does not, so we fall back to file
   mtime (`as_of_source: file_mtime`) — weaker provenance, say so.

## Workflow B — resolving broker names (once per name)

`/status` returns `unresolved: [...]`. For each, identify the NSE symbol from your own
knowledge of Indian listed companies, then confirm with the user **as a batch table**
before writing any of them — one wrong row is a wrong trade.

```
POST /api/holdings/aliases   {"raw_name": "Great Eastern Shipping Company",
                              "symbol": "GESHIP"}
```

Watch for renames (a broker may show the new name while our data has the old) and for
symbols genuinely absent from our universe — those need a data fix, not an alias.

## Workflow C — "I've rebalanced"

1. Ask for a **fresh export**; the old file is stale by definition.
2. `GET /status` → note `snapshot_id`.
3. Compare and present three things separately:
   - **system target vs observed** — did the rebalance execute as intended?
   - **locked baseline vs observed** — what moved since the last lock?
   - anything unclassified
4. For each discrepancy, ask which it is: `accepted` · `pending` · `partial` ·
   `intentional`.
5. Offer the lock. On approval `POST /lock` with `snapshot_id`, `locked`,
   `dispositions`, `note`.

The lock stores three vectors — `target`, `observed`, `locked`. That triple is what
makes the journal worth keeping: target vs observed **avg cost is measured execution
cost**, which feeds the break-even slippage question in the research (~145bp/side).

## Workflow D — intern: prepare for the next rebalance

Ask 1–2 days ahead:
1. `GET /api/portfolio/{pid}/preview` → probable adds/exits.
2. Invoke **`research-analyst`** on the probable adds — news/filing risk before buying.
3. `GET /status` → confirm the current book is reconciled, so the rebalance starts
   from a known position rather than an assumed one.
4. Produce a prep note: what is likely to trade, what to watch, what is unresolved.

Preview is *probable*, not final — it moves with prices until the rebalance date. Say so.

## Workflow E — the execution-timing gap (how the user actually trades)

The system rebalances at the **close of** the rebalance date. The user cannot act at
that instant, so the agreed pattern is:

1. **T−1** — `GET /preview` ("probable moves") and plan the trades.
2. **T** — execute the bulk during the day at reasonable prices.
3. **T+1** — fresh export in, `GET /status`, and the remaining gaps ARE the cleanup
   list. Execute those, then lock.

So a `BUY`/`SELL` list on T+1 is expected, not a failure. Present it as
"remaining to execute", not "your book has drifted".

⚠ The preview is computed on T−1 data, so a name that surges ON the rebalance day can
enter the book after the plan was made. One or two such misses per rebalance is normal
and is exactly what step 3 catches.

⚠ The backtest assumes execution at the T close, which none of this achieves. For a
momentum strategy the lag has a direction — you buy after another day of the move — so
live results will differ from backtest for reasons that are NOT slippage. Nobody has
measured this yet; do not present backtest returns as what the user should expect live.

## Mapping a portfolio to an account

Folders live **outside the repo** — exports carry PII (holder name, UCC, mobile,
email); none is persisted, and the account is tracked by a one-way fingerprint.
**One folder per portfolio, named exactly for the portfolio**, which is what makes the
mapping derivable rather than hand-entered:

```
D:\gs\workspace\live_portfolio    Alpha-Market-probdx0.1\   holdings-1.csv   holdings-2.csv     <- two Dhan accounts
    LargeCaps\                holdings.csv                        <- one Kite account

POST /api/holdings/automap   {"root": "D:\gs\workspace\live_portfolio"}
```

`automap` wires every sub-folder whose name matches a portfolio exactly, and reports
the rest as `unmatched_folders` / `portfolios_without_folder` rather than guessing.

**A portfolio can be funded by SEVERAL broker accounts.** Every CSV in the folder is
read and quantities are SUMMED. `pin_account:true` binds the whole set present, so a
later export from an account outside it trips `WRONG_ACCOUNT`. Same account exported
twice collapses to the newest; it never double-counts.

State as of 2026-08-30 (after `automap`):

| portfolio | universe · cadence · N | live |
|---|---|---|
| Alpha-Market-probdx0.1 | N500MICRO750 · quarterly · top-20 | 2 Dhan accounts, 27 pos, pinned |
| LargeCaps | NIFTY200 · monthly · top-7 | 1 Kite account, 13 pos — **9 of 11** system positions held |
| micro | N500MICRO750 · quarterly · top-20 | no folder yet |
| midcap | NIFTYMIDCAP150 · monthly · top-10 | no folder yet |

All 27 Dhan display names are resolved; the alias table is populated and further Dhan
reads need no new mappings unless the book changes.

## Where the user sees this

- `/analytics/portfolios` — a sync chip per card (hover for the message)
- `/analytics/portfolios/{id}` — **Book · system vs live**, one full-width table:
  Symbol · Tier · Sector · System · Live · Action · Price · Value · Wt · P&L, ordered
  BUY → SELL → match → yours. Live/Action columns are WITHHELD in any blocked state,
  so a stale comparison degrades to the plain system book instead of showing confident
  wrong numbers.
- `/analytics/portfolios/{id}/edit` — folder path, pin toggle, and per-position
  "Mine, not the strategy's" buttons for discretionary marking.

## Gotchas (all hit in practice)

- **`micro` and `Alpha-Market` have identical sleeve configs** — same universe,
  cadence and top-20 — so they hold the same names and differ only in capital. Two
  identically-configured portfolios in ONE broker account cannot be told apart from
  holdings alone. They need separate accounts, or reconciliation is ambiguous.
- **Windows' `" (1)"` duplicates were two different account holders.** Identity is
  proved from inside the file, never by filename. The folder is AGGREGATED, not picked
  from — reading only the newest would reconcile against part of a multi-account book
  and report the rest as missing, which is worse than refusing.
- **Dhan filenames embed the date**, so they change every download — point at the
  folder, never a fixed file path.
- **Target quantities are rounded to whole shares.** The simulated book holds
  fractional shares (37.0813 = capital ÷ price); a real book holds 37. Without
  rounding every position would show a permanent ~0.08 gap.
- **Never reconcile on value.** Broker LTP is from the export's own moment, our prices
  from the last data refresh — every value comparison differs for reasons that carry
  no information. Quantities only. Show value as context, tagged with its timestamp.
- **Kite exports carry no account identity**, so `pin_account` cannot bind them and
  `WRONG_ACCOUNT` can never fire for Kite. Two different Kite files in one folder are
  therefore `AMBIGUOUS` and block: they could be two accounts or one account exported
  twice, and summing would double a position.
- **A cached run older than the price data poisons everything.** `RUN_STALE` exists
  because LargeCaps' run was computed against 08-14 data and had not seen the 08-17
  rebalance; gaps against it looked entirely healthy and were entirely wrong.
- **Always trust `/status` over your own comparison.** A bug that sorted runs by a
  null-able field made LargeCaps look like 3/13 overlap when the truth was 9/11 — and
  the wrong number was confidently reported for several turns before the user caught
  it. If a reconciliation contradicts what the user sees on the page, the tooling is
  wrong until proven otherwise; say so rather than explaining the discrepancy away.
