# Group-chained execution: sell a group, then buy the next group from the cash
# those sells actually realized. Pure state-machine tests — no broker, no Mongo.
#
# Why a state machine and not a poll loop: a request that blocks while waiting for
# fills cannot survive a crash, a timeout or a second caller, and the existing
# 10-minute plan lock assumes short calls. So /execute advances exactly ONE step
# per call and every transition is persisted.
from datetime import datetime, timedelta

import pytest

import order_execution as oe


@pytest.fixture(autouse=True)
def _clean_caps(monkeypatch):
    monkeypatch.delenv("MH_MAX_ORDER_VALUE", raising=False)
    monkeypatch.delenv("MH_MAX_EXECUTION_VALUE", raising=False)


def _row(seq, symbol, action, qty, px=100.0):
    return {"seq": seq, "symbol": symbol, "action": action, "qty": qty,
            "order_type": "LIMIT", "limit_price": px, "last_price": px,
            "product": "CNC"}


def _plan(actions, group_size=3, cash=0.0, **kw):
    p = {"status": "approved", "feasible": True,
         "generated_at": (datetime.now() - timedelta(hours=1)).isoformat(),
         "actions": actions, "group_size": group_size,
         "totals": {"available_cash": cash}}
    p.update(kw)
    return p


# ---------- group assembly ---------------------------------------------------

def test_groups_are_sells_first_then_buys_never_mixed():
    actions = ([_row(i, f"S{i}", "SELL", 10) for i in range(1, 5)] +
               [_row(i, f"B{i}", "BUY", 10) for i in range(5, 8)])
    groups = oe.build_groups(actions, group_size=3)
    kinds = [g["kind"] for g in groups]
    assert kinds == ["SELL", "SELL", "BUY"]        # 4 sells -> 3+1, then 3 buys
    assert [len(g["rows"]) for g in groups] == [3, 1, 3]
    # a group must never contain both sides — proceeds ordering depends on it
    for g in groups:
        assert len({r["action"] for r in g["rows"]}) == 1


def test_group_size_is_configurable():
    actions = [_row(i, f"S{i}", "SELL", 10) for i in range(1, 6)]
    assert [len(g["rows"]) for g in oe.build_groups(actions, group_size=2)] == [2, 2, 1]


# ---------- transitions ------------------------------------------------------

def test_pending_sell_group_is_punched_first():
    plan = _plan([_row(1, "S1", "SELL", 10), _row(2, "B1", "BUY", 10)])
    plan["groups"] = oe.build_groups(plan["actions"], group_size=3)
    plan["cursor"] = 0
    action, _ = oe.advance_plan(plan, fills={})
    assert action == "PUNCH"
    assert plan["groups"][0]["kind"] == "SELL"


def test_punched_group_with_open_orders_waits_and_changes_nothing():
    plan = _plan([_row(1, "S1", "SELL", 10)])
    plan["groups"] = oe.build_groups(plan["actions"], group_size=3)
    plan["cursor"] = 0
    plan["groups"][0]["state"] = "punched"
    plan["groups"][0]["punched_at"] = datetime.now().isoformat()
    before = dict(plan["groups"][0])
    action, _ = oe.advance_plan(plan, fills={"S1": {"status": "OPEN", "traded_qty": 0}})
    assert action == "WAIT"
    assert plan["groups"][0]["state"] == before["state"]   # no mutation while waiting


def test_all_fills_terminal_settles_and_advances_the_cursor():
    plan = _plan([_row(1, "S1", "SELL", 10, px=100.0), _row(2, "B1", "BUY", 5, px=100.0)])
    plan["groups"] = oe.build_groups(plan["actions"], group_size=3)
    plan["cursor"] = 0
    plan["groups"][0]["state"] = "punched"
    plan["groups"][0]["punched_at"] = datetime.now().isoformat()
    action, _ = oe.advance_plan(
        plan, fills={"S1": {"status": "COMPLETE", "traded_qty": 10, "avg_price": 100.0}})
    assert action == "SETTLE"
    assert plan["groups"][0]["state"] == "settled"
    assert plan["cursor"] == 1
    assert plan["realized_cash"] == pytest.approx(1000.0)   # 10 x 100 actually received


def test_cursor_past_last_group_completes():
    plan = _plan([_row(1, "S1", "SELL", 10)])
    plan["groups"] = oe.build_groups(plan["actions"], group_size=3)
    plan["cursor"] = 1
    action, _ = oe.advance_plan(plan, fills={})
    assert action == "COMPLETE"


# ---------- proceeds-based buy sizing (Task 7) -------------------------------

def test_buys_are_sized_from_what_the_sells_actually_realized():
    """3 sells planned, only 2 fill -> the buy group spends those two lots only."""
    rows = [{"symbol": "B1", "qty": 10, "last_price": 100.0},
            {"symbol": "B2", "qty": 10, "last_price": 100.0}]
    sized, deferred = oe.size_buy_group(rows, budget=1500.0)
    assert [(r["symbol"], r["qty"]) for r in sized] == [("B1", 10), ("B2", 5)]
    # B2 was HALF funded — the unbought 5 must carry to a later session, not vanish
    assert [(r["symbol"], r["qty"]) for r in deferred] == [("B2", 5)]


def test_unaffordable_row_is_deferred_not_dropped():
    rows = [{"symbol": "B1", "qty": 10, "last_price": 100.0},
            {"symbol": "B2", "qty": 10, "last_price": 100.0}]
    sized, deferred = oe.size_buy_group(rows, budget=1000.0)
    assert [(r["symbol"], r["qty"]) for r in sized] == [("B1", 10)]
    assert [r["symbol"] for r in deferred] == ["B2"]   # carries, never silently dropped


def test_zero_budget_defers_the_whole_group():
    rows = [{"symbol": "B1", "qty": 10, "last_price": 100.0}]
    sized, deferred = oe.size_buy_group(rows, budget=0.0)
    assert sized == [] and [r["symbol"] for r in deferred] == ["B1"]


def test_budget_accounts_for_cash_already_spent():
    plan = _plan([], cash=5000.0, realized_cash=2000.0, already_spent=6000.0)
    assert oe.buy_budget(plan) == pytest.approx(1000.0)   # 5000 + 2000 - 6000


def test_budget_never_goes_negative():
    plan = _plan([], cash=1000.0, realized_cash=0.0, already_spent=5000.0)
    assert oe.buy_budget(plan) == 0.0


def test_zero_fill_sell_group_stalls_rather_than_spinning():
    """No proceeds -> the buy group behind it cannot be funded. The chain must
    say so, not loop forever re-punching."""
    plan = _plan([_row(1, "S1", "SELL", 10), _row(2, "B1", "BUY", 10)], cash=0.0)
    plan["groups"] = oe.build_groups(plan["actions"], group_size=3)
    plan["cursor"] = 0
    plan["groups"][0]["state"] = "punched"
    plan["groups"][0]["punched_at"] = (datetime.now() - timedelta(hours=2)).isoformat()
    oe.advance_plan(plan, fills={"S1": {"status": "CANCELLED", "traded_qty": 0}})
    assert plan["realized_cash"] == 0.0
    action, info = oe.advance_plan(plan, fills={})
    assert action == "STALLED"
    assert "fund" in info["reason"].lower() or "budget" in info["reason"].lower()
