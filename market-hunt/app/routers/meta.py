"""Root + health endpoints (moved verbatim from api_server)."""
import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db import mongo_conn

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def root():
    return {"message": "Market Hunt API Server", "version": "1.0.0", "status": "operational"}


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test MongoDB connection
        if mongo_conn.db is not None:
            mongo_conn.db.command("ping")
            db_status = "connected"
        else:
            db_status = "disconnected"

        return JSONResponse(content={
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": db_status
        })

    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
        )
