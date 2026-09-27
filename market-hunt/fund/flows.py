"""Deposits and withdrawals after go-live, accounted in fund UNITS (pure functions).

The model's NAV is the blended fund curve (normalised, time-weighted). At go-live the capital buys
units = capital / NAV(go-live signal day). A deposit of D on a run's signal day S buys D / NAV(S) units; a withdrawal
redeems them. Fund rupees = units x NAV, so a cash flow changes the fund's size but never its return.

A flow is applied by the next published run: that run's ticket resizes every sleeve so the new money lands at target
weights (a deposit buys pro-rata to each sleeve's holdings; a withdrawal sells pro-rata). If the same run carries the
monthly reset, the resize targets the reset weights of the post-flow fund value.
"""
from __future__ import annotations

import math


def units_at_go_live(capital: float, nav_go_live: float) -> float:
    return capital / nav_go_live


def total_units(capital: float, nav_go_live: float, applied_units: list[float]) -> float:
    return units_at_go_live(capital, nav_go_live) + sum(applied_units)


def resize_factors(rupees: dict[str, float], weights: dict[str, float], flow: float, reset: bool) -> dict | None:
    """Per-sleeve multiplier on current holdings for this run's flow and/or monthly reset; None when neither applies.
    rupees: sleeve values before the flow; weights: target weights (sum 1); flow: net rupees in (+) or out (-)."""
    if not reset and abs(flow) < 1e-9:
        return None
    total = sum(rupees.values())
    after = total + flow
    if after <= 0:
        raise ValueError(f"withdrawal of ₹{-flow:,.0f} exceeds the fund value ₹{total:,.0f}")
    out = {}
    for k, r in rupees.items():
        target = weights[k] * after if reset else r + weights[k] * flow
        out[k] = target / r if r > 0 else (math.inf if target > 0 else 1.0)
    return out


def validate_flow(amount, fund_value: float) -> str | None:
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount):
        return "amount must be a number"
    if amount == 0:
        return "amount must not be zero"
    if abs(amount) > 1e11:
        return "amount is too large"
    if amount < 0 and -amount >= fund_value:
        return f"a withdrawal must be smaller than the fund value (₹{fund_value:,.0f})"
    return None
