"""Per-stock hard stop-loss: exit a holding intra-period, before the rebalance.

Contract: on any non-rebalance day, a long holding whose CLOSE has fallen to or
below buy_price * (1 - pct/100) is sold at that close; the proceeds sit in cash
until the next scheduled rebalance redeploys them. The stop is evaluated against
`avg_price` — the price the position was actually opened at — not a trailing high.
"""
import pytest

from engine.stateless import stop_loss_exits


def _hold(shares, avg):
    return {"shares": shares, "avg_price": avg}


def _px(close):
    return {"close_price": close}


class TestStopLossTrigger:
    def test_exact_threshold_is_a_stop(self):
        """-10.00% must trigger: the stop is 'at or below', not 'strictly below'."""
        out = stop_loss_exits({"A": _hold(10, 100.0)}, {"A": _px(90.0)}, 10.0)
        assert [e["symbol"] for e in out] == ["A"]

    def test_just_above_threshold_holds(self):
        out = stop_loss_exits({"A": _hold(10, 100.0)}, {"A": _px(90.01)}, 10.0)
        assert out == []

    def test_deep_breach_exits(self):
        out = stop_loss_exits({"A": _hold(10, 100.0)}, {"A": _px(55.0)}, 10.0)
        assert [e["symbol"] for e in out] == ["A"]

    def test_gain_never_stops(self):
        out = stop_loss_exits({"A": _hold(10, 100.0)}, {"A": _px(140.0)}, 10.0)
        assert out == []


class TestStopLossGuards:
    def test_zero_percent_disables_the_stop(self):
        out = stop_loss_exits({"A": _hold(10, 100.0)}, {"A": _px(1.0)}, 0.0)
        assert out == []

    def test_missing_price_is_not_a_stop(self):
        """No print today means no evidence of a breach — never exit blind."""
        out = stop_loss_exits({"A": _hold(10, 100.0)}, {}, 10.0)
        assert out == []

    def test_non_positive_avg_price_is_skipped(self):
        out = stop_loss_exits({"A": _hold(10, 0.0)}, {"A": _px(1.0)}, 10.0)
        assert out == []

    def test_empty_book_is_safe(self):
        assert stop_loss_exits({}, {"A": _px(1.0)}, 10.0) == []


class TestStopLossDetails:
    def test_only_breached_names_exit(self):
        holdings = {"A": _hold(10, 100.0), "B": _hold(5, 200.0), "C": _hold(1, 50.0)}
        prices = {"A": _px(89.0), "B": _px(199.0), "C": _px(40.0)}
        out = stop_loss_exits(holdings, prices, 10.0)
        assert sorted(e["symbol"] for e in out) == ["A", "C"]

    def test_exit_carries_pnl_and_prices(self):
        out = stop_loss_exits({"A": _hold(10, 100.0)}, {"A": _px(85.0)}, 10.0)
        e = out[0]
        assert e["shares"] == 10
        assert e["avg_price"] == 100.0
        assert e["exit_price"] == 85.0
        assert e["pnl"] == pytest.approx(-150.0)
        assert e["pnl_percent"] == pytest.approx(-15.0)

    def test_tighter_stop_catches_more(self):
        holdings = {"A": _hold(10, 100.0)}
        prices = {"A": _px(95.0)}
        assert stop_loss_exits(holdings, prices, 10.0) == []
        assert [e["symbol"] for e in stop_loss_exits(holdings, prices, 5.0)] == ["A"]
