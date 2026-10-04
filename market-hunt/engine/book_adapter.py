"""Book strategies in the simulator: run the FUND DESK engine and answer in the simulator's response shape.

A saved simulator strategy with a `book` block ({"kind": "grid10" | "tranche", "params": {...}}) does not go through
the stateless or wave engines. It is replayed by fund.engine (the same code, market loader and schedule the fund desk
trades), and the replay is mapped onto the simulator's `results[]` / `summary` so the existing results pages and the
portfolio composer read it unchanged. Spec: docs/specs/book-strategies-in-simulator.md.

Execution timing is the fund desk's: signals at the previous close, fills at the session's open.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from fund.config import validate_book
from fund.engine import ENGINE_VERSION
from fund.engine.calendar import trading_days
from fund.engine.costs import Costs
from fund.engine.factory import base_schedule, make_book
from fund.engine.fund import MODEL_START_FLOOR
from fund.engine.market import Market
from fund.engine.replay import ReplayResult, replay_sleeve

EXECUTION = "signals at the previous close, fills at the session's open (fund desk engine)"
RISK_FREE = 0.06            # same Sharpe convention as the stateless engine

# Settings the book engine does not implement: a non-default value is refused rather than silently ignored.
_UNSUPPORTED = {"primary_filter_take": "none", "regime_gate": False, "stock_gate": False, "portfolio_gate": False,
                "stop_loss_percent": 0.0, "side": "long", "rebalance_offset_sessions": 0}
# Settings the book defines itself: accepted, ignored, and reported back when sent with a non-default value.
_BOOK_DEFINED = {"max_holdings": None, "momentum_ranking": "20_day_return", "rebalance_type": "equal_weight",
                 "rebalance_date": "first"}


def book_run_errors(book: dict, run: dict) -> tuple[list[str], list[str]]:
    """(errors, ignored_settings) for running `book` with the simulator settings `run` (a SimulationParams dict)."""
    errors = list(validate_book(book.get("kind"), book.get("params") or {}))
    try:
        start, end = date.fromisoformat(run["start_date"]), date.fromisoformat(run["end_date"])
        if start < MODEL_START_FLOOR:
            errors.append(f"book strategies start on or after {MODEL_START_FLOOR.isoformat()} (validated "
                          f"point-in-time membership); got {start.isoformat()}")
        if end < start:
            errors.append(f"end_date {end.isoformat()} must be after start_date {start.isoformat()}")
    except (KeyError, TypeError, ValueError):
        errors.append("start_date and end_date must be YYYY-MM-DD")
    if run.get("point_in_time_membership", True) is False:
        errors.append("book strategies are always point-in-time; point_in_time_membership=false is not supported")
    if run.get("rebalance_frequency", "weekly") != "weekly":
        errors.append(f"book strategies rebalance weekly; rebalance_frequency={run.get('rebalance_frequency')!r} "
                      "is not supported")
    wd = run.get("book_weekday", 0)
    if isinstance(wd, bool) or not isinstance(wd, int) or not 0 <= wd <= 4:
        errors.append(f"book_weekday must be 0 (Mon) .. 4 (Fri); got {wd!r}")
    for k, default in _UNSUPPORTED.items():
        if k in run and run[k] not in (default, None):
            errors.append(f"{k}={run[k]!r} is not supported by book strategies")
    for k in ("wave", "filter"):
        if run.get(k):
            errors.append(f"a {k} block cannot be combined with a book strategy")
    ignored = sorted(k for k, default in _BOOK_DEFINED.items() if k in run and run[k] not in (default, None))
    return errors, ignored


def simulate_book(m: Market, kind: str, params: dict, costs: Costs, start, end, capital: float, weekday: int = 0,
                  bench: pd.Series | None = None) -> tuple[ReplayResult, dict]:
    """Replay the book on `m` and map it. `bench` defaults to the market's own benchmark closes."""
    cal = trading_days(m.closes[m.bench], start, end)
    res = replay_sleeve(make_book(kind, params), m, costs, start, end, base_schedule(kind, cal, weekday), capital)
    b = (bench if bench is not None else m.closes[m.bench]).reindex(res.equity.index).ffill()
    return res, to_sim_result(res, m, capital, b, {"kind": kind, "params": dict(params), "weekday": weekday})


def to_sim_result(res: ReplayResult, m: Market, capital: float, bench: pd.Series, book: dict) -> dict:
    eq = res.equity
    b0 = float(bench.dropna().iloc[0]) if bench.notna().any() else np.nan
    qty: dict[str, float] = {}
    cost: dict[str, float] = {}          # rupees paid for the shares still held (buys incl. costs, reduced pro rata)
    held_since: dict[str, int] = {}
    cum_costs = 0.0
    rows = []
    reasons: dict[str, int] = {}
    for i, d in enumerate(eq.index):
        new_added, exited, exited_details, trades = [], [], [], []
        buy_c = sell_c = 0.0
        for o in res.orders.get(d, []):
            reason = getattr(o.reason, "value", str(o.reason))
            reasons[reason] = reasons.get(reason, 0) + 1
            if o.side == "BUY":
                c = o.value - o.qty * o.px
                buy_c += c
                if qty.get(o.symbol, 0.0) <= 0:
                    new_added.append(o.symbol)
                    held_since[o.symbol] = i
                qty[o.symbol] = qty.get(o.symbol, 0.0) + o.qty
                cost[o.symbol] = cost.get(o.symbol, 0.0) + o.value
            else:
                c = o.qty * o.px - o.value
                sell_c += c
                before = qty.get(o.symbol, 0.0)
                basis = cost.get(o.symbol, 0.0) * (o.qty / before if before > 0 else 1.0)
                left = before - o.qty
                if left <= before * 1e-12:
                    exited.append(o.symbol)
                    exited_details.append({
                        "symbol": o.symbol, "company_name": o.symbol, "quantity": o.qty,
                        "avg_price": basis / o.qty if o.qty else 0.0, "exit_price": o.px,
                        "pnl": o.value - basis, "pnl_percent": (o.value / basis - 1) * 100 if basis else 0.0,
                        "sector": "Unknown", "exit_reason": reason})
                    qty.pop(o.symbol, None); cost.pop(o.symbol, None); held_since.pop(o.symbol, None)
                else:
                    qty[o.symbol] = left
                    cost[o.symbol] -= basis
            trades.append({"symbol": o.symbol, "action": o.side, "quantity": o.qty, "price": o.px,
                           "value": o.value, "costs": c, "reason": reason})
        cum_costs += buy_c + sell_c
        pv = float(eq.iloc[i])
        holdings = []
        for s in sorted(qty):
            px = float(m.cclose.at[d, s])
            mv = qty[s] * px
            holdings.append({
                "symbol": s, "company_name": s, "quantity": qty[s], "avg_price": cost[s] / qty[s],
                "current_price": px, "market_value": mv, "pnl": mv - cost[s],
                "pnl_percent": (mv / cost[s] - 1) * 100 if cost[s] else 0.0, "sector": "Unknown",
                "weight": mv / pv * 100 if pv else 0.0, "holding_periods": i - held_since.get(s, i),
                "allocation_weight": 1.0, "side": "long"})
        holdings.sort(key=lambda h: h["market_value"], reverse=True)
        bv = capital * float(bench.iloc[i]) / b0 if b0 == b0 and bench.iloc[i] == bench.iloc[i] else capital
        rows.append({
            "date": d.strftime("%Y-%m-%d"), "portfolio_value": pv, "benchmark_value": bv, "holdings": holdings,
            "new_added": new_added, "exited": exited, "exited_details": exited_details,
            "cash": pv - sum(h["market_value"] for h in holdings),
            "total_pnl": pv - capital, "day_pnl": pv - (float(eq.iloc[i - 1]) if i else capital),
            "benchmark_price": float(bench.iloc[i]) if bench.iloc[i] == bench.iloc[i] else 0.0,
            "daily_charges": {"total_charges": buy_c + sell_c, "buy_charges": buy_c, "sell_charges": sell_c},
            "cumulative_charges": cum_costs, "charge_impact_percent": cum_costs / capital * 100,
            "trade_details": trades, "brokerage_enabled": True, "exchange_used": "NSE",
        })
    return {"point_in_time": True, "results": rows, "summary": _summary(eq, rows, capital, len(res.orders), cum_costs),
            "book": {**book, "engine_version": ENGINE_VERSION, "execution": EXECUTION, "order_reasons": reasons}}


def _summary(eq: pd.Series, rows: list, capital: float, trade_days: int, costs: float) -> dict:
    total = (float(eq.iloc[-1]) / capital - 1) * 100
    bench = (rows[-1]["benchmark_value"] / capital - 1) * 100
    r = eq.pct_change().dropna()
    if len(r) and r.std(ddof=0) > 0:
        sharpe = ((1 + r.mean()) ** 252 - 1 - RISK_FREE) / (r.std(ddof=0) * np.sqrt(252))
    else:
        sharpe = 0.0
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    peak = np.maximum.accumulate(np.maximum(eq.to_numpy(), capital))
    return {
        "total_return": round(total, 2), "benchmark_return": round(bench, 2), "alpha": round(total - bench, 2),
        "cagr": round(((float(eq.iloc[-1]) / capital) ** (1 / yrs) - 1) * 100, 2) if yrs > 0 else None,
        "max_drawdown": round(float((eq.to_numpy() / peak - 1).min()) * 100, 2),
        "sharpe_ratio": round(float(sharpe), 3), "total_trades": trade_days,
        "ruined": False, "ruin_date": None, "brokerage_enabled": True, "exchange": "NSE",
        "charge_impact": round(costs / capital * 100, 4), "stop_loss_percent": None, "stop_loss_exits": 0,
        "gated_days": 0, "regime_gate": None, "primary_filter": None,
    }


def run_book_simulation(db, book: dict, universe: str, start: str, end: str, capital: float, slippage_bps: float,
                        include_brokerage: bool = True, weekday: int = 0, benchmark_symbol: str | None = None,
                        today: date | None = None) -> dict:
    """Load the market exactly as the fund desk does and simulate. Membership is anchored at today (the current
    snapshot the reconstitution changelog runs back from), never at `end`."""
    from fund.data import BENCH, build_market, charge_rates, load_prices

    kind, params = book["kind"], dict(book.get("params") or {})
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    m = build_market(db, universe, kind, s, today or date.today())
    br, sr = charge_rates() if include_brokerage else (0.0, 0.0)
    costs = Costs(br, sr, float(slippage_bps) / 1e4)
    bench = None
    bsym = benchmark_symbol or BENCH
    if bsym != BENCH:
        from datetime import datetime
        _, bc = load_prices(db, [bsym], datetime.combine(s, datetime.min.time()))
        if bsym not in bc.columns or bc[bsym].dropna().empty:
            raise ValueError(f"no benchmark price data for {bsym!r}")
        bench = bc[bsym]
    res, sim = simulate_book(m, kind, params, costs, s, e, capital, weekday, bench)
    sim["summary"]["brokerage_enabled"] = include_brokerage
    for r in sim["results"]:
        r["brokerage_enabled"] = include_brokerage
    sim["benchmark_symbol"] = bsym
    sim["book"]["universe"] = universe
    sim["book"]["slippage_bps"] = float(slippage_bps)
    return sim
