"""Request middleware: request IDs, access logging, safe error responses.

Every response carries X-Request-ID. Unhandled exceptions are logged with
traceback and returned as a generic JSON 500 that echoes the request id —
clients get a correlation handle, never internals.
"""
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "unhandled error rid=%s %s %s (%.1fms)",
                request_id, request.method, request.url.path, elapsed_ms,
                exc_info=True,
            )
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": request_id},
            )
        else:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "rid=%s %s %s -> %s (%.1fms)",
                request_id, request.method, request.url.path,
                response.status_code, elapsed_ms,
            )
        response.headers["X-Request-ID"] = request_id
        return response
