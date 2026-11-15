"""Core value types of the book engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Reason(str, Enum):
    """Why an order exists — shown on every ticket line (spec §6)."""
    GRID_DOWN_POSITIVE_MONTH = "GRID_DOWN_POSITIVE_MONTH"
    NEW_LOWEST_22D = "NEW_LOWEST_22D"
    TOPUP_TO_TARGET = "TOPUP_TO_TARGET"
    REMAINDER_SPREAD = "REMAINDER_SPREAD"
    TOP5_NEW_TRANCHE = "TOP5_NEW_TRANCHE"
    TRANCHE_ROLL = "TRANCHE_ROLL"
    TRANCHE_EXPIRED = "TRANCHE_EXPIRED"
    TRIM_ABOVE_CAP = "TRIM_ABOVE_CAP"
    TRIM_TO_FUND = "TRIM_TO_FUND"
    CASH_DEPLOY = "CASH_DEPLOY"
    INDEX_EXIT = "INDEX_EXIT"
    MONTHLY_RESET = "MONTHLY_RESET"
    INITIAL_BUILD = "INITIAL_BUILD"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"


@dataclass(frozen=True)
class Lot:
    """A block of shares bought together. `age` counts rebalances held (tranche clock)."""
    symbol: str
    qty: float
    opened: date
    age: int = 0


@dataclass(frozen=True)
class Order:
    """A fill the engine made at `px` (the fill price before slippage). `value` = rupees moved incl. costs."""
    symbol: str
    side: str            # "BUY" | "SELL"
    qty: float
    px: float
    value: float
    reason: Reason


@dataclass
class SleeveState:
    cash: float
    lots: dict[str, list[Lot]] = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    def qty(self, symbol: str) -> float:
        return sum(l.qty for l in self.lots.get(symbol, []))

    def value(self, prices: dict[str, float]) -> float:
        return self.cash + sum(self.qty(s) * prices[s] for s in self.lots)

    def to_dict(self) -> dict:
        return {"cash": self.cash, "params": dict(self.params),
                "lots": {s: [{"qty": l.qty, "opened": l.opened.isoformat(), "age": l.age} for l in ls]
                         for s, ls in self.lots.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "SleeveState":
        return cls(cash=d["cash"], params=dict(d.get("params", {})),
                   lots={s: [Lot(s, x["qty"], date.fromisoformat(x["opened"]), x["age"]) for x in ls]
                         for s, ls in d["lots"].items()})
