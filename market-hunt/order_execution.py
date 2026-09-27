"""Stage-2 execution logic — pure functions, fully testable without a broker.

Safety model (every layer must pass):
  1. kill switch   : env MH_TRADING_ENABLED=true required for any real order
  2. approval      : only plans a human explicitly approved can execute
  3. freshness     : approved plans expire (default 24h) — regenerate daily
  4. caps          : per-order and per-execution value ceilings from env
  5. market hours  : NSE 09:15–15:30 IST, weekdays (skipped for dry runs)
  6. dry-run first : execute defaults to dry_run=true; real send is opt-in
  7. audit         : every attempt (incl. dry runs) recorded in execution_log

Slicing implements the validated stagger policy (~1/N of the ORIGINAL quantity
per day, capped by what remains; SELLs always before BUYs — plan rows are
already sequenced that way).
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta

DEFAULT_MAX_ORDER_VALUE = 200_000.0       # ₹ per single order
DEFAULT_MAX_EXECUTION_VALUE = 2_000_000.0  # ₹ per execute call
PLAN_MAX_AGE_HOURS = 24


def trading_enabled() -> bool:
    return os.environ.get("MH_TRADING_ENABLED", "").strip().lower() == "true"


def max_order_value() -> float:
    try:
        return float(os.environ.get("MH_MAX_ORDER_VALUE", DEFAULT_MAX_ORDER_VALUE))
    except ValueError:
        return DEFAULT_MAX_ORDER_VALUE


def max_execution_value() -> float:
    try:
        return float(os.environ.get("MH_MAX_EXECUTION_VALUE", DEFAULT_MAX_EXECUTION_VALUE))
    except ValueError:
        return DEFAULT_MAX_EXECUTION_VALUE


def market_hours_check_enabled() -> bool:
    """MH_MARKET_HOURS_CHECK=false disables the hours guard — TESTING ONLY
    (e.g. exchange-holiday dry runs against the broker). Default: enabled."""
    return os.environ.get("MH_MARKET_HOURS_CHECK", "true").strip().lower() != "false"


def in_market_hours(now: datetime | None = None) -> bool:
    """NSE regular session, Asia/Kolkata. Naive `now` is assumed IST."""
    try:
        from zoneinfo import ZoneInfo
        now = now or datetime.now(ZoneInfo("Asia/Kolkata"))
    except Exception:
        now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= hm <= (15 * 60 + 30)


def slice_qty(original_qty: int, punched_qty: int, fraction: float) -> int:
    """Today's slice: ceil(original × fraction), capped by the remainder."""
    remaining = max(int(original_qty) - int(punched_qty), 0)
    if remaining <= 0 or fraction <= 0:
        return 0
    return min(math.ceil(int(original_qty) * min(fraction, 1.0)), remaining)


def plan_age_hours(generated_at: str, now: datetime | None = None) -> float:
    now = now or datetime.now()
    gen = datetime.fromisoformat(generated_at)
    return (now - gen).total_seconds() / 3600.0


def validate_execution(plan: dict, *, fraction: float, dry_run: bool,
                       now: datetime | None = None) -> list[str]:
    """All blocking problems with executing this plan now. Empty == go."""
    errors: list[str] = []
    if not 0 < fraction <= 1:
        errors.append(f"fraction must be in (0, 1], got {fraction}")
    if plan.get("status") not in ("approved", "executing"):
        errors.append(f"plan status is '{plan.get('status')}' — approve it first")
    if plan.get("feasible") is False:
        errors.append("plan is NOT FUNDED — refusing to execute")
    try:
        age = plan_age_hours(plan["generated_at"], now)
        if age > PLAN_MAX_AGE_HOURS:
            errors.append(f"plan is {age:.0f}h old (max {PLAN_MAX_AGE_HOURS}h) — regenerate it")
    except Exception:
        errors.append("plan has no valid generated_at timestamp")
    if not dry_run:
        if not trading_enabled():
            errors.append("trading kill-switch is OFF (set MH_TRADING_ENABLED=true in .env)")
        if market_hours_check_enabled() and not in_market_hours(now if (now and now.tzinfo) else None):
            errors.append("outside NSE market hours (09:15–15:30 IST, Mon–Fri) — "
                          "set MH_MARKET_HOURS_CHECK=false to test off-hours")
    return errors


def build_execution_batch(plan: dict, *, fraction: float,
                          security_ids: dict[str, str],
                          ) -> tuple[list[dict], list[str], list[str]]:
    """Concrete per-order instructions for this slice: (orders, errors, warnings).

    `errors` are BLOCKING — the caller refuses the whole execution. `warnings`
    are non-blocking notices that must still reach the audit and the response,
    so a shrunken or dropped row is never silent.

    Rows stay SELL-first (plan seq order). Uses the row's limit_price.
    """
    orders: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    total_value = 0.0
    cap_order = max_order_value()
    for row in sorted(plan.get("actions", []), key=lambda r: r["seq"]):
        qty = slice_qty(row["qty"], int(row.get("punched_qty") or 0), fraction)
        if qty <= 0:
            continue
        # Settlement clamp. Only `sellable_qty` is deliverable today; T+1 and
        # pledged stock is owned but not sellable, and the broker rejects a SELL
        # sized against the total. Clamp the SLICE (not the plan total, which
        # later days still need) and warn — do NOT block, or one name sitting in
        # T+1 would refuse the entire rebalance. Rows from plans persisted before
        # this field existed carry no sellable_qty and are left alone.
        clamped_from = None
        if row["action"] == "SELL" and row.get("sellable_qty") is not None:
            sellable = max(int(row["sellable_qty"]), 0)
            if qty > sellable:
                clamped_from = qty
                qty = sellable
                if qty <= 0:
                    warnings.append(
                        f"{row['symbol']}: nothing sellable today — all "
                        f"{clamped_from} in the slice is unsettled (T+1) or pledged; "
                        f"row skipped, carries to the next session")
                    continue
                warnings.append(
                    f"{row['symbol']}: SELL slice clamped {clamped_from} -> {qty} "
                    f"(only {sellable} settled and unpledged today)")
        sec_id = security_ids.get(row["symbol"])
        if not sec_id:
            errors.append(f"{row['symbol']}: no Dhan securityId resolved")
            continue
        px = row.get("limit_price") or row.get("last_price") or 0
        if px <= 0:
            # No price basis -> est would be 0 and silently bypass the value
            # caps. Refuse the row instead of sending an unpriceable order.
            errors.append(f"{row['symbol']}: no positive price basis "
                          f"(limit_price/last_price missing) — refusing row")
            continue
        est = qty * (row.get("last_price") or px)
        if est > cap_order:
            errors.append(f"{row['symbol']}: slice value ₹{est:,.0f} exceeds per-order cap "
                          f"₹{cap_order:,.0f} (MH_MAX_ORDER_VALUE)")
            continue
        total_value += est
        order = {
            "seq": row["seq"], "symbol": row["symbol"], "security_id": str(sec_id),
            "side": row["action"], "qty": qty,
            "order_type": row.get("order_type") or "LIMIT",
            "price": round(px, 2) if (row.get("order_type") or "LIMIT") == "LIMIT" else 0,
            "product": row.get("product") or "CNC",
            "est_value": round(est, 2),
        }
        if clamped_from is not None:
            order["clamped_from"] = clamped_from
        orders.append(order)
    cap_total = max_execution_value()
    if total_value > cap_total:
        errors.append(f"execution total ₹{total_value:,.0f} exceeds per-run cap "
                      f"₹{cap_total:,.0f} (MH_MAX_EXECUTION_VALUE)")
    return orders, errors, warnings


# ---------------------------------------------------------------------------
# Group-chained execution
#
# Sell a group, wait for those fills, then size the next BUY group from the cash
# they actually realized. Modelled as a state machine advanced ONE step per
# /execute call rather than a loop that polls fills inside a request: a blocking
# loop cannot survive a crash or a second caller, and the 10-minute plan lock
# assumes short calls. Every transition below is persisted by the caller.
#
#   group states:  pending -> punched -> settled
#   returns:       PUNCH | WAIT | SETTLE | STALLED | COMPLETE
# ---------------------------------------------------------------------------

DEFAULT_GROUP_SIZE = 3
GROUP_FILL_TIMEOUT_SEC = 900          # 15 min; tune from live fill latency
_TERMINAL = {"COMPLETE", "REJECTED", "CANCELLED", "FILLED"}


def build_groups(actions: list[dict], group_size: int = DEFAULT_GROUP_SIZE) -> list[dict]:
    """Chunk plan rows into single-sided groups, SELLs first.

    Single-sided is load-bearing: a group's proceeds fund the NEXT group, so a
    mixed group would spend money it has not yet received.
    """
    size = max(int(group_size or DEFAULT_GROUP_SIZE), 1)
    groups: list[dict] = []
    for kind in ("SELL", "BUY"):
        rows = [r for r in sorted(actions, key=lambda r: r["seq"]) if r["action"] == kind]
        for i in range(0, len(rows), size):
            groups.append({"seq": len(groups), "kind": kind,
                           "rows": rows[i:i + size], "state": "pending",
                           "punched_at": None, "realized_value": 0.0, "retries": 0})
    return groups


def buy_budget(chain: dict) -> float:
    """Cash available to a BUY group right now, for ONE chain.

    `chain` is a per-account holder on a routed plan, or the plan itself on a
    legacy single-account plan. Both brokers credit sell proceeds immediately
    (confirmed 2026-09-06), so realized_cash counts in full — but only the cash
    belonging to THIS account, which is the whole point of the split.
    """
    cash = float(chain.get("starting_cash")
                 if chain.get("starting_cash") is not None
                 else (chain.get("totals") or {}).get("available_cash") or 0)
    return max(cash + float(chain.get("realized_cash") or 0)
               - float(chain.get("already_spent") or 0), 0.0)


def size_buy_group(rows: list[dict], budget: float) -> tuple[list[dict], list[dict]]:
    """Trim a BUY group to what `budget` actually affords.

    Rows are filled in order; the first row that does not fit is partially sized
    if it can be, and anything left over is DEFERRED to a later session — never
    dropped, or the book would quietly stop converging.
    """
    sized: list[dict] = []
    deferred: list[dict] = []
    left = max(float(budget), 0.0)
    for r in rows:
        px = float(r.get("last_price") or r.get("limit_price") or 0)
        if px <= 0:
            deferred.append(r)
            continue
        want = int(r["qty"])
        afford = int(left // px)
        take = min(want, afford)
        if take <= 0:
            deferred.append(r)
            continue
        sized.append({**r, "qty": take})
        left -= take * px
        if take < want:
            deferred.append({**r, "qty": want - take})
    return sized, deferred


def _group_fills_terminal(group: dict, fills: dict) -> bool:
    for r in group["rows"]:
        f = fills.get(r["symbol"])
        if not f or str(f.get("status", "")).upper() not in _TERMINAL:
            return False
    return True


def _chain(plan: dict, account: str | None) -> dict:
    """The state holder for one chain.

    Multi-account plans keep a chain per account under `by_account`, because cash
    does not move between broker accounts. Legacy single-account plans keep
    groups/cursor/realized_cash at the top level; returning the plan itself makes
    both shapes work through one code path, so in-flight plans never break.
    """
    if account is not None:
        return (plan.setdefault("by_account", {})
                    .setdefault(account, {"groups": [], "cursor": 0,
                                          "realized_cash": 0.0,
                                          "already_spent": 0.0,
                                          "starting_cash": 0.0}))
    return plan


def plan_complete(plan: dict) -> bool:
    """True only when EVERY account's chain has settled. One account finishing is
    not the plan finishing."""
    accounts = plan.get("accounts") or []
    if not accounts:
        return int(plan.get("cursor") or 0) >= len(plan.get("groups") or [])
    return all(int(_chain(plan, a).get("cursor") or 0)
               >= len(_chain(plan, a).get("groups") or []) for a in accounts)


def next_account_with_work(plan: dict) -> str | None:
    """Next account (alphabetical, stable) whose chain still has groups pending."""
    for a in plan.get("accounts") or []:
        c = _chain(plan, a)
        if int(c.get("cursor") or 0) < len(c.get("groups") or []):
            return a
    return None


def advance_plan(plan: dict, *, fills: dict, account: str | None = None,
                 now: datetime | None = None,
                 timeout_sec: int = GROUP_FILL_TIMEOUT_SEC) -> tuple[str, dict]:
    """Advance ONE chain exactly one step. Pure apart from mutating `plan`.

    `account` selects the chain on a routed plan; omit it for a legacy
    single-account plan. `fills` maps symbol -> {status, traded_qty, avg_price}.
    Returns (action, info); the caller performs any I/O the action implies.
    """
    now = now or datetime.now()
    ch = _chain(plan, account)
    groups = ch.get("groups") or []
    cursor = int(ch.get("cursor") or 0)
    if cursor >= len(groups):
        return "COMPLETE", {"reason": "all groups settled"}

    g = groups[cursor]

    if g["state"] == "punched":
        punched_at = g.get("punched_at")
        terminal = _group_fills_terminal(g, fills)
        if not terminal:
            age = 0.0
            if punched_at:
                age = (now - datetime.fromisoformat(punched_at)).total_seconds()
            if age <= timeout_sec:
                return "WAIT", {"reason": "orders still open", "group": cursor}
            # Timed out with an unfilled remainder -> reprice ladder territory.
            return "REPRICE", {"reason": "fill timeout", "group": cursor}
        realized = 0.0
        for r in g["rows"]:
            f = fills.get(r["symbol"]) or {}
            qty = float(f.get("traded_qty") or 0)
            px = float(f.get("avg_price") or r.get("last_price") or 0)
            if g["kind"] == "SELL":
                realized += qty * px
            else:
                ch["already_spent"] = float(ch.get("already_spent") or 0) + qty * px
        g["realized_value"] = realized
        g["state"] = "settled"
        if g["kind"] == "SELL":
            ch["realized_cash"] = float(ch.get("realized_cash") or 0) + realized
        ch["cursor"] = cursor + 1
        return "SETTLE", {"group": cursor, "realized": realized}

    # pending
    if g["kind"] == "BUY":
        budget = buy_budget(ch)
        sized, deferred = size_buy_group(g["rows"], budget)
        if not sized:
            return "STALLED", {
                "group": cursor,
                "reason": (f"no budget to fund this BUY group "
                           f"(available {budget:.0f}) — sells realized "
                           f"{float(ch.get('realized_cash') or 0):.0f}"),
                "deferred": [r["symbol"] for r in deferred]}
        g["sized_rows"] = sized
        g["deferred_rows"] = deferred
    return "PUNCH", {"group": cursor, "kind": g["kind"]}


# ---------------------------------------------------------------------------
# Reprice ladder
#
# The limit band defaults to +-0.1%, which on low-ADV names sits inside the
# spread. Unfilled SELLs starve the BUY group behind them, so recovering fills
# keeps the chain moving. Two independent ceilings bound the chase: how many
# times we widen, and how far from the original anchor we will ever go.
# ---------------------------------------------------------------------------

DEFAULT_BAND_LADDER = [0.1, 0.3, 0.6]
DEFAULT_MAX_RETRIES = 2
# Must be >= the ladder's top rung, or that rung is unreachable dead code. The
# ceiling is still an independent guard: lower it per mapping to stop the chase
# earlier than the retry count would.
DEFAULT_MAX_SLIPPAGE_PERCENT = 0.6


def reprice_band(retries: int, *, ladder: list[float] | None = None,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 max_slippage_percent: float = DEFAULT_MAX_SLIPPAGE_PERCENT
                 ) -> float | None:
    """Band to use on attempt `retries`, or None when the chase must stop.

    Stops at whichever ceiling binds first — retry count or cumulative slippage.
    """
    lad = ladder or DEFAULT_BAND_LADDER
    if retries > max_retries or retries >= len(lad):
        return None
    band = lad[retries]
    if band > max_slippage_percent:
        return None
    return band


def limit_price_for(anchor: float, side: str, band_percent: float) -> float:
    """A wider band always moves AGAINST us: a BUY bids up, a SELL offers down."""
    b = float(band_percent) / 100.0
    px = anchor * (1 + b) if side == "BUY" else anchor * (1 - b)
    return round(px, 2)


def reprice_decision(row: dict, *, ladder: list[float] | None = None,
                     max_retries: int = DEFAULT_MAX_RETRIES,
                     max_slippage_percent: float = DEFAULT_MAX_SLIPPAGE_PERCENT
                     ) -> dict:
    """What to do with a row whose orders timed out unfilled.

    CARRY means the chase is exhausted — the row moves to the next session and is
    REPORTED. It is never silently abandoned; a book that quietly stops
    converging is the failure mode this guards against.
    """
    retries = int(row.get("retries") or 0)
    band = reprice_band(retries, ladder=ladder, max_retries=max_retries,
                        max_slippage_percent=max_slippage_percent)
    if band is None:
        return {"action": "CARRY", "symbol": row.get("symbol"),
                "reason": (f"{row.get('symbol')}: reprice ladder exhausted after "
                           f"{retries} attempts — unfilled quantity carries to the "
                           f"next session")}
    anchor = float(row.get("last_price") or row.get("limit_price") or 0)
    return {"action": "REPRICE", "symbol": row.get("symbol"), "band": band,
            "limit_price": limit_price_for(anchor, row.get("action", "BUY"), band)}


def safe_to_replace(*, order_id: str, book: list[dict]) -> bool:
    """True only when the original order is CONFIRMED gone from the live book.

    Cancel-then-repunch is the one path in this design that can produce a
    duplicate live order. An order that is still OPEN or PARTIAL would be
    duplicated by a replacement; an order ABSENT from the book is ambiguous
    (it may simply not have registered yet), so absence is treated as unsafe
    rather than as proof of cancellation.
    """
    for o in book:
        if o.get("order_id") == order_id:
            return str(o.get("status", "")).upper() in ("CANCELLED", "REJECTED")
    return False
