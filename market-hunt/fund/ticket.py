"""Turn model orders into a trade ticket: whole shares for the fund's real capital (spec §6). Pure functions.

Model orders are in the sleeve's stand-alone units. Multiplying by the sleeve's SCALE (fund rupees in the sleeve /
the sleeve's stand-alone value) gives real shares. Weekly orders and the monthly-reset resize are netted per
(sleeve, symbol) so the manager never buys and sells the same stock in one ticket.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .engine.types import Order, Reason

REASON_TEXT = {
    Reason.GRID_DOWN_POSITIVE_MONTH: "Grid turned down while the 22-day return is positive ({roc22:+.1f}%) — take the bounce",
    Reason.NEW_LOWEST_22D: "New entry: among the lowest 22-day returns ({roc22:+.1f}%) with the grid up",
    Reason.TOPUP_TO_TARGET: "Top-up toward the equal share (sale money fills gaps)",
    Reason.REMAINDER_SPREAD: "Leftover money spread equally",
    Reason.TOP5_NEW_TRANCHE: "Top-5 by 22-day return ({roc22:+.1f}%), Alpha-eligible — new 2.5% tranche",
    Reason.TRANCHE_ROLL: "Picked again — expiring tranche rolled into a new one",
    Reason.TRANCHE_EXPIRED: "Tranche reached its 8-week clock",
    Reason.TRIM_ABOVE_CAP: "Holding above the 10% cap — trimmed back",
    Reason.CASH_DEPLOY: "Leftover cash deployed up to the 10% cap",
    Reason.INDEX_EXIT: "Left its index{exit_note} — forced sale",
    Reason.MONTHLY_RESET: "Fund rebalance — sleeves restored to their target weights",
    Reason.INITIAL_BUILD: "Initial build — model holding bought to start the fund",
    Reason.DEPOSIT: "New money added — spread across the sleeve at its target weight",
    Reason.WITHDRAWAL: "Money withdrawn — sold pro-rata across the sleeve",
}


@dataclass
class TicketLine:
    sleeve: str
    symbol: str
    side: str
    qty: int
    ref_px: float
    value: float
    limit_px: float
    reasons: list = field(default_factory=list)
    reason_text: str = ""
    full_exit: bool = False
    line_id: str = ""

    def to_dict(self):
        d = asdict(self)
        d["reasons"] = [str(r.value if isinstance(r, Reason) else r) for r in self.reasons]
        return d


def reason_text(reasons: list, ctx: dict) -> str:
    out = []
    for r in reasons:
        t = REASON_TEXT.get(r, str(r))
        try:
            note = f" on {ctx['exit_date']} (model sold it then; sell at the next open)" if ctx.get("exit_date") else ""
            out.append(t.format(**{"roc22": float("nan"), "exit_note": note, **ctx}))
        except (KeyError, ValueError):
            out.append(t.split(" (")[0])
    return " · ".join(dict.fromkeys(out))


def orders_since(orders_by_day: dict, after) -> list[tuple]:
    """(session, Order) for every model order on a session strictly after `after` (the last published fill day),
    oldest first. Between run days the engine still acts — index exits happen on whatever session the stock leaves —
    and those trades must reach the next ticket. None (nothing published yet) -> []."""
    if after is None:
        return []
    return [(d, o) for d in sorted(orders_by_day) if d > after for o in orders_by_day[d]]


def held_before_ticket(held_now: dict, missed: list[tuple]) -> dict:
    """Model holdings as of the last ticket: today's holdings with the in-between trades undone. A stock the model
    already sold mid-week is still in the manager's book, so the ticket must treat its sale as a full exit."""
    out = dict(held_now)
    for _, o in missed:
        out[o.symbol] = out.get(o.symbol, 0.0) + (o.qty if o.side == "SELL" else -o.qty)
    return {s: q for s, q in out.items() if q > 1e-12}


def build_ticket(sleeves: dict, ref_px: dict, band: float = 0.003) -> tuple[list[TicketLine], list[dict]]:
    """sleeves: {name: {"orders": [Order], "scale": float, "held_before": {sym: model_qty},
                        "held_after": {sym: model_qty}, "reset_factor": float | None, "ctx": {sym: {...}}}}
    reset_factor: sleeve rupees after the monthly reset and/or cash flow / before (None when neither applies);
    resize_reasons: reasons shown on those resize lines (default MONTHLY_RESET).
    Returns (lines, residuals) — residuals are nets smaller than one share."""
    lines, residual = [], []
    for name, sl in sleeves.items():
        scale = sl["scale"]
        net: dict[str, float] = {}
        why: dict[str, list] = {}
        for o in sl["orders"]:
            q = o.qty * scale * (1 if o.side == "BUY" else -1)
            net[o.symbol] = net.get(o.symbol, 0.0) + q
            why.setdefault(o.symbol, []).append(o.reason)
        rf = sl.get("reset_factor")
        if rf is not None and abs(rf - 1) > 1e-12:
            for sym, q in sl["held_after"].items():
                net[sym] = net.get(sym, 0.0) + q * scale * (rf - 1)
                why.setdefault(sym, []).extend(sl.get("resize_reasons") or [Reason.MONTHLY_RESET])
        for sym in sorted(net):
            shares = net[sym]
            px = float(ref_px[sym])
            full_exit = sym in sl["held_before"] and sl["held_after"].get(sym, 0.0) <= 1e-12
            if full_exit:
                qty = int(round(sl["held_before"][sym] * scale))
            else:
                qty = int(math.floor(abs(shares) + 1e-9))
            if qty == 0:
                residual.append({"sleeve": name, "symbol": sym, "model_shares": shares})
                continue
            side = "BUY" if shares > 0 else "SELL"
            limit = px * (1 + band) if side == "BUY" else px * (1 - band)
            rs = list(dict.fromkeys(why[sym]))
            lines.append(TicketLine(name, sym, side, qty, px, qty * px, round(limit, 2), rs,
                                    reason_text(rs, sl.get("ctx", {}).get(sym, {})), full_exit))
    lines.sort(key=lambda l: (l.side != "SELL", l.sleeve, l.symbol))
    for i, l in enumerate(lines, 1):
        l.line_id = f"L{i:03d}"
    return lines, residual
