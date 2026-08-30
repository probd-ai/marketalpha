# Broker layer security + normalization tests. No network calls anywhere.
import os
from unittest import mock

import pytest

from brokers.base import BrokerError, Funds, Holding
from brokers.dhan import DhanClient
from brokers.kite import KiteClient


# ---------- auth guard ------------------------------------------------------

class _Req:
    def __init__(self, host):
        class C:  # noqa: N801
            pass
        self.client = C()
        self.client.host = host


def _guard(request, key=None):
    from app.routers.broker import _require_broker_auth
    return _require_broker_auth(request, key)


def test_guard_localhost_allowed_without_token(monkeypatch):
    monkeypatch.delenv("MH_API_TOKEN", raising=False)
    _guard(_Req("127.0.0.1"))  # no raise


def test_guard_remote_blocked_without_token(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.delenv("MH_API_TOKEN", raising=False)
    with pytest.raises(HTTPException) as e:
        _guard(_Req("203.0.113.7"))
    assert e.value.status_code == 403


def test_guard_token_mode_requires_exact_key(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv("MH_API_TOKEN", "s3cret")
    _guard(_Req("203.0.113.7"), "s3cret")  # remote OK with key
    with pytest.raises(HTTPException) as e:
        _guard(_Req("127.0.0.1"), "wrong")  # even localhost needs the key once set
    assert e.value.status_code == 401
    with pytest.raises(HTTPException):
        _guard(_Req("127.0.0.1"), None)


# ---------- normalization (mocked payloads, exact broker shapes) ------------

KITE_HOLDINGS = {"status": "success", "data": [{
    "tradingsymbol": "TCS", "exchange": "NSE", "quantity": 10, "t1_quantity": 2,
    "average_price": 3500.0, "last_price": 3850.0, "pnl": 4200.0}]}

KITE_MARGINS = {"status": "success", "data": {"equity": {
    "available": {"live_balance": 152000.5, "cash": 140000.0},
    "utilised": {"debits": 48000.0}}}}

DHAN_HOLDINGS = [{"tradingSymbol": "INFY", "exchange": "NSE", "totalQty": 5,
                  "avgCostPrice": 1400.0, "lastTradedPrice": 1500.0}]

DHAN_FUNDS = {"availabelBalance": 99000.0, "utilizedAmount": 1000.0,
              "withdrawableBalance": 98000.0}


def _resp(payload, status=200):
    m = mock.Mock()
    m.status_code = status
    m.json.return_value = payload
    return m


def test_kite_holdings_normalized(monkeypatch):
    c = KiteClient()
    c.api_key, c.api_secret = "k", "s"
    monkeypatch.setattr("brokers.config.read_cached_token", lambda name: "tok")
    with mock.patch("brokers.kite.requests.get", return_value=_resp(KITE_HOLDINGS)):
        [h] = c.holdings()
    assert h.symbol == "TCS" and h.quantity == 12  # quantity + t1
    assert h.market_value == 12 * 3850.0
    assert h.pnl == 4200.0 and h.pnl_percent == round(4200.0 / (12 * 3500.0) * 100, 2)


def test_kite_funds_normalized(monkeypatch):
    c = KiteClient()
    c.api_key, c.api_secret = "k", "s"
    monkeypatch.setattr("brokers.config.read_cached_token", lambda name: "tok")
    with mock.patch("brokers.kite.requests.get", return_value=_resp(KITE_MARGINS)):
        f = c.funds()
    assert f.available_cash == 152000.5 and f.margin_used == 48000.0


def test_kite_expired_session_drops_cache(monkeypatch):
    c = KiteClient()
    c.api_key, c.api_secret = "k", "s"
    dropped = []
    monkeypatch.setattr("brokers.config.read_cached_token", lambda name: "tok")
    monkeypatch.setattr("brokers.config.drop_cached_token", lambda name: dropped.append(name))
    with mock.patch("brokers.kite.requests.get", return_value=_resp({}, status=403)):
        with pytest.raises(BrokerError):
            c.holdings()
    assert dropped  # expired token must be evicted, not retried forever


def test_dhan_holdings_normalized():
    c = DhanClient()
    c.client_id, c.token = "cid", "tok"
    with mock.patch("brokers.dhan.requests.get", return_value=_resp(DHAN_HOLDINGS)):
        [h] = c.holdings()
    assert h.symbol == "INFY" and h.market_value == 7500.0 and h.pnl == 500.0


def test_dhan_funds_handles_misspelled_field():
    c = DhanClient()
    c.client_id, c.token = "cid", "tok"
    with mock.patch("brokers.dhan.requests.get", return_value=_resp(DHAN_FUNDS)):
        f = c.funds()
    assert f.available_cash == 99000.0 and f.withdrawable == 98000.0


# ---------- secrets hygiene --------------------------------------------------

def test_broker_errors_never_contain_credentials():
    c = DhanClient()
    c.client_id, c.token = "cid", "SECRET_TOKEN_VALUE"
    with mock.patch("brokers.dhan.requests.get",
                    return_value=_resp({"errorMessage": "bad request"}, status=400)):
        with pytest.raises(BrokerError) as e:
            c.holdings()
    assert "SECRET_TOKEN_VALUE" not in str(e.value)


def test_status_endpoint_never_returns_secrets(monkeypatch):
    import asyncio
    monkeypatch.setenv("KITE_API_KEY", "kkey")
    monkeypatch.setenv("KITE_API_SECRET", "ksecret")
    monkeypatch.setenv("DHAN_CLIENT_ID", "cid")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "dtok")
    monkeypatch.delenv("MH_API_TOKEN", raising=False)
    import app.routers.broker as broker_router
    resp = asyncio.run(broker_router.broker_status(_Req("127.0.0.1"), None))
    body = resp.body.decode()
    assert '"configured":true' in body.replace(" ", "")
    for secret in ("ksecret", "dtok"):
        assert secret not in body


def test_token_cache_roundtrip(tmp_path, monkeypatch):
    import brokers.config as bc
    monkeypatch.setattr(bc, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(bc, "ENV_PATH", tmp_path / ".env")
    (tmp_path / ".env").write_text("")
    monkeypatch.delenv("MH_TOKEN_KEY", raising=False)
    bc.cache_token("t", "hello-token")
    on_disk = (tmp_path / ".secrets" / "t.enc").read_bytes()
    assert b"hello-token" not in on_disk  # encrypted at rest
    assert bc.read_cached_token("t") == "hello-token"
    bc.drop_cached_token("t")
    assert bc.read_cached_token("t") is None
