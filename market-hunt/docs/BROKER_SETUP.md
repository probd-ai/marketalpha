# Broker Setup — Kite + Dhan

Security model, then setup.

⚠ **This system places real orders on both Dhan and Kite.** Stage 2 has shipped. Order
placement runs behind an approval flow — see the gate list below. An earlier version of
this page said nothing here places orders; that was false.

## Security model

| Control | Implementation |
|---|---|
| Secrets at rest | Only in `market-hunt/.env` (gitignored) — never in code, Mongo, or the frontend |
| Kite daily token | Exchanged server-side (api_secret never leaves the box), cached **Fernet-encrypted** in `.secrets/` (gitignored); auto-evicted on expiry |
| API surface | `/api/broker/*` is read-only (holdings/funds/profile). Execution lives on the **plan-scoped** verbs `/api/broker-mapping/{id}/sync-plan`, `/api/sync-plan/{id}/approve`, `/execute`, `/refresh-status` — none of which accept a caller-supplied symbol, quantity or price |
| Order gate | A real order needs ALL of: `MH_TRADING_ENABLED=true` · plan approved by a human · plan < 24h old · plan funded · within per-order (`MH_MAX_ORDER_VALUE`) and per-run (`MH_MAX_EXECUTION_VALUE`) caps · NSE market hours · `dry_run=false` explicitly passed (it defaults to **true**). Every attempt is audited; an atomic lock prevents double-punching |
| Access guard | `MH_API_TOKEN` unset → only localhost clients allowed (403 otherwise). Set → every call needs `X-API-Key` (constant-time compare) — required before exposing via tunnel |
| Leak hygiene | Broker errors are sanitized; tests assert tokens never appear in responses or error text (`tests/test_broker_security.py`, 11 tests) |

## One-time setup

1. `cd market-hunt && copy .env.example .env`
2. **Dhan** (data leg: portfolio + funds; static ~30-day token)
   - web.dhan.co → My Profile → *DhanHQ Trading APIs* → generate Access Token
   - Put `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` in `.env`
   - Rotation: regenerate on the site, replace the value, restart backend
3. **Zerodha Kite Connect** (second leg; daily login)
   - developers.kite.trade → Create app (type *Connect*; redirect URL can be
     `http://localhost:3000/analytics/brokers`) — note this is a paid subscription; verify
     current pricing before subscribing
   - Put `KITE_API_KEY` and `KITE_API_SECRET` in `.env`
   - Daily: open the login link on the Brokers page → after redirect copy `request_token`
     from the URL → paste in the Connect box. Token cached encrypted until Kite expires it
4. Restart the backend. Open **http://localhost:3000/analytics/brokers**.
5. Before any internet exposure (Cloudflare tunnel etc.): set a long random
   `MH_API_TOKEN` in `.env`, restart, and enter the same value in the page's
   X-API-Key box (stored in your browser's localStorage only).

## What the page gives you

- Per-broker connection status, funds (available / utilized / withdrawable), holdings with P&L
- **Live vs simulation**: pick a portfolio → diffs broker holdings against the newest cached
  sim book → matched (value drift) / sim-only (to buy) / broker-only (not in system)

## Verify

```bash
cd market-hunt && .venv/Scripts/python.exe -m pytest tests/test_broker_security.py -q   # 11 tests
curl http://127.0.0.1:3001/api/broker/status          # from the machine: 200
curl http://<lan-ip>:3001/api/broker/status           # remote without token: 403
```

Field caveat: Dhan's fund-limit API historically spells the field `availabelBalance`; the
client accepts both spellings. If funds read 0 with a valid token, check the raw response.

## Stage 2 — approve & execute (added 2026-07-05)

Flow: generate sync plan → **Approve plan** → **Execute** slices (default 20%/day per the
validated 5-day stagger; SELLs before BUYs; LIMIT prices from the plan).

Layered guards — every one must pass for a real order:
1. `MH_TRADING_ENABLED=true` in .env (kill switch — ships as `false`)
2. Plan approved by a human (button), feasible (funded), and < 24h old
3. `dry_run=false` explicitly (default is dry run — payload preview, nothing sent)
4. NSE market hours (09:15–15:30 IST, Mon–Fri)
5. Value caps: `MH_MAX_ORDER_VALUE` (default ₹2L/order), `MH_MAX_EXECUTION_VALUE` (₹20L/run)

Every attempt — including dry runs — is audited in Mongo `execution_log`. Fill status:
**Refresh fills** pulls the day's order book and updates per-row punched/filled quantities.

Broker identifiers resolve from Dhan's scrip master (auto-downloaded, cached in `instruments`,
keyed on ISIN so a ticker rename still resolves;
`POST /api/broker/dhan/instruments/refresh` to re-pull). ⚠ Before first live punch: verify the
order payload fields against current DhanHQ v2 docs, place ONE small manual test order via
`dry_run=false` with a tiny `MH_MAX_EXECUTION_VALUE`, and confirm it lands in Dhan web.

Daily stagger workflow (recommended): each day of the window — regenerate plan (captures
yesterday's fills automatically) → approve → execute 1/(days-remaining) in the closing window.
