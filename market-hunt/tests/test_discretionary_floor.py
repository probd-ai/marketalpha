# Discretionary quantities as a NEVER-SELL FLOOR in the order planner.
#
# The bug this closes: `discretionary` was a reconciliation-only concept. The
# holdings page could say "20 of these 40 are yours" while build_sync_plan happily
# queued all 40 for sale. Two systems disagreeing about the same book is exactly the
# class of defect that becomes a wrong order.
#
# Semantics match broker_holdings.reconcile: a discretionary quantity is the USER'S,
# held alongside the strategy's. It does NOT satisfy a target -- 50 held privately
# with a target of 30 still means buy 30 -- and it can never be sold.
import broker_sync


def _plan(sim_book, live, discretionary=None, **settings):
    return broker_sync.build_sync_plan(
        sim_book=sim_book, live_holdings=live, available_cash=0.0,
        settings={"tolerance_percent": 1.0, "min_order_value": 1.0, **settings},
        discretionary=discretionary)


def _live(symbol, qty, px=100.0):
    return {"symbol": symbol, "quantity": qty, "last_price": px,
            "market_value": qty * px, "sellable_qty": qty}


def _sim(symbol, qty, px=100.0):
    return {"symbol": symbol, "quantity": qty, "market_value": qty * px,
            "current_price": px}


def _row(plan, symbol):
    return next((a for a in plan["actions"] if a["symbol"] == symbol), None)


def test_sell_never_dips_below_the_discretionary_floor():
    """40 held, 20 marked mine, no target -> sell at most 20."""
    plan = _plan([], [_live("ATHERENERG", 40)], {"ATHERENERG": 20},
                 sell_unknown=True)
    row = _row(plan, "ATHERENERG")
    assert row is not None and row["action"] == "SELL"
    assert row["qty"] == 20                       # not 40


def test_fully_discretionary_position_is_never_sold():
    plan = _plan([], [_live("KEEPME", 40)], {"KEEPME": 40}, sell_unknown=True)
    assert _row(plan, "KEEPME") is None
    assert any(s["symbol"] == "KEEPME" for s in plan["skipped"])


def test_discretionary_larger_than_holding_does_not_go_negative():
    plan = _plan([], [_live("ODD", 10)], {"ODD": 999}, sell_unknown=True)
    assert _row(plan, "ODD") is None


def test_discretionary_does_not_satisfy_a_target():
    """50 held privately, target 30 -> still BUY 30. The user's shares are theirs,
    not the strategy's inventory (matches broker_holdings.reconcile).

    capital_mode='sim' here so the target is taken at face value; `account` mode
    would rescale it to the whole account and hide what is being asserted."""
    plan = _plan([_sim("AAA", 30)], [_live("AAA", 50)], {"AAA": 50},
                 capital_mode="sim")
    row = _row(plan, "AAA")
    assert row is not None and row["action"] == "BUY" and row["qty"] == 30


def test_partial_discretionary_reduces_the_system_position():
    """40 held, 25 mine -> the strategy owns 15. Target 15 means nothing to do."""
    plan = _plan([_sim("AAA", 15)], [_live("AAA", 40)], {"AAA": 25},
                 capital_mode="sim")
    assert _row(plan, "AAA") is None


def test_excess_above_target_sells_only_the_system_portion():
    """40 held, 20 mine, target 5 -> strategy holds 15, sell 10, floor intact."""
    plan = _plan([_sim("AAA", 5)], [_live("AAA", 40)], {"AAA": 20},
                 capital_mode="sim")
    row = _row(plan, "AAA")
    assert row is not None and row["action"] == "SELL"
    assert row["qty"] == 15                       # 40 - 20 mine - 5 target
    assert row["live_qty"] - row["qty"] >= 20     # floor never breached


def test_no_discretionary_behaves_exactly_as_before():
    """Backward compatibility: the argument is optional and absent means no floor."""
    a = _plan([], [_live("AAA", 40)], None, sell_unknown=True)
    b = broker_sync.build_sync_plan(
        sim_book=[], live_holdings=[_live("AAA", 40)], available_cash=0.0,
        settings={"tolerance_percent": 1.0, "min_order_value": 1.0,
                  "sell_unknown": True})
    assert _row(a, "AAA")["qty"] == _row(b, "AAA")["qty"] == 40


def test_floor_is_reported_on_the_row():
    """The user must be able to see WHY a sell is smaller than the holding."""
    plan = _plan([], [_live("ATHERENERG", 40)], {"ATHERENERG": 20},
                 sell_unknown=True)
    row = _row(plan, "ATHERENERG")
    assert row.get("discretionary_qty") == 20
