"""Engine factory and rebalance schedules — the one place a sleeve kind becomes an engine.

Shared by the fund desk (fund.service) and the simulator's book strategies (engine.book_adapter), so both always run
the same rules on the same days.
"""
from __future__ import annotations

import pandas as pd

from .asset import Asset
from .calendar import period_start_shifted, week_session
from .grid10 import Grid10
from .tranche import Tranche


def make_book(kind: str, params: dict | None, symbol: str | None = None):
    p = params or {}
    if kind == "grid10":
        return Grid10(int(p.get("n", 10)))
    if kind == "tranche":
        return Tranche(int(p.get("top", 5)), int(p.get("weeks", 8)), int(p.get("slot_div", 40)),
                       float(p.get("max_w", 0.10)), bool(p.get("deploy_cash", True)),
                       float(p["trim_at"]) if p.get("trim_at") is not None else None,
                       bool(p.get("full_slot", False)))
    if kind == "asset":
        return Asset(symbol)
    raise ValueError(f"unknown sleeve kind {kind!r}")


def base_schedule(kind: str, cal: pd.DatetimeIndex, weekday: int = 0) -> set:
    """Rebalance sessions before any published run: Grid-10 = the week's first session shifted `weekday` sessions
    (rotation_roc weekly+k); tranche = the `weekday`-th session inside each week (weekly_tranche). Assets never trade
    on their own."""
    if kind == "asset":
        return set()
    if kind == "grid10":
        return period_start_shifted(cal, "weekly", weekday)
    return week_session(cal, weekday)
