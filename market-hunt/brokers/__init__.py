"""Broker connectivity layer — READ-ONLY by design (holdings/funds/profile).

Security invariants (do not weaken):
- No credentials in code or repo: everything comes from .env / process env.
- Access tokens cached only encrypted (Fernet) under .secrets/ (gitignored).
- No secret ever appears in a log line or an HTTP response body.
- There is deliberately NO order-placement code in this package (Stage 2 gate).
"""
from .base import BrokerError, Holding, Funds  # noqa: F401
from .kite import KiteClient  # noqa: F401
from .dhan import DhanClient, list_accounts as list_dhan_accounts  # noqa: F401
