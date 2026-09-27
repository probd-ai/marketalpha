"""Common broker types. Normalized shapes so Kite/Dhan are interchangeable."""
from dataclasses import dataclass, asdict


class BrokerError(Exception):
    """Sanitized broker failure — message must never contain credentials."""


@dataclass
class Holding:
    """One position, normalized across brokers.

    `quantity` is the TOTAL economic position and is what sizing runs on.
    The settlement split below is what EXECUTION must respect: only
    `sellable_qty` can actually be sold today. `pending_qty` (T+1, unsettled)
    and `pledged_qty` (collateral) are owned but not deliverable, and a SELL
    sized against the total gets rejected by the broker.

    `isin` is the cross-broker identity. Symbol is a display label — it changes
    on a rename, ISIN does not, so joins key on ISIN.
    """
    symbol: str
    exchange: str
    quantity: float
    avg_price: float
    last_price: float | None
    market_value: float | None
    pnl: float | None
    pnl_percent: float | None
    isin: str = ""
    sellable_qty: float = 0.0
    pending_qty: float = 0.0
    pledged_qty: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class Funds:
    available_cash: float
    margin_used: float | None
    withdrawable: float | None

    def to_dict(self):
        return asdict(self)
