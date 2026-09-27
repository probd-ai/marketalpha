# Stage-2 execution guard tests — pure, no broker, no Mongo.
from datetime import datetime, timedelta

import pytest

import order_execution as oe


@pytest.fixture(autouse=True)
def _clean_caps(monkeypatch):
    # .env (loaded by api_server imports elsewhere in the suite) may carry tiny
    # supervised-test caps; these tests must see the defaults unless they set caps.
    monkeypatch.delenv("MH_MAX_ORDER_VALUE", raising=False)
    monkeypatch.delenv("MH_MAX_EXECUTION_VALUE", raising=False)


def _plan(status="approved", feasible=True, age_hours=1, actions=None):
    return {
        "status": status, "feasible": feasible,
        "generated_at": (datetime.now() - timedelta(hours=age_hours)).isoformat(),
        "actions": actions if actions is not None else [
            {"seq": 1, "symbol": "AAA", "action": "SELL", "qty": 10,
             "order_type": "LIMIT", "limit_price": 99.0, "last_price": 100.0,
             "product": "CNC"},
            {"seq": 2, "symbol": "BBB", "action": "BUY", "qty": 100,
             "order_type": "LIMIT", "limit_price": 202.0, "last_price": 200.0,
             "product": "CNC"},
        ],
    }


SEC = {"AAA": "111", "BBB": "222"}


def test_slice_qty_math():
    assert oe.slice_qty(100, 0, 0.2) == 20
    assert oe.slice_qty(100, 90, 0.2) == 10   # capped at remainder
    assert oe.slice_qty(100, 100, 0.2) == 0
    assert oe.slice_qty(3, 0, 0.2) == 1        # ceil, never 0 while remaining
    assert oe.slice_qty(10, 0, 1.0) == 10


def test_validate_blocks_unapproved_and_unfunded_and_stale():
    assert any("approve" in e for e in
               oe.validate_execution(_plan(status="draft"), fraction=0.2, dry_run=True))
    assert any("NOT FUNDED" in e for e in
               oe.validate_execution(_plan(feasible=False), fraction=0.2, dry_run=True))
    assert any("regenerate" in e for e in
               oe.validate_execution(_plan(age_hours=30), fraction=0.2, dry_run=True))
    assert oe.validate_execution(_plan(), fraction=0.2, dry_run=True) == []


def test_kill_switch_blocks_real_orders(monkeypatch):
    monkeypatch.delenv("MH_TRADING_ENABLED", raising=False)
    errs = oe.validate_execution(_plan(), fraction=0.2, dry_run=False)
    assert any("kill-switch" in e for e in errs)
    monkeypatch.setenv("MH_TRADING_ENABLED", "true")
    errs = oe.validate_execution(_plan(), fraction=0.2, dry_run=False)
    assert not any("kill-switch" in e for e in errs)  # market hours may still block


def test_market_hours():
    assert oe.in_market_hours(datetime(2026, 7, 6, 10, 0)) is True    # Mon 10:00
    assert oe.in_market_hours(datetime(2026, 7, 6, 9, 0)) is False    # pre-open
    assert oe.in_market_hours(datetime(2026, 7, 6, 15, 45)) is False  # post-close
    assert oe.in_market_hours(datetime(2026, 7, 5, 10, 0)) is False   # Sunday


def test_batch_sells_first_and_limit_prices():
    orders, errs, _ = oe.build_execution_batch(_plan(), fraction=0.2, security_ids=SEC)
    assert errs == []
    assert orders[0]["side"] == "SELL" and orders[0]["qty"] == 2
    assert orders[1]["side"] == "BUY" and orders[1]["qty"] == 20
    assert orders[1]["price"] == 202.0 and orders[1]["security_id"] == "222"


def test_batch_missing_security_id_blocks():
    orders, errs, _ = oe.build_execution_batch(_plan(), fraction=0.2,
                                            security_ids={"AAA": "111"})
    assert any("BBB" in e and "securityId" in e for e in errs)


def test_per_order_cap(monkeypatch):
    monkeypatch.setenv("MH_MAX_ORDER_VALUE", "1000")
    orders, errs, _ = oe.build_execution_batch(_plan(), fraction=1.0, security_ids=SEC)
    assert any("per-order cap" in e for e in errs)
    monkeypatch.delenv("MH_MAX_ORDER_VALUE")


def test_total_execution_cap(monkeypatch):
    monkeypatch.setenv("MH_MAX_EXECUTION_VALUE", "5000")
    orders, errs, _ = oe.build_execution_batch(_plan(), fraction=1.0, security_ids=SEC)
    assert any("per-run cap" in e for e in errs)
    monkeypatch.delenv("MH_MAX_EXECUTION_VALUE")


def test_punched_rows_reduce_next_slice():
    plan = _plan()
    plan["actions"][1]["punched_qty"] = 80
    orders, errs, _ = oe.build_execution_batch(plan, fraction=0.5, security_ids=SEC)
    buy = [o for o in orders if o["side"] == "BUY"][0]
    assert buy["qty"] == 20  # ceil(100*0.5)=50 capped by remaining 20


def test_market_hours_override_env(monkeypatch):
    monkeypatch.setenv("MH_TRADING_ENABLED", "true")
    monkeypatch.setenv("MH_MARKET_HOURS_CHECK", "false")
    errs = oe.validate_execution(_plan(), fraction=0.2, dry_run=False)
    assert not any("market hours" in e for e in errs)
    monkeypatch.setenv("MH_MARKET_HOURS_CHECK", "true")
    # guard restored (may or may not trigger depending on wall clock; flag check only)
    assert oe.market_hours_check_enabled() is True


def test_batch_refuses_rows_without_price_basis():
    """Cap enforcement needs a price: a row with no limit_price and no last_price
    must be a blocking error, never a zero-value order that bypasses caps."""
    plan = _plan(actions=[
        {"seq": 1, "symbol": "AAA", "action": "BUY", "qty": 10,
         "order_type": "LIMIT", "limit_price": None, "last_price": None,
         "product": "CNC"},
    ])
    orders, errors, _ = oe.build_execution_batch(plan, fraction=1.0, security_ids=SEC)
    assert orders == []
    assert any("price" in e.lower() for e in errors), errors


def test_dhan_network_error_wrapped_as_broker_error(monkeypatch):
    """requests-level failures (Timeout/ConnectionError) must surface as
    BrokerError so the execute loop's audit path handles them."""
    import requests as _requests
    monkeypatch.setenv("DHAN_CLIENT_ID", "cid")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "tok")
    from brokers import BrokerError
    from brokers.dhan import DhanClient

    def _boom(*a, **k):
        raise _requests.exceptions.ConnectTimeout("no route")
    monkeypatch.setattr(_requests, "post", _boom)
    monkeypatch.setattr(_requests, "get", _boom)
    c = DhanClient()
    with pytest.raises(BrokerError):
        c.place_order(security_id="1", side="BUY", quantity=1,
                      order_type="LIMIT", price=10.0)
