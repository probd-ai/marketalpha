# End-to-end group-chained execution against a mocked broker, plus a guard
# regression sweep. Pure — no Mongo, no network.
#
# The chain under test: sell a group -> fills settle -> the BUY group behind it is
# sized from the cash those sells ACTUALLY realized -> plan completes.
from datetime import datetime, timedelta

import pytest

import broker_sync
import order_execution as oe


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("MH_MAX_ORDER_VALUE", "MH_MAX_EXECUTION_VALUE",
              "MH_TRADING_ENABLED", "MH_MARKET_HOURS_CHECK"):
        monkeypatch.delenv(k, raising=False)


# ---------- plan build -> groups -> chain ------------------------------------

def _sim_book():
    return [{"symbol": "KEEP", "quantity": 10, "market_value": 10000.0,
             "current_price": 1000.0},
            {"symbol": "NEW1", "quantity": 10, "market_value": 5000.0,
             "current_price": 500.0},
            {"symbol": "NEW2", "quantity": 10, "market_value": 5000.0,
             "current_price": 500.0}]


def _live():
    # OLD is held but absent from the sim book; KEEP is already right
    return [{"symbol": "KEEP", "quantity": 10, "last_price": 1000.0,
             "market_value": 10000.0, "sellable_qty": 10, "isin": "INE_KEEP"},
            {"symbol": "OLD", "quantity": 20, "last_price": 500.0,
             "market_value": 10000.0, "sellable_qty": 20, "isin": "INE_OLD"}]


def test_full_chain_sells_then_buys_from_realized_proceeds():
    plan_body = broker_sync.build_sync_plan(
        sim_book=_sim_book(), live_holdings=_live(), available_cash=0.0,
        settings={"sell_unknown": True, "tolerance_percent": 1.0})
    plan = {"status": "approved", "feasible": True,
            "generated_at": (datetime.now() - timedelta(minutes=5)).isoformat(),
            **plan_body}

    # SELLs must be sequenced ahead of BUYs — proceeds fund the buys
    kinds = [a["action"] for a in plan["actions"]]
    assert kinds.index("SELL") < kinds.index("BUY")

    plan["groups"] = oe.build_groups(plan["actions"], group_size=3)
    plan["cursor"] = 0
    assert [g["kind"] for g in plan["groups"]] == ["SELL", "BUY"]

    # 1) punch the sell group
    action, _ = oe.advance_plan(plan, fills={})
    assert action == "PUNCH"
    plan["groups"][0]["state"] = "punched"
    plan["groups"][0]["punched_at"] = datetime.now().isoformat()

    # 2) sells fill completely -> settle, proceeds credited
    sell_rows = plan["groups"][0]["rows"]
    fills = {r["symbol"]: {"status": "COMPLETE", "traded_qty": r["qty"],
                           "avg_price": r["last_price"]} for r in sell_rows}
    action, info = oe.advance_plan(plan, fills=fills)
    assert action == "SETTLE" and plan["cursor"] == 1
    assert plan["realized_cash"] > 0

    # 3) the buy group is now fundable FROM those proceeds (cash started at 0)
    action, _ = oe.advance_plan(plan, fills={})
    assert action == "PUNCH"
    assert plan["groups"][1]["sized_rows"], "buys must be funded by sell proceeds"

    # 4) buys fill -> plan completes
    plan["groups"][1]["state"] = "punched"
    plan["groups"][1]["punched_at"] = datetime.now().isoformat()
    buy_fills = {r["symbol"]: {"status": "COMPLETE", "traded_qty": r["qty"],
                               "avg_price": r["last_price"]}
                 for r in plan["groups"][1]["rows"]}
    action, _ = oe.advance_plan(plan, fills=buy_fills)
    assert action == "SETTLE"
    action, _ = oe.advance_plan(plan, fills={})
    assert action == "COMPLETE"


def test_partial_sell_fill_shrinks_the_buy_group_instead_of_overdrawing():
    plan_body = broker_sync.build_sync_plan(
        sim_book=_sim_book(), live_holdings=_live(), available_cash=0.0,
        settings={"sell_unknown": True, "tolerance_percent": 1.0})
    plan = {"status": "approved", "feasible": True,
            "generated_at": datetime.now().isoformat(), **plan_body}
    plan["groups"] = oe.build_groups(plan["actions"], group_size=3)
    plan["cursor"] = 0
    plan["groups"][0]["state"] = "punched"
    plan["groups"][0]["punched_at"] = datetime.now().isoformat()

    # only HALF the sell quantity actually trades
    sell_rows = plan["groups"][0]["rows"]
    fills = {r["symbol"]: {"status": "COMPLETE", "traded_qty": r["qty"] // 2,
                           "avg_price": r["last_price"]} for r in sell_rows}
    oe.advance_plan(plan, fills=fills)
    realized = plan["realized_cash"]

    action, _ = oe.advance_plan(plan, fills={})
    assert action in ("PUNCH", "STALLED")
    if action == "PUNCH":
        spend = sum(r["qty"] * r["last_price"] for r in plan["groups"][1]["sized_rows"])
        assert spend <= realized + 0.01   # never spends money it did not receive


# ---------- guard regression sweep -------------------------------------------

def _guard_plan():
    return {"status": "approved", "feasible": True,
            "generated_at": datetime.now().isoformat(),
            "actions": [{"seq": 1, "symbol": "AAA", "action": "SELL", "qty": 10,
                         "order_type": "LIMIT", "limit_price": 99.0,
                         "last_price": 100.0, "product": "CNC"}]}


def test_unapproved_plan_still_refused():
    p = _guard_plan() | {"status": "draft"}
    assert any("approve" in e for e in
               oe.validate_execution(p, fraction=1.0, dry_run=True))


def test_unfunded_plan_still_refused():
    p = _guard_plan() | {"feasible": False}
    assert any("NOT FUNDED" in e for e in
               oe.validate_execution(p, fraction=1.0, dry_run=True))


def test_stale_plan_still_refused():
    p = _guard_plan()
    p["generated_at"] = (datetime.now() - timedelta(hours=30)).isoformat()
    assert any("regenerate" in e for e in
               oe.validate_execution(p, fraction=1.0, dry_run=True))


def test_kill_switch_still_refused(monkeypatch):
    monkeypatch.delenv("MH_TRADING_ENABLED", raising=False)
    assert any("kill-switch" in e for e in
               oe.validate_execution(_guard_plan(), fraction=1.0, dry_run=False))


def test_market_hours_still_refused(monkeypatch):
    monkeypatch.setenv("MH_TRADING_ENABLED", "true")
    monkeypatch.setenv("MH_MARKET_HOURS_CHECK", "true")
    sunday = datetime(2026, 9, 6, 11, 0)          # a Sunday, inside session hours
    errs = oe.validate_execution(_guard_plan(), fraction=1.0, dry_run=False,
                                 now=sunday)
    assert any("market hours" in e for e in errs)


def test_per_order_cap_still_refused(monkeypatch):
    monkeypatch.setenv("MH_MAX_ORDER_VALUE", "100")
    _, errors, _ = oe.build_execution_batch(_guard_plan(), fraction=1.0,
                                            security_ids={"AAA": "1"})
    assert any("per-order cap" in e for e in errors)


def test_per_run_cap_still_refused(monkeypatch):
    monkeypatch.setenv("MH_MAX_EXECUTION_VALUE", "100")
    _, errors, _ = oe.build_execution_batch(_guard_plan(), fraction=1.0,
                                            security_ids={"AAA": "1"})
    assert any("per-run cap" in e for e in errors)


def test_unpriceable_row_still_refused():
    p = _guard_plan()
    p["actions"][0]["limit_price"] = 0
    p["actions"][0]["last_price"] = 0
    _, errors, _ = oe.build_execution_batch(p, fraction=1.0, security_ids={"AAA": "1"})
    assert any("price basis" in e for e in errors)


def test_unresolved_identifier_still_refused():
    _, errors, _ = oe.build_execution_batch(_guard_plan(), fraction=1.0,
                                            security_ids={})
    assert errors, "a row with no broker identifier must never be sent"
