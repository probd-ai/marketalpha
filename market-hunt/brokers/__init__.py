"""Broker connectivity layer — reads (holdings/funds/profile) AND live order placement.

⚠ THIS PACKAGE PLACES REAL ORDERS. `DhanClient.place_order` sends to a funded account.
(Kite is read-only for now — it has no place_order yet.) Stage 2 has shipped; this
docstring previously claimed no order code existed, which was false and dangerous to
trust. Do not reintroduce that claim.

The gate is NOT the absence of order code. Every real order must clear ALL of these,
enforced in `order_execution.py` and `app/routers/broker.py` (do not weaken any):
- kill switch  : MH_TRADING_ENABLED=true
- approval     : a human explicitly approved the plan (draft -> approved)
- freshness    : the plan is < 24h old
- funding      : the plan is feasible (buys covered by cash + sell proceeds)
- caps         : per-order (MH_MAX_ORDER_VALUE) and per-run (MH_MAX_EXECUTION_VALUE)
- market hours : NSE 09:15-15:30 IST, weekdays
- dry run      : /execute defaults to dry_run=true; a real send is opt-in
- lock + audit : atomic claim against double-punching; every attempt logged

Security invariants (do not weaken):
- No credentials in code or repo: everything comes from .env / process env.
- Access tokens cached only encrypted (Fernet) under .secrets/ (gitignored).
- No secret ever appears in a log line or an HTTP response body.
- No order API is reachable with a caller-supplied symbol/quantity/price: orders are
  built only from an approved plan, so a model can never fabricate one.
"""
from .base import BrokerError, Holding, Funds  # noqa: F401
from .kite import KiteClient  # noqa: F401
from .dhan import DhanClient, list_accounts as list_dhan_accounts  # noqa: F401
