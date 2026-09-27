# Reprice ladder for unfilled remainders. Pure, no broker, no Mongo.
#
# This is load-bearing, not a nicety. The limit band defaults to ±0.1% (user
# decision, 2026-09-06) which on low-ADV names sits inside the spread — orders
# will routinely not fill. In a sell->buy chain an unfilled SELL starves the BUY
# group behind it, so recovering fills is what keeps the rebalance moving.
#
# Two independent ceilings, whichever binds first:
#   max_retries          — how many times we widen
#   max_slippage_percent — how far from the original anchor we will ever go
import pytest

import order_execution as oe


def test_band_default_is_the_tight_one():
    from broker_sync import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["limit_band_percent"] == 0.1


def test_band_stays_configurable_per_mapping():
    from broker_sync import normalize_settings
    assert normalize_settings({"limit_band_percent": 1.0})["limit_band_percent"] == 1.0


def test_ladder_widens_on_each_retry():
    assert oe.reprice_band(0) == 0.1
    assert oe.reprice_band(1) == 0.3
    assert oe.reprice_band(2) == 0.6


def test_ladder_stops_at_max_retries():
    assert oe.reprice_band(3, max_retries=2) is None


def test_ladder_stops_when_slippage_ceiling_would_be_breached():
    """Widening to 0.6% is refused when only 0.5% of slippage is permitted."""
    assert oe.reprice_band(2, max_slippage_percent=0.5) is None
    assert oe.reprice_band(1, max_slippage_percent=0.5) == 0.3


def test_custom_ladder_is_respected():
    assert oe.reprice_band(1, ladder=[0.05, 0.2, 0.9]) == 0.2


def test_reprice_price_moves_against_us_on_each_side():
    """BUY has to bid UP to fill, SELL has to offer DOWN."""
    assert oe.limit_price_for(100.0, "BUY", 0.3) == pytest.approx(100.30)
    assert oe.limit_price_for(100.0, "SELL", 0.3) == pytest.approx(99.70)


def test_exhausted_row_is_reported_for_carry_not_dropped():
    row = {"symbol": "AAA", "action": "SELL", "qty": 100, "retries": 3}
    decision = oe.reprice_decision(row, max_retries=2)
    assert decision["action"] == "CARRY"
    assert "AAA" in decision["reason"]


def test_row_with_retries_left_is_repriced():
    row = {"symbol": "AAA", "action": "SELL", "qty": 100, "retries": 1,
           "last_price": 100.0}
    decision = oe.reprice_decision(row, max_retries=2)
    assert decision["action"] == "REPRICE"
    assert decision["band"] == 0.3
    assert decision["limit_price"] == pytest.approx(99.70)


def test_cancel_must_be_confirmed_before_a_replacement_is_sent():
    """The one path in this design that can double-punch. A replacement is only
    permitted once the original is confirmed gone from the live order book."""
    assert oe.safe_to_replace(order_id="X1", book=[{"order_id": "X1",
                                                   "status": "CANCELLED"}]) is True
    assert oe.safe_to_replace(order_id="X1", book=[{"order_id": "X1",
                                                   "status": "OPEN"}]) is False
    # partially filled but still live -> replacing it would duplicate the remainder
    assert oe.safe_to_replace(order_id="X1", book=[{"order_id": "X1",
                                                   "status": "PARTIAL"}]) is False


def test_order_absent_from_the_book_is_not_assumed_cancelled():
    """An order missing from the book is ambiguous — it may not have registered
    yet. Refuse to replace rather than risk sending a duplicate."""
    assert oe.safe_to_replace(order_id="X1", book=[]) is False
