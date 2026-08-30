# Notification logic tests — pure functions only, no network, no Mongo.
from datetime import date

from notify_rebalance import (build_order_lines, business_days_until, decide_kind,
                              format_orders_message, format_preview_message, orders_csv)

SLEEVE = {
    "id": "s1", "label": "Core · Mid", "universe": "NIFTY500MICRO750",
    "data_as_of": "2026-07-03", "next_due_estimate": "2026-07-16",
    "eligible_count": 160, "current_count": 25,
    "probable_adds": [{"symbol": "NYKAA", "score": 12.3}],
    "probable_exits": [{"symbol": "SYRMA", "score": -4.2}],
    "holds": [{"symbol": "AETHER", "score": 8.0}] * 24,
}


def test_business_days_until():
    assert business_days_until(date(2026, 7, 3), date(2026, 7, 3)) == 0   # Fri, today
    assert business_days_until(date(2026, 7, 3), date(2026, 7, 6)) == 1   # Fri -> Mon
    assert business_days_until(date(2026, 7, 3), date(2026, 7, 7)) == 2   # Fri -> Tue
    assert business_days_until(date(2026, 7, 6), date(2026, 7, 3)) == 0   # past


def test_decide_kind_windows():
    due = date(2026, 7, 16)  # Thu
    assert decide_kind(date(2026, 7, 16), due, 2) == "orders"
    assert decide_kind(date(2026, 7, 15), due, 2) == "preview"   # 1 bd out
    assert decide_kind(date(2026, 7, 14), due, 2) == "preview"   # 2 bd out
    assert decide_kind(date(2026, 7, 13), due, 2) is None        # 3 bd out
    # after the due day the estimator returns the NEXT period, so overdue
    # never reaches decide_kind; a stale date must not fire again
    assert decide_kind(date(2026, 7, 17), due, 2) is None


def test_preview_message_contains_moves():
    msg = format_preview_message("Alpha", SLEEVE)
    assert "NYKAA" in msg and "SYRMA" in msg and "2026-07-16" in msg
    assert "<b>" in msg  # telegram HTML


def test_order_lines_quantities():
    lines = build_order_lines(SLEEVE, sleeve_value=250000.0, closes={"NYKAA": 190.0})
    sells = [x for x in lines if x["action"] == "SELL"]
    buys = [x for x in lines if x["action"] == "BUY"]
    assert sells[0]["symbol"] == "SYRMA" and sells[0]["qty"] == "ALL"
    # 25 target names -> 10k per name -> 52 shares at 190
    assert buys[0]["qty"] == 52 and buys[0]["est_value"] == 52 * 190.0


def test_order_lines_missing_price_falls_back():
    lines = build_order_lines(SLEEVE, sleeve_value=250000.0, closes={})
    buy = [x for x in lines if x["action"] == "BUY"][0]
    assert buy["qty"] is None and buy["est_value"] == 10000.0


def test_orders_message_and_csv():
    lines = build_order_lines(SLEEVE, 250000.0, {"NYKAA": 190.0})
    msg = format_orders_message("Alpha", SLEEVE, lines)
    assert "BUY" in msg and "SELL" in msg and "DELIVERY" in msg
    csv = orders_csv("Alpha", [(SLEEVE, lines)])
    assert csv.splitlines()[0].startswith("portfolio,sleeve,action")
    assert any("NYKAA" in ln for ln in csv.splitlines()[1:])
