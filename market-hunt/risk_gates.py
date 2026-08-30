"""Dynamic-Fib risk gates (port of the probabilityDesk Pine 'Dynamic Fib').

State per bar: ON when close >= LL + (HH - LL) * level, where HH/LL are the
rolling highest-high / lowest-low over `lookback` bars (Pine: trendlookback,
factor fixed at 1.0 so both ranges share the lookback). OFF below the level.

Three applications (engine wiring in api_server):
  index     : gate the whole book on a base index (replaces the flat SMA)
  stock     : per-name eligibility — a stock must be above its own fib level
  portfolio : self-gate on the equity curve (values serve as high=low=close)

All functions are pure and O(n) (monotonic deques), warmup bars default ON.
"""
from __future__ import annotations

from collections import deque

MIN_BARS = 30  # below this much history the gate stays ON (no signal yet)


def rolling_fib_gate(highs: list[float], lows: list[float], closes: list[float],
                     lookback: int = 222, level: float = 0.66,
                     min_bars: int = MIN_BARS) -> list[bool]:
    """Per-bar ON/OFF states. Window = trailing `lookback` bars incl. current."""
    n = len(closes)
    out = [True] * n
    maxq: deque = deque()  # (idx, val) decreasing
    minq: deque = deque()  # (idx, val) increasing
    for i in range(n):
        h, l = highs[i], lows[i]
        while maxq and maxq[-1][1] <= h:
            maxq.pop()
        maxq.append((i, h))
        while minq and minq[-1][1] >= l:
            minq.pop()
        minq.append((i, l))
        lo_bound = i - lookback + 1
        while maxq[0][0] < lo_bound:
            maxq.popleft()
        while minq[0][0] < lo_bound:
            minq.popleft()
        if i + 1 < min_bars:
            continue  # warmup: ON
        hh, ll = maxq[0][1], minq[0][1]
        out[i] = closes[i] >= ll + (hh - ll) * level
    return out


def gate_map_fib(dates: list[str], highs: list[float], lows: list[float],
                 closes: list[float], lookback: int = 222,
                 level: float = 0.66) -> dict[str, bool]:
    """{date_str: ON} for the index gate."""
    states = rolling_fib_gate(highs, lows, closes, lookback, level)
    return dict(zip(dates, states))


def fib_state_from_values(values: list[float], lookback: int = 222,
                          level: float = 0.66, min_bars: int = MIN_BARS) -> bool:
    """Single current ON/OFF from a value series (portfolio equity curve —
    values act as high, low and close). O(lookback)."""
    if len(values) < min_bars:
        return True
    window = values[-lookback:]
    hh, ll = max(window), min(window)
    return values[-1] >= ll + (hh - ll) * level
