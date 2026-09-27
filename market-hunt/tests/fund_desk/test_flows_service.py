"""Cash-flow lifecycle end to end on real market data, throw-away database (integration)."""
import pytest

pytestmark = pytest.mark.integration


def test_deposit_lifecycle():
    from pymongo import MongoClient

    from fund import service
    from fund.store import FundStore
    cli = MongoClient("mongodb://localhost:27017/")
    cli.drop_database("fund_desk_test3")
    store, db = FundStore(cli["fund_desk_test3"]), cli["market_hunt"]
    try:
        with pytest.raises(service.RunRefused, match="not live yet"):
            service.add_cashflow(store, 500_000)
        s1 = service.compute_run(db, store)
        # go live as if last week, so this week's run is allowed (drift is expected from the move: acknowledge it)
        store.db.fund_runs.update_one({"_id": s1["_id"]}, {"$set": {
            "status": "PUBLISHED", "signal_date": "2026-09-18", "fill_date": "2026-09-21", "iso_week": "2026-W39",
            "publish_key": "1|2026-W39"}})
        with pytest.raises(service.RunRefused, match="smaller than the fund value"):
            service.add_cashflow(store, -10_000_000)
        cf = service.add_cashflow(store, 500_000, "bonus")
        s2 = service.compute_run(db, store)
        store.db.fund_config.update_one({"_id": "main"}, {"$set": {"drift_ack": {"hash": s2["drift_hash"]}}})
        s3 = service.compute_run(db, store)
        f = s3["fund"]
        assert f["value"] == pytest.approx(f["value_before_flows"] + 500_000)
        assert f["net_invested"] == pytest.approx(f["capital"] + 500_000)
        chk = next(c for c in s3["checks"] if c["name"] == "Cash flow")
        assert chk["status"] == "PASS" and s3["cash_flows"][0]["id"] == cf["_id"]
        dep = [l for l in s3["ticket"] if "DEPOSIT" in l["reasons"]]
        assert dep and all(l["side"] == "BUY" or "DEPOSIT" not in l["reasons"] or len(l["reasons"]) > 1 for l in dep)
        # a flow recorded after the run was computed must force a recompute
        late = service.add_cashflow(store, 100_000)
        with pytest.raises(service.RunRefused, match="cash flows changed"):
            service.publish(db, store, s3["_id"])
        service.cancel_cashflow(store, late["_id"])
        if s3["verdict"] == "BLOCK":
            pytest.skip(f"other checks block publishing: {[c['name'] for c in s3['checks'] if c['status'] == 'BLOCK']}")
        service.publish(db, store, s3["_id"])
        got = store.db.fund_cashflows.find_one({"_id": cf["_id"]})
        assert got["status"] == "APPLIED" and got["units"] == pytest.approx(500_000 / s3["nav_signal"])
        with pytest.raises(service.RunRefused, match="pending"):
            service.cancel_cashflow(store, cf["_id"])
    finally:
        cli.drop_database("fund_desk_test3")
