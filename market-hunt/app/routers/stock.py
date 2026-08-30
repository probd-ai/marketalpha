"""Stock data endpoints: mappings, prices, downloads, indicators (moved verbatim from api_server)."""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import mongo_conn

logger = logging.getLogger(__name__)
router = APIRouter()


class StockDataRequest(BaseModel):
    """Request model for stock data operations"""
    symbol: Optional[str] = None
    symbols: Optional[List[str]] = None
    index_name: Optional[str] = None
    industry_name: Optional[str] = None
    start_date: Optional[str] = None  # ISO format: YYYY-MM-DD
    end_date: Optional[str] = None    # ISO format: YYYY-MM-DD
    force_refresh: Optional[bool] = False


class StockDataResponse(BaseModel):
    """Response model for stock data operations"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    errors: Optional[List[str]] = None


class IndicatorRequest(BaseModel):
    symbol: str
    indicator_type: str
    period: Optional[int] = 20
    fast_period: Optional[int] = 12
    slow_period: Optional[int] = 26
    signal_period: Optional[int] = 9
    std_dev: Optional[float] = 2.0
    base_symbol: Optional[str] = "Nifty 500"  # Default base for CRS
    lookback: Optional[List[int]] = None  # Multiple lookback periods for Dynamic Fibonacci
    start_date: Optional[str] = None  # ISO format: YYYY-MM-DD
    end_date: Optional[str] = None    # ISO format: YYYY-MM-DD
    price_field: Optional[str] = "close_price"  # Field to use for calculation

    # TrueValueX specific parameters
    s1: Optional[int] = None  # Alpha (short lookback)
    m2: Optional[int] = None  # Beta (mid lookback)
    l3: Optional[int] = None  # Gamma (long lookback)
    strength: Optional[int] = None  # Trend Strength (bars)
    w_long: Optional[float] = None  # Weight Long
    w_mid: Optional[float] = None   # Weight Mid
    w_short: Optional[float] = None # Weight Short
    deadband_frac: Optional[float] = None  # Deadband γ (fraction of range)
    min_deadband: Optional[float] = None   # Minimum Deadband

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


@router.get("/api/stock/mappings")
async def get_symbol_mappings(
    index_name: Optional[str] = None,
    industry: Optional[str] = None,
    mapped_only: bool = False
):
    """Get symbol mappings between index_meta and NSE scripcode"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        from stock_data_manager import StockDataManager

        async with StockDataManager() as manager:
            mappings = await manager.get_symbol_mappings(
                index_name=index_name,
                industry=industry,
                mapped_only=mapped_only
            )

            # Convert to dict for JSON serialization
            mappings_data = []
            for mapping in mappings:
                mapping_dict = {
                    "symbol": mapping.symbol,
                    "company_name": mapping.company_name,
                    "industry": mapping.industry,
                    "index_names": mapping.index_names,  # Now an array
                    "nse_scrip_code": mapping.nse_scrip_code,
                    "nse_symbol": mapping.nse_symbol,
                    "nse_name": mapping.nse_name,
                    "match_confidence": mapping.match_confidence,
                    "last_updated": mapping.last_updated.isoformat() if mapping.last_updated else None
                }
                mappings_data.append(mapping_dict)

            return JSONResponse(content={
                "total_mappings": len(mappings_data),
                "mapped_count": len([m for m in mappings_data if m["nse_scrip_code"] is not None]),
                "mappings": mappings_data
            })

    except Exception as e:
        logger.error(f"Error fetching symbol mappings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch symbol mappings: {str(e)}")


@router.post("/api/stock/mappings/refresh")
async def refresh_symbol_mappings():
    """Refresh symbol mappings from index_meta and NSE masters"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        from stock_data_manager import StockDataManager

        async with StockDataManager() as manager:
            result = await manager.refresh_symbol_mappings_from_index_meta()

            return JSONResponse(content={
                "success": True,
                "message": "Symbol mappings refreshed successfully",
                "result": result
            })

    except Exception as e:
        logger.error(f"Error refreshing symbol mappings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to refresh symbol mappings: {str(e)}")


@router.get("/api/stock/data/{symbol}")
async def get_stock_price_data(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = 1000  # Increased default limit
):
    """Get historical price data for a symbol"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        from stock_data_manager import StockDataManager

        # Parse dates
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        async with StockDataManager() as manager:
            # Get total count without limit for progress tracking
            total_count = await manager.get_price_data_count(
                symbol=symbol,
                start_date=start_dt,
                end_date=end_dt
            )

            # Get actual data with limit (sorted by date descending - newest first)
            price_data = await manager.get_price_data(
                symbol=symbol,
                start_date=start_dt,
                end_date=end_dt,
                limit=limit,
                sort_order=-1  # -1 for descending (newest first)
            )

            # Convert to dict for JSON serialization
            price_records = []
            for record in price_data:
                price_dict = {
                    "scrip_code": record.scrip_code,
                    "symbol": record.symbol,
                    "date": record.date.isoformat(),
                    "open_price": record.open_price,
                    "high_price": record.high_price,
                    "low_price": record.low_price,
                    "close_price": record.close_price,
                    "volume": record.volume,
                    "value": record.value,
                    "year_partition": record.year_partition
                }
                price_records.append(price_dict)

            return JSONResponse(content={
                "symbol": symbol,
                "total_records": total_count,  # Actual total count
                "returned_records": len(price_records),  # Records in this response
                "data": price_records
            })

    except Exception as e:
        logger.error(f"Error fetching price data for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch price data: {str(e)}")


@router.post("/api/stock/download")
async def download_stock_data(request: StockDataRequest, background_tasks: BackgroundTasks):
    """Download historical stock data for symbols, indexes, or industries"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        # Parse dates
        start_dt = datetime.fromisoformat(request.start_date) if request.start_date else None
        end_dt = datetime.fromisoformat(request.end_date) if request.end_date else None

        # Determine download type
        if request.symbol:
            # Single symbol download
            background_tasks.add_task(
                download_symbol_data_task,
                request.symbol,
                start_dt,
                end_dt,
                request.force_refresh
            )
            return JSONResponse(content={
                "success": True,
                "message": f"Started downloading data for symbol: {request.symbol}",
                "type": "symbol"
            })

        elif request.symbols:
            # Multiple symbols download
            background_tasks.add_task(
                download_symbols_data_task,
                request.symbols,
                start_dt,
                end_dt,
                request.force_refresh
            )
            return JSONResponse(content={
                "success": True,
                "message": f"Started downloading data for {len(request.symbols)} symbols",
                "type": "symbols"
            })

        elif request.index_name:
            # Index download
            background_tasks.add_task(
                download_index_data_task,
                request.index_name,
                start_dt,
                end_dt,
                request.force_refresh
            )
            return JSONResponse(content={
                "success": True,
                "message": f"Started downloading data for index: {request.index_name}",
                "type": "index"
            })

        elif request.industry_name:
            # Industry download
            background_tasks.add_task(
                download_industry_data_task,
                request.industry_name,
                start_dt,
                end_dt,
                request.force_refresh
            )
            return JSONResponse(content={
                "success": True,
                "message": f"Started downloading data for industry: {request.industry_name}",
                "type": "industry"
            })
        else:
            raise HTTPException(status_code=400, detail="Must specify symbol, symbols, index_name, or industry_name")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting stock data download: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start download: {str(e)}")


@router.get("/api/stock/statistics")
async def get_stock_data_statistics():
    """Get statistics about stored stock price data"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        from stock_data_manager import StockDataManager

        async with StockDataManager() as manager:
            stats = await manager.get_data_statistics()

            # Convert datetime objects to strings for JSON serialization
            if stats.get("date_range"):
                if stats["date_range"]["earliest"]:
                    stats["date_range"]["earliest"] = stats["date_range"]["earliest"].isoformat()
                if stats["date_range"]["latest"]:
                    stats["date_range"]["latest"] = stats["date_range"]["latest"].isoformat()

            return JSONResponse(content=stats)

    except Exception as e:
        logger.error(f"Error fetching stock data statistics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch statistics: {str(e)}")


@router.get("/api/stock/available")
async def get_available_symbols(
    index_name: Optional[str] = None,
    industry: Optional[str] = None,
    limit: Optional[int] = None,
    search: Optional[str] = None
):
    """Get all available symbols from the database with optional filtering"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        from stock_data_manager import StockDataManager

        async with StockDataManager() as manager:
            mappings = await manager.get_symbol_mappings(
                index_name=index_name,
                industry=industry,
                mapped_only=True  # Only return symbols with NSE mapping
            )

            # Convert to simplified format for frontend
            symbols_data = []
            for mapping in mappings:
                symbol_dict = {
                    "symbol": mapping.symbol,
                    "name": mapping.company_name,
                    "sector": mapping.industry,
                    "index_names": mapping.index_names,
                    "nse_symbol": mapping.nse_symbol,
                    "last_updated": mapping.last_updated.isoformat() if mapping.last_updated else None
                }
                symbols_data.append(symbol_dict)

            # Apply search filter if provided
            if search:
                search_lower = search.lower()
                symbols_data = [
                    s for s in symbols_data
                    if search_lower in s["symbol"].lower() or
                       search_lower in s["name"].lower() or
                       search_lower in (s["sector"] or "").lower()
                ]

            # Apply limit if provided
            if limit:
                symbols_data = symbols_data[:limit]

            # Sort by symbol name for consistent ordering
            symbols_data.sort(key=lambda x: x["symbol"])

            return JSONResponse(content={
                "success": True,
                "total": len(symbols_data),
                "symbols": symbols_data,
                "filters": {
                    "index_name": index_name,
                    "industry": industry,
                    "search": search,
                    "limit": limit
                }
            })

    except Exception as e:
        logger.error(f"Error fetching available symbols: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch available symbols: {str(e)}")


@router.post("/api/stock/check-gaps")
def check_stock_gaps(symbols: List[str]):
    """Get gap status for multiple symbols from the cached gap_status collection"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        gap_statuses = []
        for symbol in symbols:
            symbol = symbol.upper()

            # Get cached gap status from database
            gap_doc = mongo_conn.db["gap_status"].find_one({"symbol": symbol})

            if not gap_doc:
                # No cached status found, return default values
                gap_statuses.append({
                    "symbol": symbol,
                    "company_name": "Unknown",
                    "industry": "Unknown",
                    "index_names": [],
                    "nse_scrip_code": None,
                    "has_data": False,
                    "record_count": 0,
                    "date_range": {"start": None, "end": None},
                    "data_freshness_days": 0,
                    "coverage_percentage": 0,
                    "last_price": None,
                    "needs_update": True,
                    "gap_details": ["Gap status not calculated yet. Run 'update-gap-status' command."],
                    "last_calculated": None
                })
            else:
                # Convert cached status to response format
                gap_statuses.append({
                    "symbol": gap_doc["symbol"],
                    "company_name": gap_doc["company_name"],
                    "industry": gap_doc["industry"],
                    "index_names": gap_doc["index_names"],
                    "nse_scrip_code": gap_doc["nse_scrip_code"],
                    "has_data": gap_doc["has_data"],
                    "record_count": gap_doc["record_count"],
                    "date_range": gap_doc["date_range"],
                    "data_freshness_days": gap_doc["data_freshness_days"],
                    "coverage_percentage": gap_doc["coverage_percentage"],
                    "last_price": gap_doc.get("last_price"),
                    "needs_update": gap_doc["needs_update"],
                    "gap_details": gap_doc["gap_details"],
                    "last_calculated": gap_doc["last_calculated"].isoformat() if gap_doc.get("last_calculated") else None
                })

        return JSONResponse(content=gap_statuses)

    except Exception as e:
        logger.error(f"Error checking stock gaps: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to check gaps: {str(e)}")


@router.post("/api/stock/indicators")
async def calculate_stock_indicators(request: IndicatorRequest):
    """Calculate technical indicators for stock price data - Optimized"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        from stock_data_manager import StockDataManager
        from indicator_engine import IndicatorEngine

        # Optimize date range - if not provided, use smart defaults based on indicator period
        if not request.start_date or not request.end_date:
            end_dt = datetime.now()

            # Special handling for TrueValueX which needs much more data (l3=222 default)
            if request.indicator_type == 'truevx':
                # TrueValueX needs at least 222 points + buffer, so get ~2 years of data
                min_points_needed = 500  # Conservative estimate for TrueValueX
                days_needed = min_points_needed * 1.8  # Account for weekends/holidays
                start_dt = end_dt - pd.Timedelta(days=days_needed)
                logger.info(f"Auto-calculated TrueValueX date range: {start_dt.date()} to {end_dt.date()}")
            else:
                # Calculate minimum required data points (period * 3 for better SMA accuracy)
                min_points_needed = max(request.period * 3, 100)

                # Estimate days needed (accounting for weekends/holidays)
                days_needed = min_points_needed * 1.5
                start_dt = end_dt - pd.Timedelta(days=days_needed)

                logger.info(f"Auto-calculated date range: {start_dt.date()} to {end_dt.date()} for {request.period}-period indicator")
        else:
            start_dt = datetime.fromisoformat(request.start_date)
            end_dt = datetime.fromisoformat(request.end_date)

        # Get price data with optimized limit
        async with StockDataManager() as manager:
            # Calculate appropriate limit based on date range
            date_range_days = (end_dt - start_dt).days
            if date_range_days > 365 * 5:  # More than 5 years
                initial_limit = 50000  # Large limit for ALL timeframe
            elif date_range_days > 365:    # More than 1 year
                initial_limit = 20000  # Medium limit for 5Y timeframe
            else:
                initial_limit = 10000  # Small limit for 1Y timeframe

            logger.info(f"Using limit {initial_limit} for {date_range_days} days of data")

            price_data = await manager.get_price_data(
                symbol=request.symbol,
                start_date=start_dt,
                end_date=end_dt,
                limit=initial_limit,
                sort_order=1  # Ascending order for indicator calculation
            )

            if not price_data:
                raise HTTPException(status_code=404, detail=f"No price data found for symbol {request.symbol}")

            # Check if we have enough data for the indicator
            if len(price_data) < request.period:
                # Try to get more data by extending the date range
                extended_start = start_dt - pd.Timedelta(days=365)  # Go back 1 year
                logger.info(f"Insufficient data ({len(price_data)} points), extending range to {extended_start.date()}")

                price_data = await manager.get_price_data(
                    symbol=request.symbol,
                    start_date=extended_start,
                    end_date=end_dt,
                    limit=20000,
                    sort_order=1
                )

                if len(price_data) < request.period:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Insufficient data for {request.period}-period indicator. Found {len(price_data)} points, need at least {request.period}"
                    )

            # Convert price data to dict format for indicator engine (optimized)
            price_records = []
            for record in price_data:
                price_dict = {
                    "date": record.date.isoformat(),
                    "close_price": record.close_price,  # Always include close_price
                }

                # Only include other fields if needed
                if request.price_field != 'close_price':
                    price_dict[request.price_field] = getattr(record, request.price_field)

                # For MACD, Bollinger, CRS, Dynamic Fibonacci, and TrueValueX indicators, include OHLC
                if request.indicator_type in ['macd', 'bollinger', 'crs', 'dynamic_fib', 'truevx']:
                    price_dict.update({
                        "open_price": record.open_price,
                        "high_price": record.high_price,
                        "low_price": record.low_price,
                        "volume": record.volume,
                    })

                price_records.append(price_dict)

            # Initialize indicator engine
            engine = IndicatorEngine()

            # Prepare parameters for indicator calculation
            calc_params = {
                "period": request.period,
                "price_field": request.price_field
            }

            # Add specific parameters for different indicators
            if request.indicator_type == 'macd':
                calc_params.update({
                    "fast_period": request.fast_period,
                    "slow_period": request.slow_period,
                    "signal_period": request.signal_period
                })
            elif request.indicator_type == 'bollinger':
                calc_params.update({
                    "std_dev": request.std_dev
                })
            elif request.indicator_type == 'crs':
                calc_params.update({
                    "base_symbol": request.base_symbol,
                    "start_date": start_dt.isoformat() if start_dt else None,
                    "end_date": end_dt.isoformat() if end_dt else None
                })
            elif request.indicator_type == 'dynamic_fib':
                # Set default lookback periods if not provided
                lookback_periods = request.lookback if request.lookback else [22, 66, 222]
                calc_params.update({
                    "lookback": lookback_periods
                })
            elif request.indicator_type == 'truevx':
                calc_params.update({
                    "base_symbol": request.base_symbol or "Nifty 50",
                    "start_date": start_dt.isoformat() if start_dt else None,
                    "end_date": end_dt.isoformat() if end_dt else None
                })

            # Calculate indicator (now with caching and optimization)
            try:
                calculation_start = datetime.now()

                # Special handling for CRS and TrueValueX since they're async
                if request.indicator_type == 'crs':
                    indicator_data = await engine.calculate_crs(
                        data=price_records,
                        base_symbol=request.base_symbol,
                        start_date=start_dt.isoformat() if start_dt else None,
                        end_date=end_dt.isoformat() if end_dt else None
                    )
                elif request.indicator_type == 'truevx':
                    # Pass additional TrueValueX parameters
                    truevx_params = {}
                    if request.s1 is not None:
                        truevx_params['s1'] = request.s1
                    if request.m2 is not None:
                        truevx_params['m2'] = request.m2
                    if request.l3 is not None:
                        truevx_params['l3'] = request.l3
                    if request.strength is not None:
                        truevx_params['strength'] = request.strength
                    if request.w_long is not None:
                        truevx_params['w_long'] = request.w_long
                    if request.w_mid is not None:
                        truevx_params['w_mid'] = request.w_mid
                    if request.w_short is not None:
                        truevx_params['w_short'] = request.w_short
                    if request.deadband_frac is not None:
                        truevx_params['deadband_frac'] = request.deadband_frac
                    if request.min_deadband is not None:
                        truevx_params['min_deadband'] = request.min_deadband

                    indicator_data = await engine.calculate_truevx_ranking(
                        data=price_records,
                        base_symbol=request.base_symbol or "Nifty 50",
                        start_date=start_dt.isoformat() if start_dt else None,
                        end_date=end_dt.isoformat() if end_dt else None,
                        **truevx_params
                    )
                else:
                    indicator_data = engine.calculate_indicator(
                        indicator_type=request.indicator_type,
                        data=price_records,
                        **calc_params
                    )
                calculation_time = (datetime.now() - calculation_start).total_seconds()

                logger.info(f"Indicator calculation completed in {calculation_time:.3f}s")

            except ValueError as ve:
                raise HTTPException(status_code=400, detail=str(ve))

            return JSONResponse(content={
                "symbol": request.symbol,
                "indicator_type": request.indicator_type,
                "parameters": calc_params,
                "total_points": len(indicator_data),
                "price_data_points": len(price_records),
                "calculation_time_seconds": round(calculation_time, 3),
                "data": indicator_data
            })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating indicators for {request.symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to calculate indicators: {str(e)}")


@router.get("/api/stock/indicators/supported")
async def get_supported_indicators():
    """Get list of supported technical indicators"""
    try:
        from indicator_engine import IndicatorEngine

        engine = IndicatorEngine()
        supported = engine.get_supported_indicators()

        # Add metadata about each indicator
        indicator_info = {
            'sma': {
                'name': 'Simple Moving Average',
                'description': 'Average price over a specified period',
                'parameters': ['period', 'price_field'],
                'default_period': 20
            },
            'ema': {
                'name': 'Exponential Moving Average',
                'description': 'Weighted moving average giving more importance to recent prices',
                'parameters': ['period', 'price_field'],
                'default_period': 20
            },
            'rsi': {
                'name': 'Relative Strength Index',
                'description': 'Momentum oscillator measuring speed and magnitude of price changes',
                'parameters': ['period', 'price_field'],
                'default_period': 14,
                'range': [0, 100]
            },
            'macd': {
                'name': 'Moving Average Convergence Divergence',
                'description': 'Trend-following momentum indicator',
                'parameters': ['fast_period', 'slow_period', 'signal_period', 'price_field'],
                'default_periods': {'fast': 12, 'slow': 26, 'signal': 9}
            },
            'bollinger': {
                'name': 'Bollinger Bands',
                'description': 'Volatility bands around moving average',
                'parameters': ['period', 'std_dev', 'price_field'],
                'default_period': 20,
                'default_std_dev': 2.0
            },
            'truevx': {
                'name': 'TrueValueX Ranking',
                'description': 'Advanced ranking system with structural and trend analysis',
                'parameters': ['base_symbol', 'start_date', 'end_date'],
                'default_base_symbol': 'Nifty 50',
                'range': [50, 100]
            }
        }

        return JSONResponse(content={
            "supported_indicators": supported,
            "indicator_details": {k: v for k, v in indicator_info.items() if k in supported}
        })

    except Exception as e:
        logger.error(f"Error getting supported indicators: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get supported indicators: {str(e)}")


# Background task functions for stock data downloads
async def download_symbol_data_task(symbol: str, start_date: datetime, end_date: datetime, force_refresh: bool):
    """Background task to download data for a single symbol"""
    try:
        from stock_data_manager import StockDataManager

        async with StockDataManager() as manager:
            result = await manager.download_historical_data_for_symbol(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                force_refresh=force_refresh
            )
            logger.info(f"✅ Background download completed for symbol {symbol}: {result}")

    except Exception as e:
        logger.error(f"❌ Background download failed for symbol {symbol}: {e}")


async def download_symbols_data_task(symbols: List[str], start_date: datetime, end_date: datetime, force_refresh: bool):
    """Background task to download data for multiple symbols"""
    try:
        from stock_data_manager import StockDataManager

        async with StockDataManager() as manager:
            for symbol in symbols:
                try:
                    result = await manager.download_historical_data_for_symbol(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date,
                        force_refresh=force_refresh
                    )
                    logger.info(f"✅ Downloaded data for symbol {symbol}: {result}")
                    await asyncio.sleep(1)  # Rate limiting
                except Exception as e:
                    logger.error(f"❌ Failed to download data for symbol {symbol}: {e}")

    except Exception as e:
        logger.error(f"❌ Background download failed for symbols: {e}")


async def download_index_data_task(index_name: str, start_date: datetime, end_date: datetime, force_refresh: bool):
    """Background task to download data for an index"""
    try:
        from stock_data_manager import StockDataManager

        async with StockDataManager() as manager:
            result = await manager.download_historical_data_for_index(
                index_name=index_name,
                start_date=start_date,
                end_date=end_date,
                force_refresh=force_refresh
            )
            logger.info(f"✅ Background download completed for index {index_name}: {result}")

    except Exception as e:
        logger.error(f"❌ Background download failed for index {index_name}: {e}")


async def download_industry_data_task(industry_name: str, start_date: datetime, end_date: datetime, force_refresh: bool):
    """Background task to download data for an industry"""
    try:
        from stock_data_manager import StockDataManager

        async with StockDataManager() as manager:
            result = await manager.download_historical_data_for_industry(
                industry_name=industry_name,
                start_date=start_date,
                end_date=end_date,
                force_refresh=force_refresh
            )
            logger.info(f"✅ Background download completed for industry {industry_name}: {result}")

    except Exception as e:
        logger.error(f"❌ Background download failed for industry {industry_name}: {e}")
