"""Performance service on real market data (integration): the performance replay must land on exactly the books a
run computes, and its accounting must reconcile in every period it shows."""
from datetime import date

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def env():
    from pymongo import MongoClient

    from fund.store import FundStore
    cli = MongoClient("mongodb://localhost:27017/")
    cli.drop_database("fund_desk_perf_test")
    store = FundStore(cli["fund_desk_perf_test"])
    yield cli["market_hunt"], store
    cli.drop_database("fund_desk_perf_test")


def _reconciles(period):
    f = period["fund"]
    assert sum(c["pts"] for c in f["contribution"]) + f["reset_cost_pts"] == pytest.approx(
        f["stats"]["total_return"], abs=1e-6), f"{period['id']}: contributions do not add up to the fund"
    assert all(r > period["start"] for r in f["resets"])
    for b in period["books"]:
        assert b["stats"]["start"] == period["start"]
        if b["trades"] is None:
            assert b["kind"] == "asset"
            continue
        assert sum(x["pts"] for x in b["trades"]["pnl_by_symbol"]) == pytest.approx(
            b["stats"]["total_return"], abs=1e-6), f"{period['id']} {b['label']}: stock P&L does not add up"
        assert {r["kind"] for r in b["relative"]} == {"index", "ew"}
        pts = [x["pts"] for x in b["trades"]["pnl_by_symbol"]]
        assert pts == sorted(pts, reverse=True)


def test_performance_matches_the_run_and_reconciles(env):
    from fund import service
    db, store = env
    snap = service.compute_run(db, store)
    out = service.performance(db, store)
    assert out["as_of"] == snap["signal_date"]
    assert [p["id"] for p in out["periods"]] == ["all", "ytd"]
    full = out["periods"][0]
    assert full["start"] < out["model_start"], "curves are anchored on the close before model start"
    run_books = {b["id"]: b for b in snap["books"]}
    for b in full["books"]:
        if b["trades"] is None:
            continue
        perf = {p["symbol"]: p["weight"] for p in b["trades"]["open"]}
        model = {h["symbol"]: h["weight"] for h in run_books[b["id"]]["holdings"]}
        assert set(perf) == set(model), f"{b['label']}: performance replay holds different stocks than the run"
        for s in model:
            assert perf[s] == pytest.approx(model[s], abs=1e-9)
    for p in out["periods"]:
        _reconciles(p)
    assert service.performance(db, store) is out, "second call within the TTL is served from the cache"


def test_a_period_starting_mid_history_reconciles(env):
    from fund import service
    db, store = env
    out = service.performance(db, store, extra_start=date(2026, 6, 30))
    custom = out["periods"][-1]
    assert custom["id"] == "custom" and custom["start"] == "2026-06-30" and not custom["same_as_all"]
    _reconciles(custom)
    full = out["periods"][0]["books"]
    for b_all, b_mid in zip(full, custom["books"]):
        if b_mid["trades"]:
            # only positions closed after the period start are counted; open positions are the same ones
            assert b_mid["trades"]["closed_count"] <= b_all["trades"]["closed_count"]
            assert {p["symbol"] for p in b_mid["trades"]["open"]} == {p["symbol"] for p in b_all["trades"]["open"]}
