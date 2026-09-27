"""Execution costs, identical to the research harness (H6): slippage on the fill price, statutory charges on value.

Rates come from BrokerageCalculator OUTSIDE the engine (fund.data.charge_rates); the engine only receives numbers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Costs:
    buy_rate: float
    sell_rate: float
    slip: float

    def sell_proceeds(self, qty: float, px: float) -> float:
        return qty * px * (1 - self.slip) * (1 - self.sell_rate)

    def buy_qty(self, value: float, px: float) -> float:
        """Shares a buy of `value` rupees (charges included) gets at fill price `px`."""
        return value / (px * (1 + self.slip) * (1 + self.buy_rate))

    def buy_cost(self, qty: float, px: float) -> float:
        return qty * px * (1 + self.slip) * (1 + self.buy_rate)
