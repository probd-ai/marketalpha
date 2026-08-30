"""Batch indicator jobs + stored indicator endpoints (moved verbatim from api_server)."""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class BatchIndicatorRequest(BaseModel):
    symbols: List[str]
    indicator_type: str = "truevx"
    base_symbol: str = "Nifty 50"
    start_date: str = ""  # Empty string means full range
    end_date: str = ""    # Empty string means full range
    parameters: Dict[str, Any] = {"s1": 22, "m2": 66, "l3": 222}


@router.post("/api/indicators/batch")
async def submit_batch_indicator_job(request: BatchIndicatorRequest):
    """
    Submit a batch indicator calculation job
    """
    try:
        from batch_indicator_processor import submit_truevx_batch_job

        if request.indicator_type != "truevx":
            raise HTTPException(status_code=400, detail="Only TrueValueX indicator is currently supported for batch processing")

        if not request.symbols:
            raise HTTPException(status_code=400, detail="At least one symbol must be provided")

        # Determine date range - if not provided (empty string), get full range from base symbol data
        start_date = request.start_date
        end_date = request.end_date

        logger.info(f"🔍 Received request: start_date='{start_date}', end_date='{end_date}'")

        if not start_date or not end_date:
            logger.info(f"📅 Empty dates received, determining full range for {request.base_symbol}")
            # Get full data range for base symbol
            try:
                from stock_data_manager import StockDataManager
                from datetime import datetime

                async with StockDataManager() as data_manager:
                    base_symbol_range = await data_manager.get_symbol_date_range(request.base_symbol)
                    if base_symbol_range:
                        if not start_date:
                            start_date = base_symbol_range['earliest'].strftime('%Y-%m-%d')
                        if not end_date:
                            end_date = base_symbol_range['latest'].strftime('%Y-%m-%d')
                        logger.info(f"📅 Using full data range for {request.base_symbol}: {start_date} to {end_date}")
                    else:
                        # Fallback to default range if no data found
                        start_date = start_date or "2020-01-01"
                        end_date = end_date or datetime.now().strftime('%Y-%m-%d')
                        logger.warning(f"⚠️ No data range found for {request.base_symbol}, using fallback: {start_date} to {end_date}")
            except Exception as e:
                from datetime import datetime
                logger.error(f"❌ Failed to get data range for {request.base_symbol}: {e}")
                # Fallback to default range
                start_date = start_date or "2020-01-01"
                end_date = end_date or datetime.now().strftime('%Y-%m-%d')
                logger.warning(f"🔄 Using fallback dates: {start_date} to {end_date}")

        logger.info(f"📋 Final dates for job: {start_date} to {end_date}")

        # Submit the batch job
        job_id = await submit_truevx_batch_job(
            symbols=request.symbols,
            base_symbol=request.base_symbol,
            start_date=start_date,
            end_date=end_date,
            s1=request.parameters.get("s1", 22),
            m2=request.parameters.get("m2", 66),
            l3=request.parameters.get("l3", 222)
        )

        logger.info(f"✅ Submitted batch job {job_id} for {len(request.symbols)} symbols")

        return JSONResponse(content={
            "job_id": job_id,
            "status": "submitted",
            "symbols": request.symbols,
            "indicator_type": request.indicator_type,
            "base_symbol": request.base_symbol,
            "parameters": request.parameters,
            "estimated_completion_minutes": len(request.symbols) * 0.5  # Rough estimate
        })

    except Exception as e:
        logger.error(f"❌ Failed to submit batch job: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to submit batch job: {str(e)}")


@router.get("/api/indicators/batch/{job_id}")
async def get_batch_job_progress(job_id: str):
    """
    Get batch job progress and status
    """
    try:
        from batch_indicator_processor import get_batch_job_progress

        progress = await get_batch_job_progress(job_id)

        if "error" in progress:
            raise HTTPException(status_code=404, detail=progress["error"])

        return JSONResponse(content=progress)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to get job progress: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get job progress: {str(e)}")


@router.delete("/api/indicators/batch/{job_id}")
async def cancel_batch_job(job_id: str):
    """
    Cancel a running batch job
    """
    try:
        from batch_indicator_processor import cancel_batch_job

        success = await cancel_batch_job(job_id)

        if success:
            return JSONResponse(content={
                "job_id": job_id,
                "status": "cancelled",
                "message": "Job cancelled successfully"
            })
        else:
            raise HTTPException(status_code=404, detail="Job not found or not cancellable")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to cancel job: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel job: {str(e)}")


@router.get("/api/indicators/batch")
async def list_batch_jobs():
    """
    List all recent batch jobs
    """
    try:
        from batch_indicator_processor import list_all_batch_jobs

        jobs = await list_all_batch_jobs()

        return JSONResponse(content={
            "total_jobs": len(jobs),
            "jobs": jobs
        })

    except Exception as e:
        logger.error(f"❌ Failed to list batch jobs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list batch jobs: {str(e)}")


@router.get("/api/indicators/stored")
async def get_stored_indicators():
    """
    Get list of all stored pre-calculated indicators
    """
    try:
        from indicator_data_manager import IndicatorDataManager

        async with IndicatorDataManager() as data_manager:
            indicators = await data_manager.get_available_indicators()

            return JSONResponse(content={
                "total_indicators": len(indicators),
                "indicators": indicators
            })

    except Exception as e:
        logger.error(f"❌ Failed to get stored indicators: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get stored indicators: {str(e)}")


@router.get("/api/indicators/stored/{symbol}/{indicator_type}/{base_symbol}")
async def get_stored_indicator_data(symbol: str, indicator_type: str, base_symbol: str,
                                   start_date: Optional[str] = None, end_date: Optional[str] = None):
    """
    Get stored indicator data for a specific symbol
    """
    try:
        from indicator_data_manager import IndicatorDataManager

        async with IndicatorDataManager() as data_manager:
            data = await data_manager.get_indicator_data(
                symbol=symbol,
                indicator_type=indicator_type,
                base_symbol=base_symbol,
                start_date=start_date,
                end_date=end_date
            )

            return JSONResponse(content={
                "symbol": symbol,
                "indicator_type": indicator_type,
                "base_symbol": base_symbol,
                "total_points": len(data),
                "date_range": {
                    "start": start_date,
                    "end": end_date
                },
                "data": data
            })

    except Exception as e:
        logger.error(f"❌ Failed to get stored indicator data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get stored indicator data: {str(e)}")
