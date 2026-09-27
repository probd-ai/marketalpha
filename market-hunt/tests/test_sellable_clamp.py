# Settlement-aware SELL clamping. Pure, no broker, no Mongo.
#
# A SELL sized against the TOTAL position gets rejected by the broker when part of
# that position is unsettled (T+1) or pledged. Rejection burns an execution slice
# and leaves the book unbalanced, and it lands hardest in the T+1 window right
# after a rebalance — exactly when we rebalance.
#
# Design notes (these deviate from the written plan, deliberately):
#  * The clamp does NOT block the plan. Refusing to execute because one name is in
#    T+1 would kill an entire rebalance; clamping the affected row and continuing
#    is strictly safer and keeps the other rows moving.
#  * The clamp applies to the SLICE, not the planned total: slice first, then clamp.
#    Clamping the total first would silently shrink every later day's slice too.
#  * Plans persisted before this change carry no sellable_qty. Those rows must be
#    left alone rather than clamped to zero, or an in-flight plan would stall.
from datetime import datetime, timedelta

import pytest

import order_execution as oe


@pytest.fixture(autouse=True)
def _clean_caps(monkeypatch):
    monkeypatch.delenv("MH_MAX_ORDER_VALUE", raising=False)
    monkeypatch.delenv("MH_MAX_EXECUTION_VALUE", raising=False)


SEC = {"AAA": "111", "BBB": "222"}


def _plan(actions):
    return {"status": "approved", "feasible": True,
            "generated_at": (datetime.now() - timedelta(hours=1)).isoformat(),
            "actions": actions}


def _sell(qty, sellable=None, seq=1, symbol="AAA"):
    row = {"seq": seq, "symbol": symbol, "action": "SELL", "qty": qty,
           "order_type": "LIMIT", "limit_price": 99.0, "last_price": 100.0,
           "product": "CNC"}
    if sellable is not None:
        row["sellable_qty"] = sellable
    return row


def _buy(qty, sellable=None, seq=2, symbol="BBB"):
    row = {"seq": seq, "symbol": symbol, "action": "BUY", "qty": qty,
           "order_type": "LIMIT", "limit_price": 202.0, "last_price": 200.0,
           "product": "CNC"}
    if sellable is not None:
        row["sellable_qty"] = sellable
    return row


def test_sell_slice_clamped_to_sellable_and_warned():
    # fraction 1.0 -> slice is the whole 100; only 60 is deliverable
    orders, errors, warnings = oe.build_execution_batch(
        _plan([_sell(100, sellable=60)]), fraction=1.0, security_ids=SEC)
    assert [o["qty"] for o in orders] == [60]
    assert orders[0]["clamped_from"] == 100
    assert errors == []                              # clamping must NOT block
    assert any("AAA" in w and "60" in w for w in warnings)


def test_sell_within_sellable_is_untouched():
    orders, errors, warnings = oe.build_execution_batch(
        _plan([_sell(50, sellable=60)]), fraction=1.0, security_ids=SEC)
    assert [o["qty"] for o in orders] == [50]
    assert "clamped_from" not in orders[0]
    assert errors == [] and warnings == []


def test_clamp_applies_to_the_slice_not_the_total():
    """qty 100, 20% slice = 20, sellable 10 -> 10. The clamp must bite the slice."""
    orders, errors, warnings = oe.build_execution_batch(
        _plan([_sell(100, sellable=10)]), fraction=0.2, security_ids=SEC)
    assert [o["qty"] for o in orders] == [10]
    assert orders[0]["clamped_from"] == 20           # the slice, not the planned 100
    assert errors == []


def test_slice_below_sellable_is_not_clamped():
    """qty 100, 20% slice = 20, sellable 60 -> untouched. Later slices deal with
    later settlement; today's slice is fully deliverable."""
    orders, _, warnings = oe.build_execution_batch(
        _plan([_sell(100, sellable=60)]), fraction=0.2, security_ids=SEC)
    assert [o["qty"] for o in orders] == [20]
    assert warnings == []


def test_zero_sellable_drops_the_row_without_blocking():
    """All of it is T+1 / pledged. Skip this row, warn, let the rest proceed."""
    orders, errors, warnings = oe.build_execution_batch(
        _plan([_sell(100, sellable=0), _buy(10)]), fraction=1.0, security_ids=SEC)
    assert [o["symbol"] for o in orders] == ["BBB"]   # the BUY still goes
    assert errors == []                               # NOT a blocking error
    assert any("AAA" in w and "nothing sellable" in w.lower() for w in warnings)


def test_buy_rows_are_never_clamped_by_sellable():
    orders, errors, warnings = oe.build_execution_batch(
        _plan([_buy(100, sellable=0)]), fraction=1.0, security_ids=SEC)
    assert [o["qty"] for o in orders] == [100]
    assert errors == [] and warnings == []


def test_legacy_plan_without_sellable_qty_is_not_clamped():
    """Plans persisted before this change have no sellable_qty. Treat as unknown
    and leave the row alone — clamping to zero would stall an in-flight plan."""
    orders, errors, warnings = oe.build_execution_batch(
        _plan([_sell(100)]), fraction=1.0, security_ids=SEC)
    assert [o["qty"] for o in orders] == [100]
    assert errors == [] and warnings == []


def test_clamp_runs_before_the_per_order_value_cap(monkeypatch):
    """A clamped order must be capped on its CLAMPED value, not the planned one —
    otherwise a large planned sell trips the cap and is refused even though the
    deliverable slice was well inside it."""
    monkeypatch.setenv("MH_MAX_ORDER_VALUE", "10000")   # 100 shares @100 = 10k planned
    orders, errors, _ = oe.build_execution_batch(
        _plan([_sell(500, sellable=50)]), fraction=1.0, security_ids=SEC)
    assert [o["qty"] for o in orders] == [50]           # 50 @ 100 = 5,000 < cap
    assert errors == []


def test_validate_execution_does_not_block_on_settlement():
    """Deliberate: settlement is handled by clamping, not by refusing the plan."""
    errs = oe.validate_execution(_plan([_sell(100, sellable=0)]),
                                 fraction=1.0, dry_run=True)
    assert errs == []
