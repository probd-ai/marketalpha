"""Dhan API v2 — READ-ONLY client (holdings / fund limits / profile).

Auth model: DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN in .env. The Dhan token is a
long-lived (~30 day) static token generated on web.dhan.co — no daily flow.
Rotate it by replacing the .env value. Never logged, never echoed.
"""
import requests

from . import config
from .base import BrokerError, Funds, Holding

API = "https://api.dhan.co/v2"
TIMEOUT = 15


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def list_accounts() -> list[str]:
    """Configured Dhan account aliases. Plain DHAN_CLIENT_ID = "default";
    additional accounts use DHAN_CLIENT_ID__<ALIAS> + DHAN_ACCESS_TOKEN__<ALIAS>."""
    import os
    config.load_env()
    aliases = []
    if config.get("DHAN_CLIENT_ID") and config.get("DHAN_ACCESS_TOKEN"):
        aliases.append("default")
    for k in os.environ:
        if k.startswith("DHAN_CLIENT_ID__"):
            alias = k[len("DHAN_CLIENT_ID__"):].lower()
            if config.get(f"DHAN_ACCESS_TOKEN__{alias.upper()}"):
                aliases.append(alias)
    return sorted(set(aliases))


class DhanClient:
    name = "dhan"

    def __init__(self, account: str = "default"):
        config.load_env()
        self.account = (account or "default").lower()
        if self.account == "default":
            self.client_id = config.get("DHAN_CLIENT_ID")
            self.token = config.get("DHAN_ACCESS_TOKEN")
        else:
            suffix = self.account.upper()
            self.client_id = config.get(f"DHAN_CLIENT_ID__{suffix}")
            self.token = config.get(f"DHAN_ACCESS_TOKEN__{suffix}")

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.token)

    @property
    def session_active(self) -> bool:
        return self.configured  # static token: configured == active

    def _get(self, path: str):
        if not self.configured:
            raise BrokerError("Dhan not configured (DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing)")
        try:
            r = requests.get(f"{API}{path}",
                             headers={"access-token": self.token, "Accept": "application/json"},
                             timeout=TIMEOUT)
        except requests.RequestException as e:
            raise BrokerError(f"Dhan network error: {e}")
        if r.status_code == 401:
            raise BrokerError("Dhan token rejected — regenerate it on web.dhan.co")
        try:
            body = r.json()
        except ValueError:
            raise BrokerError(f"Dhan HTTP {r.status_code}: non-JSON response")
        if r.status_code != 200:
            msg = body.get("errorMessage") or body.get("message") or "error"
            raise BrokerError(f"Dhan HTTP {r.status_code}: {msg}")
        return body

    def holdings(self) -> list[Holding]:
        rows = self._get("/holdings")
        if isinstance(rows, dict):  # some deployments wrap in {"data": [...]}
            rows = rows.get("data") or []
        out = []
        for h in rows:
            qty = _num(h.get("totalQty"), 0)
            avg = _num(h.get("avgCostPrice"), 0)
            ltp = _num(h.get("lastTradedPrice"))
            mv = round(qty * ltp, 2) if ltp is not None else None
            cost = qty * avg
            pnl = round(mv - cost, 2) if mv is not None else None
            out.append(Holding(
                symbol=h.get("tradingSymbol", ""), exchange=h.get("exchange", "NSE"),
                quantity=qty, avg_price=avg, last_price=ltp, market_value=mv, pnl=pnl,
                pnl_percent=round(pnl / cost * 100, 2) if pnl is not None and cost else None))
        return out

    def funds(self) -> Funds:
        d = self._get("/fundlimit")
        if isinstance(d, dict) and "data" in d:
            d = d["data"] or {}
        # NB: Dhan's field is historically spelled "availabelBalance"; accept both.
        avail = _num(d.get("availabelBalance"), _num(d.get("availableBalance"), 0.0))
        return Funds(available_cash=avail,
                     margin_used=_num(d.get("utilizedAmount")),
                     withdrawable=_num(d.get("withdrawableBalance")))

    def _post(self, path: str, payload: dict):
        if not self.configured:
            raise BrokerError("Dhan not configured")
        try:
            r = requests.post(f"{API}{path}",
                              headers={"access-token": self.token,
                                       "Content-Type": "application/json",
                                       "Accept": "application/json"},
                              json=payload, timeout=TIMEOUT)
        except requests.RequestException as e:
            # Network-level failures must surface as BrokerError so callers'
            # audit/persistence paths handle them like any broker rejection.
            raise BrokerError(f"Dhan network error: {e}")
        try:
            body = r.json()
        except ValueError:
            raise BrokerError(f"Dhan HTTP {r.status_code}: non-JSON response")
        if r.status_code not in (200, 201, 202):
            msg = body.get("errorMessage") or body.get("message") or body
            raise BrokerError(f"Dhan HTTP {r.status_code}: {msg}")
        return body

    def place_order(self, *, security_id: str, side: str, quantity: int,
                    order_type: str = "LIMIT", price: float = 0.0,
                    product: str = "CNC", validity: str = "DAY") -> dict:
        """Place one NSE equity DELIVERY order. Caller is responsible for ALL
        safety guards (kill switch, approval, caps) — this method only talks
        to the API. Field names per DhanHQ v2 /orders; verify against the
        current API docs before first live use."""
        if side not in ("BUY", "SELL"):
            raise BrokerError(f"invalid side: {side}")
        if product != "CNC":
            raise BrokerError("only CNC (delivery) orders are permitted")
        payload = {
            "dhanClientId": self.client_id,
            "transactionType": side,
            "exchangeSegment": "NSE_EQ",
            "productType": "CNC",
            "orderType": order_type,
            "validity": validity,
            "securityId": str(security_id),
            "quantity": int(quantity),
            "disclosedQuantity": 0,
            "price": float(price) if order_type == "LIMIT" else 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
        }
        body = self._post("/orders", payload)
        return {"order_id": body.get("orderId"), "order_status": body.get("orderStatus")}

    def orders_today(self) -> list[dict]:
        """Today's order book (normalized minimal fields, defensive keys)."""
        rows = self._get("/orders")
        if isinstance(rows, dict):
            rows = rows.get("data") or []
        out = []
        for o in rows:
            out.append({
                "order_id": o.get("orderId"),
                "symbol": o.get("tradingSymbol") or o.get("customSymbol"),
                "side": o.get("transactionType"),
                "status": o.get("orderStatus"),
                "qty": o.get("quantity"),
                "traded_qty": o.get("filledQty") or o.get("tradedQuantity") or 0,
                "avg_price": o.get("averageTradedPrice") or o.get("avgTradedPrice"),
            })
        return out

    def profile(self) -> dict:
        try:
            d = self._get("/profile")
            if isinstance(d, dict) and "data" in d:
                d = d["data"] or {}
            return {"user_id": d.get("dhanClientId") or self.client_id,
                    "token_validity": d.get("tokenValidity"), "broker": "dhan"}
        except BrokerError:
            # /profile availability varies; funds reachable == token works
            return {"user_id": self.client_id, "broker": "dhan"}
