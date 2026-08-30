"""app.middleware — request IDs, access logging, safe 500s."""
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import install_middleware


def make_app():
    app = FastAPI()
    install_middleware(app)

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise ValueError("secret internal detail")

    return app


def test_ok_response_carries_request_id():
    client = TestClient(make_app())
    r = client.get("/ok")
    assert r.status_code == 200
    assert r.headers.get("x-request-id")


def test_unhandled_exception_returns_json_500_without_internals():
    client = TestClient(make_app(), raise_server_exceptions=False)
    r = client.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"] == "Internal server error"
    assert body["request_id"] == r.headers["x-request-id"]
    # never leak exception text to the client
    assert "secret internal detail" not in r.text


def test_exception_is_logged_with_traceback(caplog):
    client = TestClient(make_app(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app.middleware"):
        client.get("/boom")
    joined = " ".join(rec.getMessage() for rec in caplog.records) + " ".join(
        str(rec.exc_info) for rec in caplog.records if rec.exc_info
    )
    assert "ValueError" in joined or any(rec.exc_info for rec in caplog.records)


def test_access_log_records_method_path_status(caplog):
    client = TestClient(make_app())
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        client.get("/ok")
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("GET" in m and "/ok" in m and "200" in m for m in messages)
