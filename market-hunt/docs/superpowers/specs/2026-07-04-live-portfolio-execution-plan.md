# Live Portfolio → Internet + Broker Execution — Plan

*Drafted 2026-07-04. Target portfolio: `portfolio_1783106608552` (4 sleeves, ₹20L,
monthly first/mid, PIT NIFTY500MICRO750 + NIFTY200). Status: PLAN — nothing built yet.*

## Guiding constraints

- **Cost floor first.** Delivery trades on a discount broker = ₹0 brokerage; only statutory
  charges (~0.11% one-way incl. STT 0.1% delivery). Avoid intraday product types entirely.
- **Human stays in the loop until trust is earned.** Notify → confirm-to-punch → auto, in that
  order. Never skip a stage.
- **Tax-aware, not tax-driven.** Exits the strategy demands are executed regardless; tax
  preference only breaks ties inside the rank-tolerance band (details below).
- **Secrets never in the repo.** Broker API keys/tokens live in env vars / OS keyring only.

## Stage 0 — Publish the terminal over the internet (read-only)

Cheapest safe option: **Cloudflare Tunnel + Cloudflare Access** (free tier).
- `cloudflared` daemon on this machine → tunnel to `terminal.<your-domain>`; no port
  forwarding, no public IP, TLS automatic.
- Cloudflare Access in front: email-OTP allowlist (just yours to start). The FastAPI/Next
  servers stay bound to localhost.
- Alternative when the machine can't stay on: small VPS (Hetzner CX22-class) + Mongo restore,
  same tunnel. Defer until needed.
- Prereq hardening (small): CORS currently `*`-ish for :3000 — restrict to the tunnel origin;
  add a simple bearer token on mutating endpoints (`POST/PUT/DELETE`), read-only stays open
  behind Access.

## Stage 1 — Notifications (build first; zero broker risk)

**Telegram bot** (free, instant, works on phone) + the existing preview endpoint.

- T−2 sessions before each sleeve's due date (due-date estimator already exists):
  send "upcoming rebalance" digest from `GET /api/portfolio/{id}/preview` — probable
  entries/exits with scores, tiers.
- On rebalance morning (day-of, after prior close data is loaded): final order list —
  symbol, side, qty (from target equal-weights vs current book), est. value, and a
  copy-paste basket CSV.
- Implementation: `scripts/notify_rebalance.py` + Windows Task Scheduler (or the existing
  scheduler page) daily at ~08:30 IST; state in Mongo (`notification_log`) so re-runs don't
  double-send. Refresh prices/indicators before preview (hook into `scripts/refresh_all.py`).

**Broker account prep (works for any stage):**
- Discount broker with a stable retail API. Candidates: Zerodha Kite Connect, Upstox,
  Dhan, Fyers (API pricing/limits change — verify current terms before choosing; some are
  free for personal use, Kite Connect bills monthly).
- Enable DDPI (demat debit instruction) so sells don't need CDSL TPIN/OTP per order —
  required for any automation.
- Dedicated demat+trading account for this system (clean FIFO lots, clean P&L, no manual
  trades mixing in). Fund it with exactly the system capital.
- Margin: keep ~1 rebalance's turnover (~40–50% of book, sells settle T+1) — sells fund
  buys same-day under buy-against-sell-proceeds; verify broker's same-day credit policy.

## Stage 2 — Semi-auto (confirm-to-punch)

- Backend endpoint `POST /api/portfolio/{id}/orders/prepare`: converts preview + live book
  into a concrete order basket (qty rounding, min-value skips, LTP-based limits).
- Telegram message with an **Approve** button (or basket deep-link where the broker supports
  it, e.g. Kite basket publish) → on approve, punch via broker API, then reconcile fills
  back into a `broker_fills` collection and diff vs target.
- Safety rails: max order value per name, max total turnover per day, market-hours check,
  dry-run flag default ON, kill-switch env var.

## Stage 3 — Full auto (only after ≥3 clean manual months)

- Same pipeline, approval step replaced by policy: auto-punch if (a) data fresh, (b) preview
  vs day-of divergence < N names, (c) turnover < cap. Anything else falls back to Stage 2.
- Reconciliation job after close: fills vs simulation; alert on drift > threshold.
  Portfolio page gets a "LIVE vs SIM" divergence strip.

## Tax-aware rebalance ordering (India, current regime — verify rates at build time)

Facts that shape the design:
- Equity delivery: STCG (≤12m) ~20%, LTCG (>12m) ~12.5% above the annual exemption.
- **FIFO is mandatory per demat account per ISIN** — you cannot cherry-pick lots within one
  account, so "sell the loss-making lot" only works at the *symbol* level, not lot level.
- Harvested losses offset gains (STCL against both STCG/LTCG; carry-forward 8y with ITR filed
  on time).

Design (order of operations at each rebalance):
1. **Mandatory exits** (rank-out per strategy): execute all, losers first only for cash-flow
   sequencing — no tax discretion here, the strategy rules.
2. **Discretionary band**: names within a tolerance band of the rank cutoff (e.g. rank
   N±3 or score within X% of cutoff) — prefer *keeping* profitable names <12m old (defer
   STCG) and *exiting* loss-makers (harvest). Band width is a tunable; backtest it before
   enabling (it changes the strategy — measure the alpha cost vs tax saved).
3. **Equal-weight trims**: when trimming winners to re-true weights, allow a "trim tolerance"
   (skip trims < ₹T or < W% drift) — fewer taxable events and fewer charges for negligible
   tracking error. Also tunable + backtestable.
4. **March check**: before FY end, harvest any unrealized losers already near the exit band
   (pull-forward), and report the FY realized STCG/LTCG ledger from `broker_fills`.
5. Report, don't decide, anything else: the terminal shows per-position age (days to LTCG),
   unrealized P&L, and estimated tax impact of the pending order list.

Note from the backtest ledger: full-exit realized P&L is structurally negative (the system
cuts losers and lets winners run — 47% win rate on exits, profits come from trims/holds), so
harvestable losses are plentiful; the binding tax question is the *trim* stream, which FIFO
makes oldest-lot-first (often the most profitable). The trim-tolerance knob is therefore the
main tax lever.

## Build order (proposed)

| # | Deliverable | Effort | Risk |
|---|---|---|---|
| 1 | Cloudflare tunnel + Access, CORS/token hardening | S | none |
| 2 | Telegram notify: T−2 preview + day-of order list | M | none |
| 3 | Order-prepare endpoint + qty/limit logic + dry-run | M | low |
| 4 | Broker API integration (auth, orders, fills) behind kill-switch | M | med |
| 5 | Approve-button punch (Stage 2 live) | S | med |
| 6 | Reconciliation + LIVE-vs-SIM strip | M | low |
| 7 | Tax band/trim-tolerance backtest, then enable | M | med |
| 8 | Stage 3 auto policy | S | high — last |

Open decisions (need your call): broker choice · domain name · Telegram vs WhatsApp/email ·
band widths to backtest · whether the VPS move happens before or after Stage 2.
