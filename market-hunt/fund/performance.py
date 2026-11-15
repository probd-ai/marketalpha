"""Performance analytics for the model fund and each of its books (pure functions — no database).

Everything here works on the model's own replay output: daily equity curves, the blend result and each sleeve's
fills. Returns are in percent; annualised figures use 252 sessions a year and a 0% risk-free rate (the convention the
research used, so Sharpe here is comparable with past backtests).

Invariants (pinned by tests):
- per-symbol P&L of a sleeve (closed + open positions) sums exactly to the sleeve's equity change, because every
  order's `value` already carries its costs;
- sleeve contributions plus the rebalance-cost line sum exactly to the fund's total return.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

TRADING_DAYS = 252
CLOSE_TOL = 1e-3       # a position is closed once under 0.1% of its peak share count remains (fractional model shares;
                       # a 2026 Growth expiry left 0.075% of JINDALSTEL that a later session sold)


def _pct(x: float) -> float:
    return float(x) * 100


def _day(d) -> str:
    return pd.Timestamp(d).date().isoformat()


def _clean(c: pd.Series) -> pd.Series:
    return c.dropna().astype(float)


# ------------------------------------------------------------------------------------------------- series stats
def drawdown(curve: pd.Series) -> pd.Series:
    c = _clean(curve)
    return (c / c.cummax() - 1) * 100


def series_stats(curve: pd.Series) -> dict:
    """Return/risk statistics of one equity curve (any scale)."""
    c = _clean(curve)
    if len(c) < 2:
        raise ValueError("a curve needs at least two sessions")
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    down = math.sqrt(float((r.clip(upper=0) ** 2).mean())) if len(r) else 0.0
    dd = c / c.cummax() - 1
    trough = dd.idxmin()
    peak = c.loc[:trough].idxmax()
    after = c.loc[trough:]
    rec = after[after >= c.loc[peak]]
    m = monthly_returns(c)
    mvals = [x["r"] for x in m]
    return {
        "start": _day(c.index[0]), "end": _day(c.index[-1]), "sessions": int(len(c)), "years": yrs,
        "total_return": _pct(c.iloc[-1] / c.iloc[0] - 1),
        "cagr": _pct((c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1) if yrs >= 1 else None,
        "vol": _pct(sd * math.sqrt(TRADING_DAYS)) if sd > 0 else None,
        "sharpe": float(r.mean() / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else None,
        "sortino": float(r.mean() / down * math.sqrt(TRADING_DAYS)) if down > 0 else None,
        "max_dd": _pct(dd.min()),
        "max_dd_peak": _day(peak), "max_dd_trough": _day(trough),
        "max_dd_recovered": _day(rec.index[0]) if dd.min() < 0 and len(rec) else None,
        "dd_now": _pct(dd.iloc[-1]),
        "best_day": {"date": _day(r.idxmax()), "r": _pct(r.max())} if len(r) else None,
        "worst_day": {"date": _day(r.idxmin()), "r": _pct(r.min())} if len(r) else None,
        "best_month": max(m, key=lambda x: x["r"]) if m else None,
        "worst_month": min(m, key=lambda x: x["r"]) if m else None,
        "months": len(m),
        "pct_months_positive": 100 * sum(v > 0 for v in mvals) / len(mvals) if mvals else None,
    }


def relative(curve: pd.Series, bench: pd.Series, name: str, kind: str) -> dict:
    """The curve against one benchmark over the curve's own dates (benchmark forward-filled onto them)."""
    c = _clean(curve)
    b = bench.reindex(c.index).ffill()
    ok = b.notna()
    c, b = c[ok], b[ok]
    if len(c) < 2:
        return {"name": name, "kind": kind, "total_return": None, "excess": None, "beta": None, "corr": None}
    rc, rb = c.pct_change().dropna(), b.pct_change().dropna()
    var = float(rb.var(ddof=1)) if len(rb) > 1 else 0.0
    tr_c, tr_b = c.iloc[-1] / c.iloc[0] - 1, b.iloc[-1] / b.iloc[0] - 1
    both = rc.std(ddof=1) > 0 and rb.std(ddof=1) > 0 if len(rb) > 1 else False
    return {"name": name, "kind": kind, "total_return": _pct(tr_b), "excess": _pct(tr_c - tr_b),
            "beta": float(rc.cov(rb) / var) if var > 0 else None,
            "corr": float(rc.corr(rb)) if both else None}


def monthly_returns(curve: pd.Series) -> list[dict]:
    """Calendar-month returns; the first month is measured from the curve's first session. A first month that holds
    only that anchor session (the close before model start) has no return of its own and is left out."""
    c = _clean(curve)
    per = c.index.to_period("M")
    ends = c.groupby(per).last()
    prev = ends.shift(1)
    prev.iloc[0] = c.iloc[0]
    if len(ends) > 1 and (per == per[0]).sum() == 1:
        ends, prev = ends.iloc[1:], prev.iloc[1:]
    return [{"m": str(p), "r": _pct(ends[p] / prev[p] - 1)} for p in ends.index]


def rebased(curve: pd.Series, dates: pd.DatetimeIndex, base: float = 100.0) -> list[float | None]:
    s = curve.reindex(dates).ffill()
    first = s.dropna()
    if first.empty:
        return [None] * len(dates)
    s = s / first.iloc[0] * base
    return [None if pd.isna(x) else round(float(x), 4) for x in s]


# ------------------------------------------------------------------------------------------------- fund level
def contribution(curves: pd.DataFrame, sleeve_values: pd.DataFrame) -> tuple[dict, float]:
    """Points of fund return each sleeve supplied over the frames' period: Σ_t (value held in the sleeve after the
    previous session) × (sleeve return today), over the fund's value on the first session. A rebalance cost is what
    the fund lost on a session beyond the sleeves' moves (pre-rebalance total − post-rebalance total), so the
    function works on any slice of a blend. Returns ({sleeve: pts}, rebalance-cost pts (≤ 0))."""
    cols = list(sleeve_values.columns)
    r = curves[cols].pct_change().fillna(0)
    held = sleeve_values.shift(1)
    e0 = float(sleeve_values.iloc[0].sum())
    gain = (held * r).iloc[1:]
    pre = held.sum(axis=1).iloc[1:] + gain.sum(axis=1)
    cost = float((pre - sleeve_values.sum(axis=1).iloc[1:]).sum())
    return {k: _pct(float(gain[k].sum()) / e0) for k in cols}, -_pct(cost / e0)


def ew_universe(m, dates: pd.DatetimeIndex) -> pd.Series:
    """Equal-weight daily-rebalanced basket of the sleeve's point-in-time members (the honest control): each session
    earns the mean return of the stocks that were members that day and had a price the session before."""
    cols = [c for c in m.cclose.columns if c != m.bench]
    px = m.cclose[cols].reindex(dates)
    r = px.pct_change(fill_method=None)
    lvl, out = 1.0, [1.0]
    for i in range(1, len(dates)):
        mem = [s for s in m.members(dates[i].date()) if s in r.columns]
        x = r.iloc[i][mem].dropna() if mem else pd.Series(dtype=float)
        lvl *= 1 + (float(x.mean()) if len(x) else 0.0)
        out.append(lvl)
    return pd.Series(out, index=dates, dtype=float)


# ------------------------------------------------------------------------------------------------- trades
def positions(orders: dict, last_close: dict[str, float]) -> dict:
    """Positions reconstructed from a sleeve's fills over its whole history (raw model rupees; see trade_summary).

    A position opens when a symbol's holding goes from flat to long and closes when it is flat again; trims and
    top-ups in between belong to the same position. Order `value` includes costs (a buy's cash paid, a sell's cash
    received), so P&L is net of costs."""
    book: dict[str, dict] = {}
    closed: list[dict] = []
    buys = sells = 0.0
    n_orders = 0
    # A position is closed once what is left is under CLOSE_TOL of its peak size. The engine's fractional shares
    # leave remnants (an expired tranche can leave 1e-20 shares that a later session sells); a remnant stays attached
    # to the trade it came from — its later sale, or its value at the last close, is booked to that trade — so the
    # per-symbol P&L still reconciles exactly to the sleeve's equity.
    remnant: dict[str, dict] = {}            # symbol -> {"qty", "peak", "trade"} left by the last closed position
    for d in sorted(orders):
        for o in orders[d]:
            n_orders += 1
            p = book.get(o.symbol)
            if o.side == "BUY":
                buys += o.value
                if p is None:
                    rem = remnant.pop(o.symbol, None)
                    p = book[o.symbol] = {"symbol": o.symbol, "opened": pd.Timestamp(d), "qty": 0.0, "peak": 0.0,
                                          "invested": 0.0, "proceeds": 0.0}
                    if rem:              # carried shares: their cost sits in the old trade, their value in this one
                        p["qty"] = rem["qty"]
                p["qty"] += o.qty
                p["peak"] = max(p["peak"], p["qty"])
                p["invested"] += o.value
                continue
            sells += o.value
            if p is None:
                rem = remnant.get(o.symbol)
                if rem is None or o.qty > rem["qty"] + CLOSE_TOL * rem["peak"]:
                    raise ValueError(f"sell of {o.symbol} on {_day(d)} with no open position")
                rem["qty"] = max(rem["qty"] - o.qty, 0.0)
                rem["trade"]["pnl"] += o.value
                continue
            p["qty"] -= o.qty
            p["proceeds"] += o.value
            if p["qty"] <= CLOSE_TOL * p["peak"]:
                t = {"symbol": o.symbol, "opened": _day(p["opened"]), "closed": _day(d),
                     "days": int((pd.Timestamp(d) - p["opened"]).days),
                     "invested": p["invested"], "pnl": p["proceeds"] - p["invested"]}
                closed.append(t)
                remnant[o.symbol] = {"qty": max(p["qty"], 0.0), "peak": p["peak"], "trade": t}
                del book[o.symbol]
    for s, rem in remnant.items():                       # remnants still held: valued at the last close
        if rem["qty"] > 0:
            if s not in last_close:
                raise ValueError(f"no closing price for remnant of {s}")
            rem["trade"]["pnl"] += rem["qty"] * last_close[s]
    for t in closed:
        t["ret"] = _pct(t["pnl"] / t["invested"]) if t["invested"] > 0 else None
    open_ = []
    for s, p in book.items():
        if s not in last_close:
            raise ValueError(f"no closing price for open position {s}")
        value = p["qty"] * last_close[s]
        pnl = p["proceeds"] + value - p["invested"]
        open_.append({"symbol": s, "opened": _day(p["opened"]), "qty": p["qty"], "value": value,
                      "invested": p["invested"], "pnl": pnl,
                      "ret": _pct(pnl / p["invested"]) if p["invested"] > 0 else None})
    return {"orders": n_orders, "buys": buys, "sells": sells, "closed": closed, "open": open_}


def trade_summary(pos: dict, base: float, closed_after: str | None = None) -> dict:
    """Statistics over the closed positions (optionally only those closed after `closed_after`, an ISO date) and
    every open position. Returns are each position's own, since its first buy; P&L points are over `base` (the
    book's value at the start of the period being shown)."""
    closed = [t for t in pos["closed"] if closed_after is None or t["closed"] > closed_after]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    rets = [t["ret"] for t in closed if t["ret"] is not None]
    show = lambda t, keep: {**{k: t[k] for k in keep}, "pnl_pts": _pct(t["pnl"] / base)}
    return {
        "closed_count": len(closed), "open_count": len(pos["open"]),
        "hit_rate": 100 * len(wins) / len(closed) if closed else None,
        "avg_win": float(np.mean([t["ret"] for t in wins])) if wins else None,
        "avg_loss": float(np.mean([t["ret"] for t in losses])) if losses else None,
        "expectancy": float(np.mean(rets)) if rets else None,
        "avg_days": float(np.mean([t["days"] for t in closed])) if closed else None,
        "closed": [show(t, ("symbol", "opened", "closed", "days", "ret"))
                   for t in sorted(closed, key=lambda t: t["closed"], reverse=True)],
        "open": [show(t, ("symbol", "opened", "qty", "value", "ret")) for t in sorted(pos["open"], key=lambda t: -t["value"])],
    }


def symbol_pnl(orders: dict, start, close_start: dict[str, float], close_end: dict[str, float],
               base: float) -> list[dict]:
    """P&L per symbol over (start, end]: value held at the end − value held at `start` + sales − purchases in
    between, in points of `base`. Holdings come from the cumulative fills, so this is a cash identity: the symbols
    sum exactly to the book's change in value over the period (cash itself earns nothing). Sorted best first."""
    start = pd.Timestamp(start)
    q0: dict[str, float] = {}
    q1: dict[str, float] = {}
    flow: dict[str, float] = {}
    for d in sorted(orders):
        inside = pd.Timestamp(d) > start
        for o in orders[d]:
            sign = 1 if o.side == "BUY" else -1
            q1[o.symbol] = q1.get(o.symbol, 0.0) + sign * o.qty
            if inside:
                flow[o.symbol] = flow.get(o.symbol, 0.0) - sign * o.value
            else:
                q0[o.symbol] = q0.get(o.symbol, 0.0) + sign * o.qty
    out = []
    for s in sorted(set(q1)):
        v0 = q0.get(s, 0.0) * close_start[s] if abs(q0.get(s, 0.0)) > 0 else 0.0
        v1 = q1[s] * close_end[s] if abs(q1[s]) > 0 else 0.0
        if s not in flow and v0 == 0.0 and v1 == 0.0:
            continue                                   # not held and not traded in the period
        out.append({"symbol": s, "pts": _pct((v1 - v0 + flow.get(s, 0.0)) / base)})
    return sorted(out, key=lambda x: -x["pts"])


def turnover(orders: dict, curve: pd.Series) -> float | None:
    """One-way turnover over the curve's period: (buys + sells) / 2 traded after its first session, over the average
    book value, in multiples (×). The day the book was first built from cash is never turnover."""
    c = _clean(curve)
    if c.empty or not orders:
        return None
    build = min(pd.Timestamp(d) for d in orders)
    lo, hi = c.index[0], c.index[-1]
    traded = sum(o.value for d, os in orders.items() if lo < pd.Timestamp(d) <= hi and pd.Timestamp(d) != build
                 for o in os)
    return float(traded / 2 / c.mean())


def series_payload(dates: pd.DatetimeIndex, **series: pd.Series) -> dict:
    return {"dates": [_day(d) for d in dates], **{k: rebased(v, dates) for k, v in series.items()}}


def as_of_label(d: date | pd.Timestamp) -> str:
    return _day(d)
