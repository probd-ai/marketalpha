import pytest

from fund.engine.costs import Costs

C = Costs(buy_rate=0.0011862, sell_rate=0.0010362, slip=0.0005)


def test_sell_proceeds_apply_slippage_then_charges():
    assert C.sell_proceeds(10, 100.0) == pytest.approx(10 * 100.0 * (1 - 0.0005) * (1 - 0.0010362), rel=1e-15)


def test_buy_qty_spends_exactly_the_value():
    q = C.buy_qty(1000.0, 100.0)
    assert q == pytest.approx(1000.0 / (100.0 * (1 + 0.0005) * (1 + 0.0011862)), rel=1e-15)
    assert C.buy_cost(q, 100.0) == pytest.approx(1000.0, rel=1e-12)


def test_zero_costs_are_identity():
    z = Costs(0.0, 0.0, 0.0)
    assert z.sell_proceeds(3, 7.0) == 21.0 and z.buy_qty(21.0, 7.0) == 3.0
