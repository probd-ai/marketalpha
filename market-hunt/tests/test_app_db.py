"""app.db — single shared Mongo layer (sync legacy + async motor target)."""
import pytest

from app.db import Database


def test_not_connected_fails_loud():
    db = Database()
    with pytest.raises(RuntimeError, match="not connected"):
        _ = db.sync_db
    with pytest.raises(RuntimeError, match="not connected"):
        _ = db.motor_db


def test_close_before_connect_is_noop():
    Database().close()  # must not raise


@pytest.mark.integration
def test_connect_exposes_both_drivers():
    import settings
    db = Database()
    db.connect(settings.MONGO_URI, settings.DB_NAME)
    try:
        # sync (legacy call sites): a real pymongo Database, ping works
        assert db.sync_db.name == settings.DB_NAME
        assert db.sync_db.command("ping")["ok"] == 1
        # async (target): a motor Database bound to the same name
        assert db.motor_db.name == settings.DB_NAME
    finally:
        db.close()


@pytest.mark.integration
def test_connect_is_idempotent_single_clients():
    import settings
    db = Database()
    db.connect(settings.MONGO_URI, settings.DB_NAME)
    sync_client_first = db.sync_db.client
    db.connect(settings.MONGO_URI, settings.DB_NAME)  # second call: no new clients
    try:
        assert db.sync_db.client is sync_client_first
    finally:
        db.close()


@pytest.mark.integration
def test_connect_bad_uri_fails_loud():
    db = Database()
    with pytest.raises(Exception):
        db.connect("mongodb://localhost:59999/?serverSelectionTimeoutMS=200", "nope")
    db.close()
