"""Central infrastructure settings for market-hunt.

Single source of truth for Mongo/CORS/server config. Values come from the
environment (.env loaded via brokers.config.load_env, never overriding real
process env); defaults preserve current local-dev behavior exactly.

Env vars:
  MH_MONGO_URI      - MongoDB connection string   (default mongodb://localhost:27017/)
  MH_DB_NAME        - database name               (default market_hunt)
  MH_API_HOST       - uvicorn bind host           (default 127.0.0.1)
  MH_API_PORT       - uvicorn bind port           (default 3001)
  MH_CORS_ORIGINS   - comma-separated allow-list  (default localhost 3000/3002/3300 http variants)

Secrets (broker keys, tokens) stay in brokers/config.py — do not add them here.
"""
import os

from brokers import config as _broker_config

_broker_config.load_env()

_DEFAULT_CORS = ",".join(
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in (3300, 3000, 3002)
)

MONGO_URI: str = os.environ.get("MH_MONGO_URI", "mongodb://localhost:27017/").strip()
DB_NAME: str = os.environ.get("MH_DB_NAME", "market_hunt").strip()
API_HOST: str = os.environ.get("MH_API_HOST", "127.0.0.1").strip()
API_PORT: int = int(os.environ.get("MH_API_PORT", "3001"))
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("MH_CORS_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()
]
