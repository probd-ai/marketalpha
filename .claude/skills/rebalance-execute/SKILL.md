---
name: rebalance-execute
description: Run a live rebalance end-to-end against a broker account (Dhan or Kite) — build the sync plan, explain it, take the user's approval, then advance the group chain (sell a group, buy the next from realized proceeds) one step at a time with dry runs first. Use when the user says "rebalance", "execute the plan", "punch the orders", "sync my portfolio to the broker", asks what would be traded, or asks to set up broker credentials for trading.
---

# rebalance-execute — driving a live rebalance

The strategy picks. `portfolio-ops` keeps the books. `research-analyst` contextualises.
**This skill executes** — it turns a target book into real orders on a funded account.

**You are the narrator and the router. You are never the calculator.**
Every quantity, price, cap and clamp is computed by `broker_sync.py` and
`order_execution.py`. Your job is to call the right verb, read back what it
produced, explain it in plain terms, and stop for the human at the approval gate.

## Five rules that are not negotiable

1. **Never approve on the user's behalf.** `POST /approve` requires the user to say
   so in this conversation, in words, after seeing the plan. "Go ahead" on a *plan*
   is not "go ahead" on a *punch*, and approval of one plan never carries to the next.
2. **Never do the arithmetic yourself.** Quote what the plan returns. Do not
   recompute, adjust or sanity-check a quantity by reasoning — a reasoning slip is
   indistinguishable from a real gap, and here it becomes a real order.
3. **Dry run first, always.** `/execute` defaults `dry_run=true`. Show the payload,
   get an explicit go-ahead, then repeat with `dry_run=false`. Never pass
   `dry_run=false` on the first call for a group.
4. **No endpoint takes a symbol, quantity or price from you.** If you ever find
   yourself wanting to supply one, stop — that is the property that makes this safe.
5. **Report refusals verbatim.** A 400 from a guard is the system working. Relay the
   message; never work around it, and never suggest disabling a guard to proceed.

## Before anything: preflight

Run these and lead with the state, not the numbers.

| Check | Call | Blocks if |
|---|---|---|
| Broker reachable + session | `GET /api/broker/status` | `configured:false` or session inactive |
| Credentials present | `python scripts/credentials.py status` | nothing stored |
| Book is current | `GET /api/portfolio/{portfolio_id}/last-run` | run older than the price data → **re-run the portfolio first** |
| Guards | `GET /api/broker/status` → `trading_enabled` | see below |

⚠ **A stale cached run poisons everything.** If the portfolio's run predates the last
data refresh, the plan compares today's holdings to last month's target. Say "cannot
judge", never "looks fine".

## Credential setup (when the user needs it)

Credentials live **only** in the Windows Credential Manager (DPAPI, per Windows user).
There is no `.env` fallback — a missing credential fails loudly by design.

Tell the user to run these themselves, in their terminal. **Never ask for a credential
value in chat, and never offer to store one for them** — values are typed at a hidden
prompt so they never reach this conversation, a file, or shell history.

```
python scripts/credentials.py store      # rebalance morning (hidden prompts)
python scripts/credentials.py status     # names only, never values
python scripts/credentials.py clear      # wipe when done trading
```

- **Dhan** — `DHAN_CLIENT_ID` + `DHAN_ACCESS_TOKEN` from web.dhan.co → My Profile →
  DhanHQ Trading APIs. Static ~30-day token, so it is the larger standing exposure:
  encourage wiping it after each rebalance.
- **Kite** — `KITE_API_KEY` + `KITE_API_SECRET` from developers.kite.trade, **plus a
  daily login**: the access token is flushed ~07:30 IST and the exchange requires a
  manual 2FA login each day. Open the login link on `/analytics/brokers`, copy
  `request_token` from the redirect URL, paste it into the Connect box.

If `status` shows nothing stored, that is the intended resting state between
rebalances — the machine holds no broker credential at all. Not an error.

## The flow

### 1 · Build the plan
`POST /api/broker-mapping/{mapping_id}/sync-plan`

Pulls the newest cached run's `current_book`, live holdings and funds, fetches fresh
TradingView LTPs, and computes the diff. Read back:

- `totals` — `target_total`, `scale_factor`, `available_cash`, `buy_value`,
  `sell_value`, `net_cash_needed`, `action_count`
- `feasible` — **if false, stop.** Approve and execute both refuse a NOT FUNDED plan.
- `fresh_quotes: N` of the candidate count — a low number means limit prices are
  anchored to stale closes, which matters a lot at a ±0.1% band. Say so.
- `warnings` — liquidity caps (`suggested_slices`), funding notes
- `skipped` — with reasons; "held in broker, not in sim" is the common one
  (`sell_unknown` is off by default and never auto-sells unknown names)

### 2 · Explain it, then stop
Summarise: how many sells, how many buys, net cash, anything liquidity-capped, and
anything skipped that the user might expect to see traded. **Then ask for approval.**

### 3 · Approve (human gate)
`POST /api/sync-plan/{plan_id}/approve` — draft only, refuses if not funded or >24h old.
Groups are built here: single-sided, SELLs first, `group_size` (default 3).
Response reports how many groups were created.

### 4 · Advance the chain, one step per call
`POST /api/sync-plan/{plan_id}/execute?fraction=0.2&dry_run=true`

Each call does exactly one thing and returns a `step`:

| step | meaning | what you do |
|---|---|---|
| `PUNCH` | this group is ready to send | show the payload · dry run first · get go-ahead · repeat with `dry_run=false` |
| `WAIT` | orders still open inside the fill window | report and wait — do not re-punch |
| `SETTLE` | fills terminal, proceeds credited | report realized cash, call again |
| `REPRICE` | fill timeout with an unfilled remainder | ladder 0.1→0.3→0.6%, max 2 retries or 0.6% slippage; exhausted rows carry and are reported |
| `STALLED` | a BUY group has no budget | sells did not realize enough — report the reason, do not force it |
| `COMPLETE` | every group settled | done |

**SELLs always precede BUYs** — their proceeds fund the buys. Both brokers credit sell
proceeds immediately (confirmed 2026-09-06).

### 5 · Fills
`POST /api/sync-plan/{plan_id}/refresh-status` — pulls the day's order book, updates
`punched_qty` / `filled_qty` / row status.

## Guards — every one must pass for a real order

`MH_TRADING_ENABLED=true` · plan approved by a human · plan < 24h old · plan funded ·
per-order cap `MH_MAX_ORDER_VALUE` (₹2L) · per-run cap `MH_MAX_EXECUTION_VALUE` (₹20L) ·
NSE hours 09:15–15:30 IST Mon–Fri · `dry_run=false` passed explicitly.

Every attempt including dry runs is audited in `execution_log`. An atomic lock
(stale after 10 min) prevents two concurrent executes double-punching.

## Defaults worth quoting to the user

| Setting | Default | Why |
|---|---|---|
| `limit_band_percent` | **0.1** | user's choice; tight — expect non-fills on low-ADV names |
| `group_size` | 3 | sell 3, buy 3 from the proceeds; `0` disables chaining |
| `tolerance_percent` | 5.0 | deadband — stops churn on noise |
| `min_order_value` | ₹500 | |
| `max_participation_percent` | 10 | of 20d ADV; over-cap orders are trimmed and flagged |
| `sell_unknown` | false | never auto-sells names the sim does not know |
| `product` | CNC | delivery only, never intraday |

## Gotchas (all real, all hit during the build)

- **±0.1% sits inside the spread on microcaps.** An unfilled SELL starves the BUY
  group behind it and the rebalance quietly under-executes. If a chain stalls, this
  is the first thing to check — say it plainly rather than reporting "no orders".
- **A 24h-old plan is a stale-price plan.** At a 0.1% band, an overnight gap means
  nothing fills. Generate → approve → punch in one session.
- **Sells are clamped to `sellable_qty`, not total holdings.** T+1 and pledged stock
  cannot be sold. The clamp warns and shrinks the row; it does not block the plan. A
  clamped order carries `clamped_from` — surface it, the user's expected quantity
  changed.
- **Unresolved instruments refuse the whole execution (400).** Identity resolves on
  ISIN first, symbol as fallback. An unresolved row is never guessed. Refresh the
  master with `POST /api/broker/dhan/instruments/refresh`.
- **Kite orders by `tradingsymbol`, Dhan by numeric `securityId`.** The adapter
  absorbs this. If that difference ever surfaces in an error you show the user,
  it is a bug, not something for them to act on.
- **`WAIT` is not a failure.** Do not re-punch, do not retry, do not "help". The
  group is in flight; call again later.
- **Never disable a guard to make something work.** `MH_MARKET_HOURS_CHECK=false`
  exists for off-hours dry runs only. If the user asks to trade outside hours,
  explain that nothing can fill anyway.

## Endpoints

| call | use |
|---|---|
| `GET /api/broker/status` | connectivity, session, `trading_enabled` |
| `GET /api/broker/{name}/holdings` · `/funds` | live account state |
| `POST /api/broker-mapping/{id}/sync-plan` | build the plan |
| `GET /api/broker-mapping/{id}/sync-plan/latest` | most recent plan |
| `GET /api/sync-plan/{id}` | read one plan |
| `POST /api/sync-plan/{id}/approve` | **human gate** — builds groups |
| `POST /api/sync-plan/{id}/execute` | advance one step (`fraction`, `dry_run`) |
| `POST /api/sync-plan/{id}/refresh-status` | pull fills |
| `GET /api/portfolio/{portfolio_id}/live-vs-sim` | read-only comparison |

Localhost-only unless `MH_API_TOKEN` is set (then `X-API-Key`).

## After trading

Remind the user to run `python scripts/credentials.py clear` and to set
`MH_TRADING_ENABLED=false`. Between rebalances there should be nothing on the machine
that can place an order.
