"""Streak watch for Grid-10 sleeves — the manual overlay on the value core (research 2026-10-04,
scripts/research_grid/grid10_streak_exit.py: Grid-10 + "sell when mean_long drops below its own 222-session SMA,
never buy below it" = 10.3–10.6%/yr vs 9.5% plain, 2008 → 2026).

For each Grid-10 sleeve: every stock ACTUALLY held (ticket executions + manual trades) with its grid state, 22-day
return and mean_long vs its 222-session SMA — below it means EXIT — and Grid-10's next picks (grid UP, lowest 22-day
return first) with the ones below their SMA marked ineligible. Read-only; the model is not changed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

SMA = 222


def streak_state(ml: pd.Series, window: int = SMA) -> dict:
    """mean_long vs its own `window`-session SMA on the last day, and how many sessions in a row it has been on that
    side. `above` is None when there is not enough history."""
    ml = ml.dropna()
    sma = ml.rolling(window).mean()
    live = sma.dropna()
    if live.empty:
        return {"above": None, "days": 0, "mean_long": float(ml.iloc[-1]) if len(ml) else None, "sma": None}
    side = (ml.loc[live.index] > live)
    now = bool(side.iloc[-1])
    days = 0
    for v in side.iloc[::-1]:
        if bool(v) != now:
            break
        days += 1
    return {"above": now, "days": days, "mean_long": float(ml.iloc[-1]), "sma": float(live.iloc[-1])}


def next_picks(members, held, grid: dict, roc22: dict, above: dict, n: int = 8) -> list[dict]:
    """Grid-10's buy order among members not held: grid UP, lowest 22-day return first; `eligible` is False for a
    stock below its SMA (the overlay never buys it)."""
    pool = [s for s in members if s not in held and grid.get(s) == 1 and roc22.get(s) is not None
            and np.isfinite(roc22[s])]
    pool.sort(key=lambda s: (roc22[s], s))
    return [{"symbol": s, "roc22": float(roc22[s]), "eligible": bool(above.get(s))} for s in pool[:n]]


def streak_watch(db, store, today: date | None = None) -> dict:
    from membership import load_membership, members_as_of

    from .data import BENCH, load_prices, load_truevx
    from .engine.signals import grid_state, roc
    from .service import actual_positions

    today = today or date.today()
    cfg = store.get_config()
    pos = actual_positions(store)
    since_ml = datetime.combine(today - timedelta(days=1500), datetime.min.time())   # long streaks count in full
    since_px = datetime.combine(today - timedelta(days=90), datetime.min.time())
    out, as_of = [], None
    for sl in [s for s in cfg["sleeves"] if s["kind"] == "grid10"]:
        iv = load_membership(db, sl["universe"], today)
        members = sorted(members_as_of(iv, today))
        held = sorted(sym for (lab, sym), q in pos.items() if lab == sl["label"] and q > 0)
        syms = sorted(set(members) | set(held))
        ml = load_truevx(db, syms, since_ml, ("mean_long",))["mean_long"]
        _, closes = load_prices(db, syms, since_px)
        stocks = [c for c in closes.columns if c != BENCH]
        last = closes[BENCH].dropna().index[-1]
        as_of = str(last.date())
        r22 = roc(closes[stocks]).loc[last]
        grid = grid_state(ml).ffill().iloc[-1] if not ml.empty else pd.Series(dtype=float)
        states = {s: streak_state(ml[s]) if s in ml.columns else {"above": None, "days": 0, "mean_long": None, "sma": None}
                  for s in syms}
        holdings = []
        for s in held:
            st = states[s]
            holdings.append({"symbol": s, "qty": int(pos[(sl["label"], s)]), **st,
                             "grid": int(grid[s]) if s in grid.index and pd.notna(grid[s]) else None,
                             "roc22": float(r22[s]) if s in r22.index and pd.notna(r22[s]) else None,
                             "in_index": s in members, "exit": st["above"] is False})
        picks = next_picks(members, set(held), {s: int(grid[s]) for s in grid.index if pd.notna(grid[s])},
                           {s: float(r22[s]) for s in r22.index if pd.notna(r22[s])},
                           {s: states[s]["above"] for s in syms})
        out.append({"sleeve": sl["label"], "universe": sl["universe"], "holdings": holdings, "picks": picks})
    return {"as_of": as_of, "sma": SMA, "sleeves": out}
