"""Single shared MongoDB layer.

One pooled client per driver for the whole process:
  sync_db   - pymongo Database. Legacy: existing route bodies use blocking
              pymongo; they keep working through this handle while modules
              migrate. Do not add new call sites.
  motor_db  - motor (async) Database. Target driver: all migrated/new code
              awaits through this handle.

connect() fails loud (pings the server) — a wrong number is worse than no
number, and a silently-down DB is the same bug.
"""
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

logger = logging.getLogger(__name__)

# Fail within 5s if Mongo is unreachable instead of pymongo's 30s default.
_SERVER_SELECTION_TIMEOUT_MS = 5000


class Database:
    """Owns exactly one sync and one async client; connect once at startup."""

    def __init__(self) -> None:
        self._sync_client: MongoClient | None = None
        self._motor_client: AsyncIOMotorClient | None = None
        self._db_name: str | None = None

    def connect(self, uri: str, db_name: str) -> None:
        """Create both clients (idempotent) and verify the server is reachable."""
        if self._sync_client is not None:
            return
        sync_client = MongoClient(uri, serverSelectionTimeoutMS=_SERVER_SELECTION_TIMEOUT_MS)
        try:
            sync_client.admin.command("ping")  # fail loud before serving traffic
        except Exception:
            sync_client.close()
            raise
        self._sync_client = sync_client
        self._motor_client = AsyncIOMotorClient(
            uri, serverSelectionTimeoutMS=_SERVER_SELECTION_TIMEOUT_MS
        )
        self._db_name = db_name
        logger.info("Mongo connected: db=%s", db_name)

    def close(self) -> None:
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None
        if self._motor_client is not None:
            self._motor_client.close()
            self._motor_client = None
        self._db_name = None

    @property
    def sync_db(self):
        if self._sync_client is None:
            raise RuntimeError("Database not connected — call connect() at startup")
        return self._sync_client[self._db_name]

    @property
    def motor_db(self):
        if self._motor_client is None:
            raise RuntimeError("Database not connected — call connect() at startup")
        return self._motor_client[self._db_name]


# Process-wide singleton; api_server lifespan connects/closes it.
database = Database()


class MongoDBConnection:
    """Legacy shim over `database` — existing `mongo_conn.db` call sites keep
    working while route groups migrate to the async motor handle.

    Semantics preserved from the original api_server class: `db` is None until
    connected (CLI scripts check `mongo_conn.db is None` and call connect()
    themselves; under uvicorn the lifespan connects before any request).
    """

    def connect(self):
        import settings
        database.connect(settings.MONGO_URI, settings.DB_NAME)
        return True

    def close(self):
        database.close()

    @property
    def client(self):
        db = self.db
        return db.client if db is not None else None

    @property
    def db(self):
        try:
            return database.sync_db
        except RuntimeError:
            return None


# Global legacy alias (lifespan owns connect/close under uvicorn)
mongo_conn = MongoDBConnection()
