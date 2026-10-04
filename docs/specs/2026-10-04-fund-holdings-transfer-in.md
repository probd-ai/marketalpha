# Fund desk — extra fills, price warnings, other holdings, undeployed capital

Status: v2 APPROVED 2026-10-04 (v1 "transfer-in breakdown" built the same day, rejected by the user as too complex
and replaced by this) · extends `2026-09-27-fund-trading-desk-design.md` §6 (tickets) and Tracking.

## 1. Problem

Recording the initial-build ticket (`R20260925-1a1594`) broke on two real situations:

- **Holding more than the ticket** (MOTILALOFS 179 vs 144): `partial` must be `< qty`, so it could not be saved.
- **A price that is not this week's fill** — an old holding's average cost (PAYTM 1323 vs traded 1624–1708) or a
  late buy. Beyond ±20% it was refused outright; inside ±20% it passed silently (SOLARINDS +15%).
- Skipped lines (ONGC, TMPV) leave capital idle, and nothing summarised how much or where.
- The desk knows nothing about what the manager holds outside the books, so a rebalance judgement (human or AI)
  can't see the whole position.

## 2. Decisions (agreed 2026-10-04)

1. **Statuses stay simple: `done` · `partial` · `skipped` · `extra`.** `extra` = bought more than the ticket
   quantity (BUY lines only). The fund counts the ticket quantity; the surplus is an *other holding* (§4), derived
   from the execution record, so re-saving the line never double-counts.
2. **Price is a warning, not a block.** A fill price outside the stock's traded low–high from the fill date to the
   latest close, or more than 20% from the reference, returns warnings. The manager reviews them and saves anyway
   (`?confirm=true`). Only a missing, zero, negative or non-finite price is refused.
3. **Other holdings** — a register of everything held outside the books (symbol, qty, avg cost, note), edited on
   its own page, readable at `GET /api/fund/other-holdings` so rebalance tooling and AI review can see it.
4. **Undeployed capital** is shown per sleeve on Tracking. The next ticket does **not** auto-buy skipped lines (it is a
   model diff); redirecting that money is the manager's call.

## 3. Execution record

| status | qty | price | fund counts |
|---|---|---|---|
| `done` | ticket qty | required | ticket qty |
| `partial` | 1 … qty−1 | required | qty |
| `extra` | > ticket qty (BUY only) | required | ticket qty; `extra_qty` = qty − ticket qty goes to other holdings |
| `skipped` | 0 | — | 0 |

- Warnings (each `{line_id, symbol, price, ref_px, low, high, reason}`) are computed for the whole request before
  anything is written. Without `confirm=true` the API answers 409 with `{"warnings": [...]}` and writes nothing.
- Records saved with a warning carry `price_warning` so the decision stays visible later.

## 4. Other holdings

- Collection `fund_other_holdings`: one doc per symbol `{symbol, qty, cost_basis?, note?, updated_at}`.
- API: `GET /api/fund/other-holdings` → `{manual: [...], from_tickets: [...], value}` valued at the latest close;
  `PUT /api/fund/other-holdings` `{SYMBOL: {qty, cost_basis?, note?}}`, qty 0 removes. Symbol must exist in
  `symbol_mappings`/`index_meta`. Journal `OTHER_HOLDING_UPDATED`.
- `from_tickets` = surplus of `extra` executions on published tickets (read-only; change it by editing the line).
- Never part of fund NAV and never a tracking gap.

## 5. Tracking additions

Per-sleeve model ₹ / actual ₹ / under-invested ₹ / over-invested ₹, KPI "Capital not deployed" (Σ under-invested),
KPI "Other holdings" value linking to the page. Valued at the latest close, model price as fallback.

## 6. Tests

Backend `tests/fund_desk/test_transfer_in.py`: each status incl. `extra` (BUY only, > qty), warnings block without
confirm and write nothing, confirm saves and marks `price_warning`, traded-range and 20% triggers, other-holdings
validation, tracking summary + derived extras. Frontend: payload builder + warning flow (vitest), `tsc`, browser
check.

⚠ Security-sensitive (input validation on write endpoints) — flag for human review before merge.
