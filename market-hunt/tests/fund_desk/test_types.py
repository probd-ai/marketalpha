from datetime import date

from fund.engine.types import Lot, Order, Reason, SleeveState


def _state():
    return SleeveState(cash=1000.0,
                       lots={"AAA": [Lot("AAA", 10.0, date(2021, 1, 4), 0), Lot("AAA", 5.0, date(2021, 1, 11), 1)],
                             "BBB": [Lot("BBB", 2.0, date(2021, 1, 4), 3)]},
                       params={"n": 10})


def test_qty_sums_lots():
    s = _state()
    assert s.qty("AAA") == 15.0
    assert s.qty("ZZZ") == 0.0


def test_value_uses_given_prices():
    s = _state()
    assert s.value({"AAA": 100.0, "BBB": 50.0}) == 1000.0 + 1500.0 + 100.0


def test_round_trip_dict():
    s = _state()
    assert SleeveState.from_dict(s.to_dict()) == s


def test_reason_codes_are_strings():
    assert Reason.GRID_DOWN_POSITIVE_MONTH == "GRID_DOWN_POSITIVE_MONTH"
    assert {r.value for r in Reason} >= {"NEW_LOWEST_22D", "TOPUP_TO_TARGET", "REMAINDER_SPREAD", "TOP5_NEW_TRANCHE",
                                        "TRANCHE_ROLL", "TRANCHE_EXPIRED", "TRIM_ABOVE_CAP", "CASH_DEPLOY",
                                        "INDEX_EXIT", "MONTHLY_RESET"}


def test_order_is_frozen():
    o = Order("AAA", "BUY", 1.0, 10.0, 10.0, Reason.NEW_LOWEST_22D)
    try:
        o.qty = 2.0
        raise AssertionError("Order must be immutable")
    except AttributeError:
        pass


def test_price_collection_year_parsing():
    from fund.data import _last_year
    assert _last_year("prices_2015_2019") == 2019 and _last_year("prices_2025") == 2025
