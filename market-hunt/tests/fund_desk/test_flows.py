import math

import pytest

from fund.engine.types import Order, Reason
from fund.flows import resize_factors, total_units, units_at_go_live, validate_flow
from fund.ticket import build_ticket

W = {"a": 0.6, "b": 0.4}


def test_units_make_flows_return_neutral():
    u0 = units_at_go_live(4_500_000, 1.5)                 # 3,000,000 units at NAV 1.5
    nav_s = 1.8
    value_before = u0 * nav_s                             # 5.4 L... fund grew 20%
    deposit = 500_000
    u = total_units(4_500_000, 1.5, [deposit / nav_s])
    assert u * nav_s == pytest.approx(value_before + deposit)
    assert (u * 2.0) / (u * nav_s) == pytest.approx(2.0 / nav_s)   # later returns unaffected by the deposit


def test_deposit_spreads_at_target_weights_without_reset():
    f = resize_factors({"a": 700.0, "b": 300.0}, W, 100.0, reset=False)
    assert f["a"] * 700 - 700 == pytest.approx(60.0) and f["b"] * 300 - 300 == pytest.approx(40.0)


def test_deposit_with_reset_targets_weights_of_new_total():
    f = resize_factors({"a": 700.0, "b": 300.0}, W, 100.0, reset=True)
    assert f["a"] * 700 == pytest.approx(0.6 * 1100) and f["b"] * 300 == pytest.approx(0.4 * 1100)


def test_withdrawal_sells_pro_rata_and_cannot_exceed_fund():
    f = resize_factors({"a": 600.0, "b": 400.0}, W, -100.0, reset=False)
    assert f["a"] * 600 == pytest.approx(540.0) and f["b"] * 400 == pytest.approx(360.0)
    with pytest.raises(ValueError, match="exceeds"):
        resize_factors({"a": 600.0, "b": 400.0}, W, -1000.0, reset=False)


def test_no_flow_no_reset_means_no_resize():
    assert resize_factors({"a": 1.0}, {"a": 1.0}, 0.0, reset=False) is None


@pytest.mark.parametrize("amount, ok", [(500_000, True), (-100_000, True), (0, False), (math.nan, False),
                                        ("5L", False), (True, False), (-5_000_000, False)])
def test_validate_flow(amount, ok):
    assert (validate_flow(amount, 4_500_000) is None) == ok


def test_ticket_labels_deposit_lines():
    sl = {"orders": [], "scale": 1.0, "held_before": {"AAA": 10.0}, "held_after": {"AAA": 10.0},
          "reset_factor": 1.2, "resize_reasons": [Reason.DEPOSIT]}
    lines, _ = build_ticket({"Value core": sl}, {"AAA": 100.0})
    assert lines[0].side == "BUY" and lines[0].qty == 2 and lines[0].reasons == [Reason.DEPOSIT]
