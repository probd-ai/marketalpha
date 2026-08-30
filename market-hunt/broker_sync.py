"""Broker-account ↔ portfolio sync planning (pure logic, no I/O).

A sync plan is the diff between a portfolio's simulated book and the broker
account's real holdings, expressed as an ordered list of concrete actions.
It is designed to be the INPUT for broker-side execution later, so every row
carries the full lifecycle scaffolding now:

  action row lifecycle:  planned -> approved -> punched -> filled | rejected
  (only "planned" is produced today; later stages mutate the persisted plan)

Capital scaling: sim books grow from sim capital (e.g. 1L -> 3L) while the real
account has its own size, so target values are scaled by settings.capital_mode:
  account : target_total = live holdings value + available cash   (default)
  sim     : use sim values 1:1
  fixed   : target_total = settings.fixed_capital
"""
from __future__ import annotations

DEFAULT_SETTINGS = {
    "capital_mode": "account",      # account | sim | fixed
    "fixed_capital": None,
    "tolerance_percent": 5.0,        # ignore drift within this band of target value
    "min_order_value": 500.0,        # skip actions smaller than this (₹)
    "sell_unknown": False,           # never auto-sell names the sim doesn't know
    "product": "CNC",                # delivery only — never intraday
    "order_type": "LIMIT",           # LIMIT default: microcaps punish market orders
    "limit_band_percent": 1.0,       # limit price = LTP ± band (BUY above, SELL below)
    "max_participation_percent": 10.0,  # cap order at this % of 20-day avg daily value
    "exclude_symbols": [],           # never BUY or SELL these via sync
    "funding_haircut_percent": 1.0,  # assume sells realize this much less when checking cash
}


def normalize_settings(raw: dict | None) -> dict:
    s = {**DEFAULT_SETTINGS, **(raw or {})}
    if s["capital_mode"] not in ("account", "sim", "fixed"):
        s["capital_mode"] = "account"
    if s["capital_mode"] == "fixed" and not s.get("fixed_capital"):
        s["capital_mode"] = "account"
    s["tolerance_percent"] = max(0.0, float(s["tolerance_percent"]))
    s["min_order_value"] = max(0.0, float(s["min_order_value"]))
    s["limit_band_percent"] = max(0.0, float(s.get("limit_band_percent", 1.0)))
    s["max_participation_percent"] = max(0.0, float(s.get("max_participation_percent", 10.0)))
    s["funding_haircut_percent"] = max(0.0, float(s.get("funding_haircut_percent", 1.0)))
    s["exclude_symbols"] = sorted({str(x).strip().upper()
                                   for x in (s.get("exclude_symbols") or []) if str(x).strip()})
    if s.get("order_type") not in ("LIMIT", "MARKET"):
        s["order_type"] = "LIMIT"
    return s


def _apply_liquidity_and_limits(row: dict, s: dict, adv_values: dict,
                                warnings: list) -> None:
    """Cap qty at max_participation_percent of the name's 20-day average daily
    traded value, and attach a LIMIT price (LTP ± band). Mutates the row."""
    px = row["last_price"]
    if s["order_type"] == "LIMIT" and px:
        band = s["limit_band_percent"] / 100.0
        row["order_type"] = "LIMIT"
        row["limit_price"] = round(px * (1 + band) if row["action"] == "BUY"
                                   else px * (1 - band), 2)
    else:
        row["limit_price"] = None
    adv = float(adv_values.get(row["symbol"]) or 0)
    row["adv_value"] = round(adv, 0) if adv else None
    if adv > 0 and s["max_participation_percent"] > 0:
        cap_value = adv * s["max_participation_percent"] / 100.0
        desired_value = row["qty"] * px if px else 0
        row["participation_percent"] = round(desired_value / adv * 100, 1)
        if desired_value > cap_value and px:
            capped_qty = int(cap_value // px)
            import math
            row["suggested_slices"] = math.ceil(row["qty"] / max(capped_qty, 1))
            row["liquidity_capped"] = True
            row["qty"] = capped_qty
            row["est_value"] = round(capped_qty * px, 2)
            warnings.append(
                f"{row['symbol']}: order capped to {s['max_participation_percent']:.0f}% of "
                f"20d ADV (₹{adv:,.0f}/day) — full size needs ~{row['suggested_slices']} "
                f"daily slices")
        else:
            row["liquidity_capped"] = False
            row["suggested_slices"] = 1
    else:
        row["participation_percent"] = None
        row["liquidity_capped"] = False
        row["suggested_slices"] = 1


def build_sync_plan(*, sim_book: list[dict], live_holdings: list[dict],
                    available_cash: float, settings: dict | None,
                    adv_values: dict[str, float] | None = None,
                    fresh_prices: dict[str, float] | None = None) -> dict:
    """sim_book rows: {symbol, quantity, market_value, current_price}
    live_holdings rows: {symbol, quantity, last_price, market_value}
    adv_values: symbol -> 20-day average daily traded value (₹), for
    participation caps in illiquid names.
    Returns {actions, skipped, totals, warnings} — deterministic, side-effect-free."""
    s = normalize_settings(settings)
    adv_values = adv_values or {}
    fresh_prices = fresh_prices or {}
    excluded = set(s["exclude_symbols"])
    live = {h["symbol"]: h for h in live_holdings}
    sim_total = sum(float(b.get("market_value") or 0) for b in sim_book)
    live_total = sum(float(h.get("market_value") or 0) for h in live_holdings)

    if s["capital_mode"] == "fixed":
        target_total = float(s["fixed_capital"])
    elif s["capital_mode"] == "sim":
        target_total = sim_total
    else:
        target_total = live_total + float(available_cash or 0)

    factor = (target_total / sim_total) if sim_total > 0 else 0.0
    sells, buys, skipped, warnings = [], [], [], []

    def price_of(sym: str, sim_row: dict | None) -> float | None:
        # priority: live TradingView quote > broker holdings LTP > last sim close
        if fresh_prices.get(sym):
            return float(fresh_prices[sym])
        h = live.get(sym)
        if h and h.get("last_price"):
            return float(h["last_price"])
        if sim_row and sim_row.get("current_price"):
            return float(sim_row["current_price"])
        return None

    for b in sorted(sim_book, key=lambda x: -float(x.get("market_value") or 0)):
        sym = b["symbol"]
        if sym.upper() in excluded:
            skipped.append({"symbol": sym, "reason": "excluded by mapping settings"})
            continue
        target_value = float(b.get("market_value") or 0) * factor
        h = live.get(sym)
        live_qty = float(h["quantity"]) if h else 0.0
        px = price_of(sym, b)
        if px is None or px <= 0:
            skipped.append({"symbol": sym, "reason": "no price available"})
            continue
        live_value = live_qty * px
        delta_value = target_value - live_value
        band = max(s["min_order_value"], target_value * s["tolerance_percent"] / 100.0)
        if abs(delta_value) <= band:
            if h is None and target_value > 0:
                skipped.append({"symbol": sym, "reason": "target below min_order_value"})
            continue
        qty = int(abs(delta_value) // px)
        if qty <= 0:
            skipped.append({"symbol": sym, "reason": "delta below one share"})
            continue
        row = {
            "action": "BUY" if delta_value > 0 else "SELL",
            "symbol": sym, "exchange": "NSE", "security_id": None,
            "product": s["product"], "order_type": s["order_type"],
            "sim_qty": round(float(b.get("quantity") or 0), 4),
            "live_qty": live_qty,
            "qty": min(qty, int(live_qty)) if delta_value < 0 else qty,
            "last_price": px,
            "est_value": round(min(qty * px, live_value) if delta_value < 0 else qty * px, 2),
            "target_value": round(target_value, 2),
            "live_value": round(live_value, 2),
            "reason": "missing" if live_qty == 0 else "drift",
            "drift_percent": round(delta_value / target_value * 100, 1) if target_value else None,
            "status": "planned",
        }
        if row["action"] == "SELL" and row["qty"] <= 0:
            skipped.append({"symbol": sym, "reason": "nothing held to sell"})
            continue
        _apply_liquidity_and_limits(row, s, adv_values, warnings)
        if row["qty"] <= 0:
            skipped.append({"symbol": sym, "reason": "liquidity cap left no tradeable qty"})
            continue
        (buys if row["action"] == "BUY" else sells).append(row)

    sim_syms = {b["symbol"] for b in sim_book}
    for sym, h in sorted(live.items()):
        if sym in sim_syms:
            continue
        if sym.upper() in excluded:
            skipped.append({"symbol": sym, "reason": "excluded by mapping settings"})
            continue
        px = price_of(sym, None)
        mv = float(h.get("market_value") or 0)
        if not s["sell_unknown"]:
            skipped.append({"symbol": sym,
                            "reason": f"held in broker, not in sim (value ≈ ₹{mv:,.0f}) — sell_unknown is off"})
            continue
        if px is None or mv < s["min_order_value"]:
            skipped.append({"symbol": sym, "reason": "unknown holding below min order value"})
            continue
        row = {
            "action": "SELL", "symbol": sym, "exchange": "NSE", "security_id": None,
            "product": s["product"], "order_type": s["order_type"],
            "sim_qty": 0.0, "live_qty": float(h["quantity"]), "qty": int(h["quantity"]),
            "last_price": px, "est_value": round(mv, 2),
            "target_value": 0.0, "live_value": round(mv, 2),
            "reason": "not_in_sim", "drift_percent": None, "status": "planned",
        }
        _apply_liquidity_and_limits(row, s, adv_values, warnings)
        if row["qty"] > 0:
            sells.append(row)

    # SELLs first: their proceeds fund the BUYs on the same day.
    actions = sells + buys
    for i, a in enumerate(actions, 1):
        a["seq"] = i

    buy_value = sum(a["est_value"] for a in buys)
    sell_value = sum(a["est_value"] for a in sells)
    net_cash_needed = buy_value - sell_value
    # Funding check: cash + haircut sell proceeds must cover the buys, else the
    # plan is explicitly NOT feasible (execution layer must refuse it).
    funding = float(available_cash or 0) + sell_value * (1 - s["funding_haircut_percent"] / 100.0)
    feasible = buy_value <= funding + 0.01
    if not feasible:
        warnings.append(
            f"NOT FUNDED: buys ≈ ₹{buy_value:,.0f} vs funding ≈ ₹{funding:,.0f} "
            f"(cash ₹{float(available_cash or 0):,.0f} + sells ₹{sell_value:,.0f} "
            f"less {s['funding_haircut_percent']}% haircut). Fund the account or "
            f"reduce target capital — do not execute this plan as-is.")
    elif buy_value > float(available_cash or 0):
        warnings.append(
            "buys depend on same-day sell proceeds — confirm the broker credits "
            "sell proceeds for delivery buys the same day, and execute SELLs first")

    return {
        "settings": s,
        "feasible": feasible,
        "fresh_quotes": len(fresh_prices),
        "totals": {
            "sim_value": round(sim_total, 2), "live_value": round(live_total, 2),
            "target_total": round(target_total, 2), "scale_factor": round(factor, 4),
            "available_cash": round(float(available_cash or 0), 2),
            "buy_value": round(buy_value, 2), "sell_value": round(sell_value, 2),
            "net_cash_needed": round(net_cash_needed, 2),
            "funding_available": round(funding, 2),
            "action_count": len(actions),
        },
        "actions": actions,
        "skipped": skipped,
        "warnings": warnings,
    }
