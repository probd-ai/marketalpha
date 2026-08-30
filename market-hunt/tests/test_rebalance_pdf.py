"""The PDF must render from a plan without inventing or dropping anything."""
from portfolio_composition import build_rebalance_plan
from rebalance_pdf import build_rebalance_pdf


def _plan():
    return build_rebalance_plan(
        current_book=[{"symbol": "OLD", "quantity": 10, "current_price": 100.0,
                       "market_value": 1000.0},
                      {"symbol": "KEEP", "quantity": 5, "current_price": 200.0,
                       "market_value": 1000.0}],
        targets=[{"symbol": "KEEP", "sleeve": "Mid", "target_value": 1000.0},
                 {"symbol": "NEW", "sleeve": "Mid", "target_value": 1000.0}],
        prices={"OLD": 100.0, "KEEP": 200.0, "NEW": 50.0},
    )


def test_pdf_renders_and_is_a_pdf():
    out = build_rebalance_pdf(_plan(), portfolio_name="Alpha", as_of="2026-08-14",
                              due_sleeves=["Mid"], symbol_meta={"NEW": {"index_tier": "Microcap 250"}})
    assert out[:5] == b"%PDF-"
    assert len(out) > 1000


def test_pdf_renders_with_no_orders():
    """A book already at target must still produce a readable sheet, not crash."""
    flat = build_rebalance_plan(
        current_book=[{"symbol": "A", "quantity": 1, "current_price": 10.0, "market_value": 10.0}],
        targets=[{"symbol": "A", "sleeve": "Mid", "target_value": 10.0}],
        prices={"A": 10.0})
    out = build_rebalance_pdf(flat, portfolio_name="Alpha", as_of="2026-08-14")
    assert out[:5] == b"%PDF-"


def test_pdf_renders_with_an_unpriced_target():
    p = build_rebalance_plan(current_book=[],
                             targets=[{"symbol": "GHOST", "sleeve": "Mid", "target_value": 100.0}],
                             prices={})
    out = build_rebalance_pdf(p, portfolio_name="Alpha", as_of="2026-08-14")
    assert out[:5] == b"%PDF-"


def test_indian_digit_grouping():
    from rebalance_pdf import _inr
    assert _inr(1234567) == "12,34,567"
    assert _inr(100000) == "1,00,000"
    assert _inr(999) == "999"
    assert _inr(-45000) == "-45,000"
