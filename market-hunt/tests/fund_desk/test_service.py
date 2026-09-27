"""Service end to end on real market data, writing to a throw-away database (integration)."""
from datetime import date

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def env():
    from pymongo import MongoClient

    from fund.store import FundStore
    cli = MongoClient("mongodb://localhost:27017/")
    cli.drop_database("fund_desk_test")
    store = FundStore(cli["fund_desk_test"])
    yield cli["market_hunt"], store
    cli.drop_database("fund_desk_test")


def test_compute_publish_execute_and_drift(env):
    from fund import service
    db, store = env
    snap = service.compute_run(db, store)
    assert snap["status"] == "DRAFT" and snap["ticket"], "first run buys the model books"
    assert {c["name"] for c in snap["checks"]} >= {"Data freshness", "Drift", "Liquidity"}
    # model value on the signal day is the fund's rupee value; sleeves sum to it
    assert sum(b["value"] for b in snap["books"]) == pytest.approx(snap["fund"]["value"], rel=1e-9)
    # before anything is published the fund is worth exactly its capital, and the build ticket spends ~all of it
    assert snap["fund"]["value"] == pytest.approx(snap["fund"]["capital"], rel=1e-9)
    buys = sum(l["value"] for l in snap["ticket"] if l["side"] == "BUY")
    assert 0.97 * snap["fund"]["capital"] <= buys <= snap["fund"]["capital"]
    sells_first = [l["side"] for l in snap["ticket"]]
    assert sells_first == sorted(sells_first, key=lambda s: s != "SELL")
    if snap["verdict"] == "BLOCK":
        pytest.skip(f"checks block publishing today: {[c for c in snap['checks'] if c['status'] == 'BLOCK']}")
    pub = service.publish(db, store, snap["_id"])
    assert pub["status"] == "PUBLISHED"
    with pytest.raises(service.RunRefused, match="already published"):
        service.compute_run(db, store)
    first = snap["ticket"][0]
    out = service.record_executions(store, snap["_id"], [{"line_id": first["line_id"], "status": "done",
                                                          "price": first["ref_px"]}])
    assert out["status"] in ("EXECUTING", "CLOSED")
    act = service.actual_book(store)
    row = next(r for r in act["rows"] if r["symbol"] == first["symbol"] and r["sleeve"] == first["sleeve"])
    assert row["actual_qty"] == (first["qty"] if first["side"] == "BUY" else -first["qty"])


def test_drift_guard_blocks_then_ack_downgrades():
    from pymongo import MongoClient

    from fund import service
    from fund.store import FundStore
    cli = MongoClient("mongodb://localhost:27017/")
    cli.drop_database("fund_desk_test2")
    store, db = FundStore(cli["fund_desk_test2"]), cli["market_hunt"]
    try:
        snap = service.compute_run(db, store)
        # pretend this run was published a week earlier: its checkpoint (state on 09-25) now sits at an older
        # signal date, so the replay cannot reproduce it -> drift
        store.db.fund_runs.update_one({"_id": snap["_id"]}, {"$set": {
            "status": "PUBLISHED", "signal_date": "2026-09-18", "fill_date": "2026-09-21", "iso_week": "2026-W39"}})
        s2 = service.compute_run(db, store)
        d = next(c for c in s2["checks"] if c["name"] == "Drift")
        assert d["status"] == "BLOCK" and s2["verdict"] == "BLOCK" and s2["drift_hash"]
        with pytest.raises(service.RunRefused, match="Drift"):
            service.publish(db, store, s2["_id"])
        store.db.fund_config.update_one({"_id": "main"}, {"$set": {"drift_ack": {"hash": s2["drift_hash"]}}})
        s3 = service.compute_run(db, store)
        assert next(c for c in s3["checks"] if c["name"] == "Drift")["status"] == "WARN"
    finally:
        cli.drop_database("fund_desk_test2")
