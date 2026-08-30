"""Zerodha Kite Connect — READ-ONLY client (holdings / margins / profile).

Auth model: KITE_API_KEY + KITE_API_SECRET in .env. The access token is DAILY:
user opens https://kite.zerodha.com/connect/login?v=3&api_key=<key>, Zerodha
redirects with a request_token, the server exchanges it (checksum =
sha256(api_key + request_token + api_secret)) and caches the access token
encrypted. The api_secret never leaves the server; tokens are never returned
to the browser or logged.
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
        config.load_env()
        self.api_key = config.get("KITE_API_KEY")
        self.api_secret = config.get("KITE_API_SECRET")

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
            qty = _num(h.get("quantity"), 0) + _num(h.get("t1_quantity"), 0)
            avg = _num(h.get("average_price"), 0)
            ltp = _num(h.get("last_price"))
            mv = round(qty * ltp, 2) if ltp is not None else None
            pnl = _num(h.get("pnl"))
            cost = qty * avg
            out.append(Holding(
                symbol=h.get("tradingsymbol", ""), exchange=h.get("exchange", "NSE"),
                quantity=qty, avg_price=avg, last_price=ltp, market_value=mv, pnl=pnl,
                pnl_percent=round(pnl / cost * 100, 2) if pnl is not None and cost else None))
        return out

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
