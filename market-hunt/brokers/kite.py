"""Zerodha Kite Connect client — reads (holdings / margins / profile) AND live orders.

⚠ `place_order` / `cancel_order` below send REAL orders to a funded account. They
are reachable only via an approved sync-plan; see brokers/__init__.py for the gates.

Auth model: KITE_API_KEY + KITE_API_SECRET in the Windows Credential Manager
(scripts/credentials.py) — NOT .env, no file fallback. The access token is DAILY:
user opens https://kite.zerodha.com/connect/login?v=3&api_key=<key>, Zerodha
redirects with a request_token, the server exchanges it (checksum =
sha256(api_key + request_token + api_secret)) and caches the access token in
the Credential Manager. The api_secret never leaves the machine; tokens are
never returned to the browser or logged. Kite's daily expiry (~07:30 IST) means
a stolen access token is self-limiting; KITE_API_SECRET is the durable one.
"""
import hashlib

import requests

from . import config
from .base import BrokerError, Funds, Holding

API = "https://api.kite.trade"
TIMEOUT = 15
TOKEN_CACHE = "kite_access_token"


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class KiteClient:
    name = "kite"

    def __init__(self):
        self.api_key = config.get_secret("KITE_API_KEY")
        self.api_secret = config.get_secret("KITE_API_SECRET")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def session_active(self) -> bool:
        return bool(config.read_cached_token(TOKEN_CACHE))

    def login_url(self) -> str:
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={self.api_key}"

    def create_session(self, request_token: str) -> dict:
        """Exchange request_token -> access_token (cached encrypted). Returns
        only non-secret session info."""
        if not self.configured:
            raise BrokerError("Kite not configured (KITE_API_KEY / KITE_API_SECRET missing)")
        checksum = hashlib.sha256(
            f"{self.api_key}{request_token}{self.api_secret}".encode()).hexdigest()
        try:
            r = requests.post(f"{API}/session/token",
                              headers={"X-Kite-Version": "3"},
                              data={"api_key": self.api_key,
                                    "request_token": request_token,
                                    "checksum": checksum},
                              timeout=TIMEOUT)
        except requests.RequestException as e:
            raise BrokerError(f"Kite network error: {e}")
        body = self._body(r)
        data = body.get("data") or {}
        token = data.get("access_token")
        if not token:
            raise BrokerError("Kite session exchange returned no token")
        config.cache_token(TOKEN_CACHE, token)
        return {"user_id": data.get("user_id"), "user_name": data.get("user_name"),
                "login_time": data.get("login_time")}

    def logout(self) -> None:
        config.drop_cached_token(TOKEN_CACHE)

    # ---- read-only data ----------------------------------------------------

    def _get(self, path: str) -> dict:
        if not self.configured:
            raise BrokerError("Kite not configured")
        token = config.read_cached_token(TOKEN_CACHE)
        if not token:
            raise BrokerError("Kite session not active — complete the daily login")
        try:
            r = requests.get(f"{API}{path}",
                             headers={"X-Kite-Version": "3",
                                      "Authorization": f"token {self.api_key}:{token}"},
                             timeout=TIMEOUT)
        except requests.RequestException as e:
            raise BrokerError(f"Kite network error: {e}")
        if r.status_code == 403:
            config.drop_cached_token(TOKEN_CACHE)  # expired daily token
            raise BrokerError("Kite session expired — repeat the daily login")
        return self._body(r)

    @staticmethod
    def _body(r) -> dict:
        try:
            body = r.json()
        except ValueError:
            raise BrokerError(f"Kite HTTP {r.status_code}: non-JSON response")
        if r.status_code != 200 or body.get("status") == "error":
            # error_type/message from Kite are safe (no secrets)
            raise BrokerError(f"Kite HTTP {r.status_code}: {body.get('message', 'error')}")
        return body

    def holdings(self) -> list[Holding]:
        rows = self._get("/portfolio/holdings").get("data") or []
        out = []
        for h in rows:
            # Kite: `quantity` is realised T+2 (tradeable), `t1_quantity` is still
            # settling, `collateral_quantity` is pledged. NB the pledged field is
            # "collateral_quantity" — there is no "pledged_quantity" in the API.
            realised = _num(h.get("quantity"), 0)
            pending = _num(h.get("t1_quantity"), 0) or 0
            pledged = _num(h.get("collateral_quantity"), 0) or 0
            qty = realised + pending          # total position — UNCHANGED behaviour
            avg = _num(h.get("average_price"), 0)
            ltp = _num(h.get("last_price"))
            mv = round(qty * ltp, 2) if ltp is not None else None
            pnl = _num(h.get("pnl"))
            cost = qty * avg
            out.append(Holding(
                symbol=h.get("tradingsymbol", ""), exchange=h.get("exchange", "NSE"),
                quantity=qty, avg_price=avg, last_price=ltp, market_value=mv, pnl=pnl,
                pnl_percent=round(pnl / cost * 100, 2) if pnl is not None and cost else None,
                isin=h.get("isin") or "",
                # Pledged stock cannot be sold. If collateral turns out NOT to be
                # counted inside `quantity`, this under-reports sellable — the safe
                # direction, since it can only shrink a SELL, never oversize one.
                # Confirm against a live pledged holding (plan Task 10).
                sellable_qty=max(realised - pledged, 0),
                pending_qty=pending, pledged_qty=pledged))
        return out

    # ---- order placement ---------------------------------------------------
    #
    # ⚠ These send REAL orders. Every safety guard (kill switch, human approval,
    # value caps, market hours, dry-run default) lives in order_execution.py and
    # app/routers/broker.py — this layer only talks to the API.

    def _write(self, method: str, path: str, data: dict | None = None) -> dict:
        if not self.configured:
            raise BrokerError("Kite not configured")
        token = config.read_cached_token(TOKEN_CACHE)
        if not token:
            raise BrokerError("Kite session not active — complete the daily login")
        fn = getattr(requests, method)
        try:
            kw = {"headers": {"X-Kite-Version": "3",
                              "Authorization": f"token {self.api_key}:{token}"},
                  "timeout": TIMEOUT}
            if data is not None:
                kw["data"] = data
            r = fn(f"{API}{path}", **kw)
        except requests.RequestException as e:
            raise BrokerError(f"Kite network error: {e}")
        if r.status_code == 403:
            config.drop_cached_token(TOKEN_CACHE)  # expired daily token
            raise BrokerError("Kite session expired — repeat the daily login")
        return self._body(r)

    def place_order(self, *, security_id: str, side: str, quantity: int,
                    order_type: str = "LIMIT", price: float = 0.0,
                    product: str = "CNC", validity: str = "DAY") -> dict:
        """Place one NSE equity DELIVERY order.

        `security_id` carries Kite's TRADINGSYMBOL — the parameter keeps Dhan's
        name so order_execution can stay broker-agnostic. Field names per
        kite.trade/docs/connect/v3/orders/ (POST /orders/regular).
        """
        if side not in ("BUY", "SELL"):
            raise BrokerError(f"invalid side: {side}")
        if product != "CNC":
            raise BrokerError("only CNC (delivery) orders are permitted")
        payload = {
            "tradingsymbol": str(security_id),
            "exchange": "NSE",
            "transaction_type": side,
            "order_type": order_type,
            "quantity": int(quantity),
            "product": "CNC",
            "validity": validity,
        }
        if order_type == "LIMIT":
            payload["price"] = float(price)
        body = self._write("post", "/orders/regular", payload)
        data = body.get("data") or {}
        return {"order_id": data.get("order_id"), "order_status": None}

    def cancel_order(self, order_id: str, variety: str = "regular") -> dict:
        body = self._write("delete", f"/orders/{variety}/{order_id}")
        return {"order_id": (body.get("data") or {}).get("order_id") or order_id}

    def orders_today(self) -> list[dict]:
        """Today's order book, normalized to the same keys Dhan emits so
        refresh_sync_plan_status stays broker-agnostic."""
        rows = self._get("/orders").get("data") or []
        return [{
            "order_id": o.get("order_id"),
            "symbol": o.get("tradingsymbol"),
            "side": o.get("transaction_type"),
            "status": o.get("status"),
            "qty": o.get("quantity"),
            "traded_qty": o.get("filled_quantity") or 0,
            "avg_price": o.get("average_price"),
        } for o in rows]

    def funds(self) -> Funds:
        eq = (self._get("/user/margins").get("data") or {}).get("equity") or {}
        avail = eq.get("available") or {}
        used = eq.get("utilised") or {}
        return Funds(available_cash=_num(avail.get("live_balance"),
                                         _num(avail.get("cash"), 0.0)),
                     margin_used=_num(used.get("debits")),
                     withdrawable=_num(avail.get("cash")))

    def profile(self) -> dict:
        d = self._get("/user/profile").get("data") or {}
        return {"user_id": d.get("user_id"), "user_name": d.get("user_name"),
                "email": d.get("email"), "broker": "kite"}
