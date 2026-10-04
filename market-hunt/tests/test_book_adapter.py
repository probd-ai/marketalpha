"""Book strategies in the simulator: the fund desk engine's replay mapped onto the simulator response.

Spec: docs/specs/book-strategies-in-simulator.md
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fund_desk"))
from helpers import BENCH, make_market  # noqa: E402

from engine.book_adapter import book_run_errors, simulate_book, to_sim_result  # noqa: E402
from fund.engine.costs import Costs  # noqa: E402

SYMS = [f"S{i:02d}" for i in range(6)]
COSTS = Costs(0.001, 0.002, 0.0005)


def _grid_market(days=30):
    """S00 is bought (lowest roc, grid up), then turns grid-down with a positive month on day 10 and is sold."""
    idx = pd.bdate_range("2024-01-01", periods=days)
    rng = np.random.default_rng(1)
    closes = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (days, len(SYMS))), axis=0)),
                          index=idx, columns=SYMS)
    grid = pd.DataFrame(1.0, index=idx, columns=SYMS)
    roc = pd.DataFrame([[-0.05, -0.04, -0.03, 0.01, 0.02, 0.03]] * days, index=idx, columns=SYMS)
    grid.loc[idx[10]:, "S00"] = -1.0
    roc.loc[idx[10]:, "S00"] = 0.04
    m = make_market(SYMS, days, closes=closes, grid=grid, roc=roc)
    m.closes[BENCH] = np.linspace(100, 110, days)
    return m, idx


def _run(kind="grid10", params=None, weekday=0, capital=100_000.0):
    m, idx = _grid_market()
    return m, idx, simulate_book(m, kind, params if params is not None else {"n": 3}, COSTS, idx[1], idx[-1],
                                 capital, weekday)


def test_portfolio_value_is_the_engine_equity_and_holdings_reconcile():
    m, idx, (res, sim) = _run()
    rows = sim["results"]
    assert [r["date"] for r in rows] == [d.strftime("%Y-%m-%d") for d in res.equity.index]
    np.testing.assert_allclose([r["portfolio_value"] for r in rows], res.equity.to_numpy(), rtol=1e-12)
    for r in rows:
        assert sum(h["market_value"] for h in r["holdings"]) + r["cash"] == pytest.approx(r["portfolio_value"],
                                                                                           rel=1e-9)
        assert sum(h["weight"] for h in r["holdings"]) <= 100.0 + 1e-6


def test_summary_and_benchmark():
    m, idx, (res, sim) = _run()
    s = sim["summary"]
    assert s["total_return"] == pytest.approx((res.equity.iloc[-1] / 100_000 - 1) * 100, abs=0.01)
    bench = m.closes[BENCH].loc[idx[1]:]                 # rebased to the first simulated session
    assert sim["results"][-1]["benchmark_value"] == pytest.approx(100_000 * bench.iloc[-1] / bench.iloc[0])
    assert s["benchmark_return"] == pytest.approx((bench.iloc[-1] / bench.iloc[0] - 1) * 100, abs=0.01)
    assert s["alpha"] == pytest.approx(s["total_return"] - s["benchmark_return"], abs=0.02)
    eq = res.equity
    peak = eq.clip(lower=100_000).cummax()               # stateless convention: the peak starts at the capital
    assert s["max_drawdown"] == pytest.approx(((eq / peak).min() - 1) * 100, abs=0.01)
    assert s["total_trades"] == len(res.orders)
    assert sim["point_in_time"] is True
    assert sim["book"]["kind"] == "grid10" and sim["book"]["params"] == {"n": 3}
    assert sim["book"]["execution"].startswith("signals at the previous close")


def test_exit_carries_realised_pnl_and_reason():
    m, idx, (res, sim) = _run()
    exits = [(r["date"], e) for r in sim["results"] for e in r["exited_details"]]
    s00 = [e for _, e in exits if e["symbol"] == "S00"]
    assert s00 and s00[0]["exit_reason"] == "GRID_DOWN_POSITIVE_MONTH"
    buys = [o for os in res.orders.values() for o in os if o.symbol == "S00" and o.side == "BUY"]
    sell = next(o for os in res.orders.values() for o in os if o.symbol == "S00" and o.side == "SELL")
    assert s00[0]["pnl"] == pytest.approx(sell.value - sum(o.value for o in buys), rel=1e-9)
    day = next(r for r in sim["results"] if any(e["symbol"] == "S00" for e in r["exited_details"]))
    assert "S00" in day["exited"] and all(h["symbol"] != "S00" for h in day["holdings"])


def test_trade_details_and_costs():
    m, idx, (res, sim) = _run()
    first = sim["results"][0]
    assert first["new_added"] and sorted(first["new_added"]) == ["S00", "S01", "S02"]
    td = first["trade_details"]
    assert {t["action"] for t in td} == {"BUY"} and all(t["reason"] == "NEW_LOWEST_22D" for t in td)
    # buy cost = value paid - shares x fill price (slippage + statutory charges)
    for t in td:
        assert t["costs"] == pytest.approx(t["value"] - t["quantity"] * t["price"], rel=1e-9)
        assert t["costs"] > 0
    assert first["daily_charges"]["total_charges"] == pytest.approx(sum(t["costs"] for t in td))
    assert sim["results"][-1]["cumulative_charges"] >= first["daily_charges"]["total_charges"]


def test_tranche_book_runs_and_reconciles():
    idx = pd.bdate_range("2024-01-01", periods=30)
    rng = np.random.default_rng(3)
    closes = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (30, len(SYMS))), axis=0)),
                          index=idx, columns=SYMS)
    roc = pd.DataFrame(rng.normal(0, 0.05, (30, len(SYMS))), index=idx, columns=SYMS)
    elig = pd.DataFrame(True, index=idx, columns=SYMS)
    m = make_market(SYMS, 30, closes=closes, roc=roc, elig=elig)
    res, sim = simulate_book(m, "tranche", {"top": 2, "weeks": 2, "slot_div": 4, "max_w": 0.5}, COSTS, idx[1],
                             idx[-1], 50_000.0, 2)
    np.testing.assert_allclose([r["portfolio_value"] for r in sim["results"]], res.equity.to_numpy(), rtol=1e-12)
    for r in sim["results"]:
        assert sum(h["market_value"] for h in r["holdings"]) + r["cash"] == pytest.approx(r["portfolio_value"],
                                                                                           rel=1e-9)
    assert sim["book"]["weekday"] == 2


# ---------------------------------------------------------------------------------------------------- guards
BASE = dict(start_date="2021-01-04", end_date="2021-06-30", point_in_time_membership=True,
            rebalance_frequency="weekly")


def test_guard_accepts_a_plain_book_run():
    errs, ignored = book_run_errors({"kind": "grid10", "params": {}}, BASE)
    assert errs == [] and ignored == []


@pytest.mark.parametrize("override,msg", [
    ({"start_date": "2020-09-30"}, "2020-10-01"),
    ({"point_in_time_membership": False}, "point-in-time"),
    ({"rebalance_frequency": "monthly"}, "weekly"),
    ({"regime_gate": True}, "regime_gate"),
    ({"stock_gate": True}, "stock_gate"),
    ({"portfolio_gate": True}, "portfolio_gate"),
    ({"stop_loss_percent": 10.0}, "stop_loss_percent"),
    ({"side": "short"}, "side"),
    ({"primary_filter_take": "top50"}, "primary_filter_take"),
    ({"rebalance_offset_sessions": 1}, "rebalance_offset_sessions"),
    ({"book_weekday": 5}, "book_weekday"),
    ({"end_date": "2020-12-31", "start_date": "2021-01-04"}, "after"),
])
def test_guard_refuses_unsupported_settings(override, msg):
    errs, _ = book_run_errors({"kind": "grid10", "params": {}}, {**BASE, **override})
    assert errs and any(msg in e for e in errs), errs


def test_guard_rejects_bad_book_and_reports_ignored_settings():
    errs, _ = book_run_errors({"kind": "grid10", "params": {"n": 0}}, BASE)
    assert any("n must be between" in e for e in errs)
    errs, ignored = book_run_errors({"kind": "tranche", "params": {}},
                                    {**BASE, "max_holdings": 20, "momentum_ranking": "price_roc_66d"})
    assert errs == [] and set(ignored) == {"max_holdings", "momentum_ranking"}
