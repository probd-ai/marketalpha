"""Double-punch guard: if order placement crashes mid-loop with an unexpected
exception, everything already sent MUST be persisted (audit + punched_qty) and
the plan lock released — otherwise the next execute re-punches live orders."""
import asyncio
from datetime import datetime

import pytest

pytestmark = pytest.mark.integration


class _FakeClient:
    """First order succeeds, second dies with a non-BrokerError (network-style)."""
    def __init__(self):
        self.calls = 0

    def place_order(self, **kw):
        self.calls += 1
        if self.calls == 1:
            return {"order_id": "OID-1", "order_status": "TRANSIT"}
        raise RuntimeError("socket exploded mid-run")


@pytest.fixture()
def crash_plan(monkeypatch):
    from app.db import mongo_conn
    if mongo_conn.db is None:
        mongo_conn.connect()
    db = mongo_conn.db
    plan_id = "testplan_crash"
    db.sync_plans.delete_many({"plan_id": plan_id})
    db.execution_log.delete_many({"plan_id": plan_id})
    db.sync_plans.insert_one({
        "plan_id": plan_id, "mapping_id": "m1", "broker": "dhan",
        "account": "default", "status": "approved", "feasible": True,
        "generated_at": datetime.now().isoformat(),
        "actions": [
            {"seq": 1, "symbol": "AAA", "action": "SELL", "qty": 10,
             "order_type": "LIMIT", "limit_price": 99.0, "last_price": 100.0,
             "product": "CNC", "punched_qty": 0},
            {"seq": 2, "symbol": "BBB", "action": "BUY", "qty": 5,
             "order_type": "LIMIT", "limit_price": 202.0, "last_price": 200.0,
             "product": "CNC", "punched_qty": 0},
        ],
    })
    monkeypatch.setenv("MH_TRADING_ENABLED", "true")
    monkeypatch.setenv("MH_MARKET_HOURS_CHECK", "false")
    monkeypatch.setenv("MH_API_TOKEN", "test-exec-key")
    import app.routers.broker as br
    fake = _FakeClient()
    monkeypatch.setattr(br, "_broker", lambda name, account=None: fake)
    monkeypatch.setattr(br, "_resolve_instruments",
                        lambda rows, broker: ({"AAA": "111", "BBB": "222"}, []))
    yield plan_id
    # TestClient's lifespan closes the shared client on exit — reconnect to clean up
    if mongo_conn.db is None:
        mongo_conn.connect()
    mongo_conn.db.sync_plans.delete_many({"plan_id": plan_id})
    mongo_conn.db.execution_log.delete_many({"plan_id": plan_id})


def test_crash_mid_loop_persists_punched_state_and_unlocks(crash_plan):
    plan_id = crash_plan
    from fastapi.testclient import TestClient
    import api_server
    from app.db import mongo_conn
    with TestClient(api_server.app) as client:
        r = client.post(f"/api/sync-plan/{plan_id}/execute",
                        params={"fraction": 1.0, "dry_run": "false"},
                        headers={"X-API-Key": "test-exec-key"})
        # must NOT be an unhandled 500; structured partial-failure response
        assert r.status_code != 500, r.text
        body = r.json()
        assert body.get("aborted") is True
        assert body.get("punched") == 1 and body.get("failed") == 1

        db = mongo_conn.db  # lifespan-connected; read state while it is open
        # audit written despite the crash — with the successful order recorded
        log = db.execution_log.find_one({"plan_id": plan_id})
        assert log is not None, "audit record missing after crash"
        oks = [x for x in log["results"] if x.get("order_id")]
        assert len(oks) == 1 and oks[0]["symbol"] == "AAA"

        # punched_qty persisted for the filled row — the double-punch guard itself
        plan = db.sync_plans.find_one({"plan_id": plan_id})
        row_a = next(x for x in plan["actions"] if x["symbol"] == "AAA")
        row_b = next(x for x in plan["actions"] if x["symbol"] == "BBB")
        assert row_a["punched_qty"] == 10
        assert row_b["punched_qty"] == 0

        # lock released so a follow-up (fixed-connectivity) run can proceed
        assert not plan.get("executing_lock")
