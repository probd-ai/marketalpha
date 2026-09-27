"""Trading calendar and rebalance schedules (H3, H4).

Two weekday conventions exist in the validated research and both are kept:
  week_session(cal, k)                   — k-th session INSIDE each ISO week, clamped to the week's last session
                                           (weekly_tranche.weekly_days; growth / satellite books).
  period_start_shifted(cal, "weekly", k) — the week's FIRST session, then k sessions later on the calendar, which can
                                           spill into the next week (rotation_roc "weekly+k"; Grid-10 value core).
"""
from __future__ import annotations

import pandas as pd


def trading_days(bench_closes: pd.Series, start, end=None) -> pd.DatetimeIndex:
    idx = bench_closes.index
    cal = idx[(idx >= pd.Timestamp(start)) & bench_closes.notna().to_numpy()]
    if end is not None:
        cal = cal[cal <= pd.Timestamp(end)]
    return cal


def week_session(cal: pd.DatetimeIndex, k: int = 0) -> set:
    s = pd.Series(cal, index=cal)
    return {g.iloc[min(k, len(g) - 1)] for _, g in s.groupby(cal.to_period("W"))}


def period_start_shifted(cal: pd.DatetimeIndex, period: str, k: int = 0) -> set:
    key = cal.to_period({"weekly": "W", "monthly": "M", "quarterly": "Q"}[period])
    base = sorted(pd.Series(cal, index=cal).groupby(key).first().values)
    if k == 0:
        return set(pd.DatetimeIndex(base))
    pos = {d: j for j, d in enumerate(cal)}
    return {cal[pos[d] + k] for d in pd.DatetimeIndex(base) if pos[d] + k < len(cal)}


def month_starts(cal: pd.DatetimeIndex) -> set:
    """First session of each calendar month (the fund layer's reset day)."""
    return period_start_shifted(cal, "monthly", 0)


def prev_session(cal: pd.DatetimeIndex, all_price_dates: pd.DatetimeIndex, i: int) -> pd.Timestamp:
    """H4: the signal day — the previous calendar session; on day 0, the last price date before the start."""
    return cal[i - 1] if i > 0 else all_price_dates[all_price_dates < cal[0]][-1]
