"""Common broker types. Normalized shapes so Kite/Dhan are interchangeable."""
from dataclasses import dataclass, asdict


class BrokerError(Exception):
    """Sanitized broker failure — message must never contain credentials."""


@dataclass
class Holding:
    symbol: str
    exchange: str
    quantity: float
    avg_price: float
    last_price: float | None
    market_value: float | None
    pnl: float | None
    pnl_percent: float | None

    def to_dict(self):
        return asdict(self)


@dataclass
class Funds:
    available_cash: float
    margin_used: float | None
    withdrawable: float | None

    def to_dict(self):
        return asdict(self)
