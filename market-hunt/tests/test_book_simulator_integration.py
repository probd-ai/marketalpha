"""Book strategies end to end on live data (integration): the simulator and the portfolio simulator run the fund desk
engine and give the fund desk's answer. Spec: docs/specs/book-strategies-in-simulator.md

  .venv/Scripts/python.exe -m pytest tests/test_book_simulator_integration.py -m integration -v
"""
import asyncio
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.integration
HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "research_grid"
START, END = "2020-10-01", "2026-09-25"


@pytest.fixture(scope="module")
def db():
    from app.db import mongo_conn
    if mongo_conn.db is None:
        mongo_conn.connect()
    return mongo_conn.db


@pytest.fixture()
def saved_books(db):
    ids = {k: f"strategy_test_book_{k}_{int(time.time() * 1000)}" for k in ("grid10", "tranche")}
    db.simulation_strategies.insert_many([
        {"id": ids["grid10"], "name": "test value core", "description": "", "rules": [],
         "book": {"kind": "grid10", "params": {"n": 10}}},
        {"id": ids["tranche"], "name": "test growth", "description": "", "rules": [],
         "book": {"kind": "tranche", "params": {"top": 5, "weeks": 8, "slot_div": 40, "max_w": 0.10}}}])
    yield ids
    db.simulation_strategies.delete_many({"id": {"$in": list(ids.values())}})


@pytest.mark.parametrize("k", range(5))
def test_simulator_grid10_equals_the_golden_value_curve(db, k):
    from engine.book_adapter import run_book_simulation
    sim = run_book_simulation(db, {"kind": "grid10", "params": {"n": 10}}, "NIFTY50", START, END, 1_260_000, 5.0,
                              True, k)
    ref = pd.read_csv(HARNESS / "grid10_weekly.csv", index_col=0, parse_dates=True)[str(k)]
    rows = sim["results"]
    assert rows[0]["date"] == "2020-10-01" and rows[-1]["date"] == END
    assert rows[-1]["portfolio_value"] / rows[0]["portfolio_value"] == pytest.approx(ref.iloc[-1], rel=1e-9)
    for r in rows[::50]:
        assert sum(h["market_value"] for h in r["holdings"]) + r["cash"] == pytest.approx(r["portfolio_value"],
                                                                                           rel=1e-9)


def test_simulator_tranche_equals_the_fund_desk_replay(db):
    from engine.book_adapter import run_book_simulation
    from fund.data import build_market, charge_rates
    from fund.engine.calendar import trading_days
    from fund.engine.costs import Costs
    from fund.engine.factory import base_schedule, make_book
    from fund.engine.replay import replay_sleeve
    params = {"top": 5, "weeks": 8, "slot_div": 40, "max_w": 0.10, "deploy_cash": True}
    sim = run_book_simulation(db, {"kind": "tranche", "params": params}, "NIFTY200", "2023-01-02", "2024-12-31",
                              1_700_000, 5.0, True, 1)
    m = build_market(db, "NIFTY200", "tranche", date(2023, 1, 2), date.today())
    cal = trading_days(m.closes[m.bench], "2023-01-02", "2024-12-31")
    res = replay_sleeve(make_book("tranche", params), m, Costs(*charge_rates(), 0.0005), "2023-01-02", "2024-12-31",
                        base_schedule("tranche", cal, 1), 1_700_000)
    assert [r["portfolio_value"] for r in sim["results"]] == pytest.approx(res.equity.tolist(), rel=1e-12)


def test_run_route_by_saved_strategy_and_refusals(db, saved_books):
    from fastapi import HTTPException

    from app.routers.simulation import SimulationParams, run_simulation
    p = SimulationParams(strategy_id=saved_books["grid10"], universe="NIFTY50", start_date="2024-01-01",
                         end_date="2024-06-28", rebalance_frequency="weekly", portfolio_base_value=1_000_000,
                         slippage_bps=5)
    body = json.loads(asyncio.run(run_simulation(p)).body)
    sim = body["simulation"]
    assert body["success"] and sim["book"]["kind"] == "grid10" and sim["point_in_time"] is True
    assert sim["results"] and "total_return" in sim["summary"] and "sharpe_ratio" in sim["summary"]
    assert "wave_diagnostics" not in sim
    for bad in ({"regime_gate": True}, {"rebalance_frequency": "monthly"}, {"start_date": "2020-01-01"},
                {"point_in_time_membership": False}):
        q = SimulationParams(**{**p.model_dump(exclude_unset=True), **bad})
        with pytest.raises(HTTPException) as e:
            asyncio.run(run_simulation(q))
        assert e.value.status_code == 400


def test_stateless_engine_refuses_a_book_strategy(db, saved_books):
    from engine.stateless import run_strategy_simulation
    strat = db.simulation_strategies.find_one({"id": saved_books["grid10"]})
    with pytest.raises(ValueError, match="book strategy"):
        asyncio.run(run_strategy_simulation(None, strat, [], None))


def test_portfolio_blends_two_book_sleeves(db, saved_books):
    from app.routers.portfolio import _compute_portfolio_run
    portfolio = {"id": "test_book_portfolio", "name": "books", "total_capital": 2_000_000,
                 "point_in_time_membership": True, "sleeves": [
                     {"id": "value", "label": "Value", "strategy_id": saved_books["grid10"], "weight": 60,
                      "universe": "NIFTY50", "slippage_bps": 5, "book_weekday": 0},
                     {"id": "growth", "label": "Growth", "strategy_id": saved_books["tranche"], "weight": 40,
                      "universe": "NIFTY200", "slippage_bps": 5, "book_weekday": 0}]}
    out = asyncio.run(_compute_portfolio_run(portfolio, "2024-01-01", "2024-12-31"))
    assert out["success"]
    assert {s["label"] for s in out["sleeves"]} == {"Value", "Growth"}
    assert out["current_book"], "a blended book of holdings"


def test_portfolio_refuses_gates_and_lag_on_book_sleeves(db, saved_books):
    from fastapi import HTTPException

    from app.routers.portfolio import _compute_portfolio_run
    sleeve = {"id": "value", "label": "Value", "strategy_id": saved_books["grid10"], "weight": 100,
              "universe": "NIFTY50", "regime_gate": True}
    p = {"id": "x", "name": "x", "total_capital": 1_000_000, "point_in_time_membership": True, "sleeves": [sleeve]}
    with pytest.raises(HTTPException) as e:
        asyncio.run(_compute_portfolio_run(p, "2024-01-01", "2024-06-28"))
    assert e.value.status_code == 400 and "regime_gate" in e.value.detail
    sleeve["regime_gate"] = False
    with pytest.raises(HTTPException) as e:
        asyncio.run(_compute_portfolio_run(p, "2024-01-01", "2024-06-28", offset_sessions=1))
    assert "rebalance_offset_sessions" in e.value.detail
