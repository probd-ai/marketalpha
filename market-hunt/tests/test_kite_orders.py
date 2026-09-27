# Kite order placement. No network calls anywhere.
#
# Field names verified 2026-09-06 against kite.trade/docs/connect/v3/orders/:
#   POST /orders/regular   params: tradingsymbol, exchange, transaction_type,
#                                  order_type, quantity, product, validity, price
#   success -> {"status":"success","data":{"order_id":"..."}}
#   DELETE /orders/{variety}/{order_id}
#   GET /orders            fields: order_id, status, filled_quantity, pending_quantity
#
# Kite orders by TRADINGSYMBOL, Dhan by numeric securityId. The signature below
# keeps Dhan's `security_id` name so order_execution stays broker-agnostic; the
# adapter is where that difference is absorbed and it must not leak upward.
from unittest import mock

import pytest

from brokers.base import BrokerError
from brokers.kite import KiteClient


def _resp(payload, status=200):
    m = mock.Mock()
    m.status_code = status
    m.json.return_value = payload
    return m


def _client(monkeypatch):
    c = KiteClient()
    c.api_key, c.api_secret = "k", "s"
    monkeypatch.setattr("brokers.config.read_cached_token", lambda name: "tok")
    return c


OK = {"status": "success", "data": {"order_id": "151220000000000"}}


def test_place_order_posts_the_documented_fields(monkeypatch):
    with mock.patch("brokers.kite.requests.post", return_value=_resp(OK)) as post:
        res = _client(monkeypatch).place_order(
            security_id="TCS", side="BUY", quantity=7,
            order_type="LIMIT", price=3850.5, product="CNC")
    url, kwargs = post.call_args[0][0], post.call_args[1]
    assert url.endswith("/orders/regular")
    sent = kwargs["data"]
    assert sent["tradingsymbol"] == "TCS"       # security_id -> tradingsymbol
    assert sent["exchange"] == "NSE"
    assert sent["transaction_type"] == "BUY"
    assert sent["order_type"] == "LIMIT"
    assert sent["quantity"] == 7
    assert sent["product"] == "CNC"
    assert sent["validity"] == "DAY"
    assert sent["price"] == 3850.5
    assert res["order_id"] == "151220000000000"


def test_market_order_sends_no_price(monkeypatch):
    with mock.patch("brokers.kite.requests.post", return_value=_resp(OK)) as post:
        _client(monkeypatch).place_order(security_id="TCS", side="SELL", quantity=1,
                                         order_type="MARKET", price=0, product="CNC")
    assert post.call_args[1]["data"].get("price") in (0, None)


def test_only_delivery_product_is_permitted(monkeypatch):
    """Intraday would change the risk profile entirely — refuse before the wire."""
    with pytest.raises(BrokerError):
        _client(monkeypatch).place_order(security_id="TCS", side="BUY", quantity=1,
                                         order_type="LIMIT", price=1.0, product="MIS")


def test_invalid_side_is_refused_before_the_wire(monkeypatch):
    with mock.patch("brokers.kite.requests.post") as post:
        with pytest.raises(BrokerError):
            _client(monkeypatch).place_order(security_id="TCS", side="SHORT", quantity=1,
                                             order_type="LIMIT", price=1.0, product="CNC")
    post.assert_not_called()


def test_api_error_becomes_brokererror_without_credentials(monkeypatch):
    c = _client(monkeypatch)
    c.api_key = "SUPER_SECRET_KEY"
    err = {"status": "error", "message": "Insufficient funds"}
    with mock.patch("brokers.kite.requests.post", return_value=_resp(err, status=400)):
        with pytest.raises(BrokerError) as e:
            c.place_order(security_id="TCS", side="BUY", quantity=1,
                          order_type="LIMIT", price=1.0, product="CNC")
    assert "SUPER_SECRET_KEY" not in str(e.value)
    assert "tok" not in str(e.value)


def test_expired_session_on_order_drops_the_cached_token(monkeypatch):
    c = _client(monkeypatch)
    dropped = []
    monkeypatch.setattr("brokers.config.drop_cached_token", lambda n: dropped.append(n))
    with mock.patch("brokers.kite.requests.post", return_value=_resp({}, status=403)):
        with pytest.raises(BrokerError):
            c.place_order(security_id="TCS", side="BUY", quantity=1,
                          order_type="LIMIT", price=1.0, product="CNC")
    assert dropped   # a dead daily token must be evicted, not retried forever


ORDERS = {"status": "success", "data": [
    {"order_id": "1", "tradingsymbol": "TCS", "transaction_type": "SELL",
     "status": "COMPLETE", "quantity": 10, "filled_quantity": 10,
     "average_price": 3851.0},
    {"order_id": "2", "tradingsymbol": "INFY", "transaction_type": "BUY",
     "status": "OPEN", "quantity": 5, "filled_quantity": 2, "average_price": 1500.0},
]}


def test_orders_today_matches_the_shape_refresh_status_expects(monkeypatch):
    """refresh_sync_plan_status reads order_id / status / traded_qty generically
    across brokers — Kite must produce the same keys as Dhan."""
    with mock.patch("brokers.kite.requests.get", return_value=_resp(ORDERS)):
        rows = _client(monkeypatch).orders_today()
    assert [r["order_id"] for r in rows] == ["1", "2"]
    assert rows[0]["status"] == "COMPLETE" and rows[0]["traded_qty"] == 10
    assert rows[1]["traded_qty"] == 2          # partial fill surfaces as traded_qty
    assert rows[0]["symbol"] == "TCS" and rows[0]["side"] == "SELL"


def test_cancel_order_uses_delete_on_the_variety_path(monkeypatch):
    with mock.patch("brokers.kite.requests.delete", return_value=_resp(OK)) as dele:
        res = _client(monkeypatch).cancel_order("151220000000000")
    assert dele.call_args[0][0].endswith("/orders/regular/151220000000000")
    assert res["order_id"] == "151220000000000"
