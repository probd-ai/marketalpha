# Routing plan rows to broker accounts. Pure — no broker, no Mongo.
#
# The constraint everything here serves: CASH DOES NOT MOVE BETWEEN ACCOUNTS.
# Selling in `default` credits `default` only, so a BUY can be funded only by sells
# in the same account. Any code that treats cash as global is wrong.
import broker_sync


def _h(symbol, qty, px=100.0, sellable=None):
    return {"symbol": symbol, "quantity": qty, "last_price": px,
            "market_value": qty * px,
            "sellable_qty": qty if sellable is None else sellable}


def _sim(symbol, qty, px=100.0):
    return {"symbol": symbol, "quantity": qty, "market_value": qty * px,
            "current_price": px}


def _plan(sim_book, by_account, discretionary=None, **settings):
    return broker_sync.build_sync_plan(
        sim_book=sim_book, live_holdings=None, available_cash=None,
        settings={"tolerance_percent": 1.0, "min_order_value": 1.0,
                  "sell_unknown": True, **settings},
        by_account=by_account, discretionary=discretionary)


def _rows(plan, action=None):
    return [a for a in plan["actions"] if action is None or a["action"] == action]


def test_every_row_carries_an_account():
    plan = _plan([_sim("NEW", 10)],
                 {"a": {"holdings": [_h("OLD", 20)], "cash": 0.0},
                  "b": {"holdings": [], "cash": 5000.0}})
    assert all(r.get("account") in ("a", "b") for r in plan["actions"])


def test_sell_only_where_the_stock_is_actually_held():
    """A SELL routed to an account that does not hold the stock is rejected by the
    broker — and would burn an execution slice."""
    plan = _plan([], {"a": {"holdings": [_h("ONLY_IN_A", 20)], "cash": 0.0},
                      "b": {"holdings": [], "cash": 0.0}})
    sells = _rows(plan, "SELL")
    assert sells and all(r["account"] == "a" for r in sells)


def test_stock_held_in_two_accounts_sells_the_larger_holding_first():
    """Empties one account rather than leaving dust in both."""
    plan = _plan([], {"a": {"holdings": [_h("DUAL", 10)], "cash": 0.0},
                      "b": {"holdings": [_h("DUAL", 90)], "cash": 0.0}})
    sells = [r for r in _rows(plan, "SELL") if r["symbol"] == "DUAL"]
    assert sells[0]["account"] == "b"


def test_proceeds_in_one_account_never_fund_a_buy_in_another():
    """THE headline constraint. `a` can fund buys from its sale; `b` has nothing,
    so its buys must not borrow `a`'s proceeds."""
    plan = _plan([_sim("WANT", 100)],
                 {"a": {"holdings": [_h("SELLME", 100)], "cash": 0.0},
                  "b": {"holdings": [], "cash": 0.0}})
    per = plan["totals"]["by_account"]
    assert per["b"]["buy_value"] <= per["b"]["funding_available"] + 0.01


def test_per_account_funding_is_reported_separately():
    plan = _plan([_sim("WANT", 10)],
                 {"a": {"holdings": [_h("OLD", 50)], "cash": 1000.0},
                  "b": {"holdings": [], "cash": 7000.0}})
    per = plan["totals"]["by_account"]
    assert set(per) == {"a", "b"}
    assert per["b"]["available_cash"] == 7000.0
    assert per["a"]["available_cash"] == 1000.0


def test_discretionary_floor_applies_per_account():
    plan = _plan([], {"a": {"holdings": [_h("KEEP", 40)], "cash": 0.0}},
                 discretionary={"KEEP": 20})
    row = next(r for r in _rows(plan, "SELL") if r["symbol"] == "KEEP")
    assert row["qty"] == 20 and row["account"] == "a"


def test_sellable_clamp_is_per_account():
    """T+1 is per account, so the clamp must be too."""
    plan = _plan([], {"a": {"holdings": [_h("X", 100, sellable=30)], "cash": 0.0}})
    row = next(r for r in _rows(plan, "SELL") if r["symbol"] == "X")
    assert row["sellable_qty"] == 30


def test_sells_still_precede_buys_within_an_account():
    plan = _plan([_sim("NEW", 50)],
                 {"a": {"holdings": [_h("OLD", 100)], "cash": 0.0}})
    seqs = [(r["action"], r["seq"]) for r in plan["actions"] if r["account"] == "a"]
    last_sell = max((s for a, s in seqs if a == "SELL"), default=-1)
    first_buy = min((s for a, s in seqs if a == "BUY"), default=10**9)
    assert last_sell < first_buy


def test_single_account_path_is_unchanged_without_by_account():
    """Backward compatibility: no by_account -> today's exact behaviour, no
    `account` key on rows."""
    plan = broker_sync.build_sync_plan(
        sim_book=[_sim("AAA", 10)], live_holdings=[_h("OLD", 20)],
        available_cash=0.0,
        settings={"tolerance_percent": 1.0, "min_order_value": 1.0,
                  "sell_unknown": True})
    assert plan["actions"]
    assert all("account" not in r or r["account"] is None for r in plan["actions"])
    assert "by_account" not in plan["totals"]
