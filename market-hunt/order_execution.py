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
                          security_ids: dict[str, str]) -> tuple[list[dict], list[str]]:
    """Concrete per-order instructions for this slice, plus blocking errors.
    Rows stay SELL-first (plan seq order). Uses the row's limit_price."""
    orders: list[dict] = []
    errors: list[str] = []
    total_value = 0.0
    cap_order = max_order_value()
    for row in sorted(plan.get("actions", []), key=lambda r: r["seq"]):
        qty = slice_qty(row["qty"], int(row.get("punched_qty") or 0), fraction)
        if qty <= 0:
            continue
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
        orders.append({
            "seq": row["seq"], "symbol": row["symbol"], "security_id": str(sec_id),
            "side": row["action"], "qty": qty,
            "order_type": row.get("order_type") or "LIMIT",
            "price": round(px, 2) if (row.get("order_type") or "LIMIT") == "LIMIT" else 0,
            "product": row.get("product") or "CNC",
            "est_value": round(est, 2),
        })
    cap_total = max_execution_value()
    if total_value > cap_total:
        errors.append(f"execution total ₹{total_value:,.0f} exceeds per-run cap "
                      f"₹{cap_total:,.0f} (MH_MAX_EXECUTION_VALUE)")
    return orders, errors
