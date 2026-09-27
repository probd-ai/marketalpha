# Per-account group chains. Pure — no broker, no Mongo.
#
# Cash does not move between broker accounts. Each account therefore runs its OWN
# sell -> buy chain with its OWN budget, and the headline invariant is that proceeds
# realized in one account can never fund a buy in another.
from datetime import datetime, timedelta

import pytest

import order_execution as oe


def _row(seq, symbol, action, qty, account, px=100.0):
    return {"seq": seq, "symbol": symbol, "action": action, "qty": qty,
            "account": account, "order_type": "LIMIT", "limit_price": px,
            "last_price": px, "product": "CNC"}


def _plan(actions, cash=None, group_size=3):
    accounts = sorted({r["account"] for r in actions})
    plan = {"status": "approved", "feasible": True,
            "generated_at": (datetime.now() - timedelta(minutes=5)).isoformat(),
            "actions": actions, "accounts": accounts, "group_size": group_size,
            "by_account": {}}
    for a in accounts:
        plan["by_account"][a] = {
            "starting_cash": (cash or {}).get(a, 0.0),
            "realized_cash": 0.0, "already_spent": 0.0, "cursor": 0,
            "groups": oe.build_groups([r for r in actions if r["account"] == a],
                                      group_size=group_size)}
    return plan


def _fills(rows, fraction=1.0, px=100.0):
    return {r["symbol"]: {"status": "COMPLETE",
                          "traded_qty": int(r["qty"] * fraction),
                          "avg_price": px} for r in rows}


# ---------- the headline constraint ------------------------------------------

def test_proceeds_in_one_account_never_fund_a_buy_in_another():
    """A sells 100 units (10,000). B has nothing. B's buy must NOT be funded."""
    plan = _plan([_row(1, "SELLA", "SELL", 100, "a"),
                  _row(2, "BUYB", "BUY", 50, "b")])
    # settle A's sell
    plan["by_account"]["a"]["groups"][0]["state"] = "punched"
    plan["by_account"]["a"]["groups"][0]["punched_at"] = datetime.now().isoformat()
    oe.advance_plan(plan, account="a",
                    fills=_fills(plan["by_account"]["a"]["groups"][0]["rows"]))
    assert plan["by_account"]["a"]["realized_cash"] == pytest.approx(10_000.0)
    assert plan["by_account"]["b"]["realized_cash"] == 0.0

    action, info = oe.advance_plan(plan, account="b", fills={})
    assert action == "STALLED"
    assert "budget" in info["reason"].lower() or "fund" in info["reason"].lower()


def test_each_account_funds_its_own_buys():
    plan = _plan([_row(1, "SA", "SELL", 100, "a"), _row(2, "BA", "BUY", 50, "a"),
                  _row(3, "SB", "SELL", 80, "b"), _row(4, "BB", "BUY", 40, "b")])
    for acct in ("a", "b"):
        g = plan["by_account"][acct]["groups"][0]
        g["state"] = "punched"; g["punched_at"] = datetime.now().isoformat()
        oe.advance_plan(plan, account=acct, fills=_fills(g["rows"]))
    assert plan["by_account"]["a"]["realized_cash"] == pytest.approx(10_000.0)
    assert plan["by_account"]["b"]["realized_cash"] == pytest.approx(8_000.0)
    for acct in ("a", "b"):
        action, _ = oe.advance_plan(plan, account=acct, fills={})
        assert action == "PUNCH"


def test_one_account_stalling_does_not_block_the_other():
    """b's sell fails entirely; a must still complete its own chain."""
    plan = _plan([_row(1, "SA", "SELL", 100, "a"), _row(2, "BA", "BUY", 50, "a"),
                  _row(3, "SB", "SELL", 80, "b"), _row(4, "BB", "BUY", 40, "b")])
    ga = plan["by_account"]["a"]["groups"][0]
    ga["state"] = "punched"; ga["punched_at"] = datetime.now().isoformat()
    oe.advance_plan(plan, account="a", fills=_fills(ga["rows"]))

    gb = plan["by_account"]["b"]["groups"][0]
    gb["state"] = "punched"
    gb["punched_at"] = (datetime.now() - timedelta(hours=2)).isoformat()
    oe.advance_plan(plan, account="b",
                    fills={"SB": {"status": "CANCELLED", "traded_qty": 0}})
    assert oe.advance_plan(plan, account="b", fills={})[0] == "STALLED"
    assert oe.advance_plan(plan, account="a", fills={})[0] == "PUNCH"


def test_plan_completes_only_when_every_account_is_done():
    plan = _plan([_row(1, "SA", "SELL", 10, "a"), _row(2, "SB", "SELL", 10, "b")])
    plan["by_account"]["a"]["cursor"] = 1          # a finished
    assert oe.advance_plan(plan, account="a", fills={})[0] == "COMPLETE"
    assert oe.plan_complete(plan) is False         # b has not started
    plan["by_account"]["b"]["cursor"] = 1
    assert oe.plan_complete(plan) is True


def test_next_account_with_work_round_robins():
    plan = _plan([_row(1, "SA", "SELL", 10, "a"), _row(2, "SB", "SELL", 10, "b")])
    assert oe.next_account_with_work(plan) == "a"
    plan["by_account"]["a"]["cursor"] = 1
    assert oe.next_account_with_work(plan) == "b"
    plan["by_account"]["b"]["cursor"] = 1
    assert oe.next_account_with_work(plan) is None


def test_partial_fill_scales_only_that_accounts_buys():
    plan = _plan([_row(1, "SA", "SELL", 100, "a"), _row(2, "BA", "BUY", 100, "a"),
                  _row(3, "SB", "SELL", 100, "b"), _row(4, "BB", "BUY", 100, "b")])
    ga = plan["by_account"]["a"]["groups"][0]
    ga["state"] = "punched"; ga["punched_at"] = datetime.now().isoformat()
    oe.advance_plan(plan, account="a", fills=_fills(ga["rows"], fraction=0.5))
    assert plan["by_account"]["a"]["realized_cash"] == pytest.approx(5_000.0)
    oe.advance_plan(plan, account="a", fills={})
    sized = plan["by_account"]["a"]["groups"][1].get("sized_rows") or []
    assert sum(r["qty"] * r["last_price"] for r in sized) <= 5_000.01
    assert plan["by_account"]["b"]["realized_cash"] == 0.0   # untouched


# ---------- backward compatibility -------------------------------------------

def test_legacy_single_account_plan_still_advances():
    """A plan persisted before routing has groups/cursor at the top level and no
    `by_account`. It must advance exactly as before."""
    plan = {"status": "approved", "feasible": True,
            "generated_at": datetime.now().isoformat(),
            "actions": [{"seq": 1, "symbol": "AAA", "action": "SELL", "qty": 10,
                         "order_type": "LIMIT", "limit_price": 99.0,
                         "last_price": 100.0, "product": "CNC"}]}
    plan["groups"] = oe.build_groups(plan["actions"], group_size=3)
    plan["cursor"] = 0
    action, _ = oe.advance_plan(plan, fills={})
    assert action == "PUNCH"
    assert "by_account" not in plan or not plan["by_account"]
