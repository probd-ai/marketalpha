"""Analytics endpoints: strength/price-return/index distributions (moved verbatim from api_server)."""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.db import mongo_conn

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/analytics/research/meanlong-shape")
async def get_meanlong_shape_study():
    """Serve the precomputed mean_long shape study (research_results collection).
    Computed offline by scripts/research_meanlong/meanlong_shape.py — never
    recomputed here; re-run that script after a data refresh to update."""
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = mongo_conn.db.research_results.find_one({"_id": "meanlong_shape"}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404,
                            detail="study not computed yet — run scripts/research_meanlong/meanlong_shape.py")
    return JSONResponse(content=doc)


def bin_strength_values(values, bins):
    """Bin a list of 0-100 values into `bins` equal buckets over [0,100].
    Value exactly 100 falls in the last bin. Returns (counts, pct) where pct is
    the percentage of `values` in each bucket (sums to ~100, modulo rounding)."""
    counts = [0] * bins
    width = 100.0 / bins
    for v in values:
        idx = int(v // width)
        if idx < 0:
            idx = 0
        elif idx >= bins:
            idx = bins - 1
        counts[idx] += 1
    n = len(values)
    pct = [round(100.0 * c / n, 2) if n else 0.0 for c in counts]
    return counts, pct


def bin_values_range(values, bins, lo, hi):
    """Bin values into `bins` equal buckets over [lo, hi]; out-of-range clamp to end bins.
    Returns (counts, pct). General version of bin_strength_values (which is lo=0, hi=100)."""
    counts = [0] * bins
    width = (hi - lo) / bins if hi > lo else 1.0
    for v in values:
        idx = int((v - lo) // width)
        if idx < 0:
            idx = 0
        elif idx >= bins:
            idx = bins - 1
        counts[idx] += 1
    n = len(values)
    pct = [round(100.0 * c / n, 2) if n else 0.0 for c in counts]
    return counts, pct


def _universe_members(uni, start_dt, end_dt, point_in_time):
    """Member symbols for an analytics universe. PIT -> ever-members over the window
    (so we pull all symbols that were ever in the index); else current snapshot."""
    if point_in_time:
        from membership import load_membership, union_over
        intervals = load_membership(mongo_conn.db, uni, datetime.now().date())
        members = sorted(union_over(intervals, start_dt.date(), end_dt.date()))
        return members, intervals
    members = [d["Symbol"] for d in mongo_conn.db.index_meta.find({"index_name": uni}, {"Symbol": 1})]
    return members, None


def _make_member_check(intervals):
    """Returns chk(symbol, doc_date)->bool. PIT: member as-of that date (cached per date);
    non-PIT: always True (the pulled set is already the current snapshot)."""
    if intervals is None:
        return lambda _sym, _d: True
    from membership import members_as_of
    cache = {}
    def chk(sym, d):
        dd = d.date() if hasattr(d, "date") else d
        s = cache.get(dd)
        if s is None:
            s = members_as_of(intervals, dd)
            cache[dd] = s
        return sym in s
    return chk


@router.get("/api/analytics/strength-distribution")
async def strength_distribution(universe: str = "NIFTY50", metric: str = "mean_long",
                                start: str = "2020-01-01", end: str = "2026-08-30", bins: int = 20,
                                point_in_time: bool = True):
    """Time-evolving distribution of a TrueVX metric across a universe's CURRENT members.
    Returns a month x value-bin matrix (% of the universe in each 0-100 bin per month)."""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        if metric not in ("mean_long", "mean_mid", "mean_short", "truevx_score"):
            raise HTTPException(status_code=400, detail=f"unsupported metric {metric}")
        if bins < 2 or bins > 100:
            raise HTTPException(status_code=400, detail="bins must be in [2,100]")

        uni = {"NIFTY 500": "NIFTY500", "NIFTY 100": "NIFTY100", "NIFTY 50": "NIFTY50",
               "NIFTY 200": "NIFTY200"}.get(universe, universe)
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        try:
            members, intervals = _universe_members(uni, start_dt, end_dt, point_in_time)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=f"point-in-time membership unavailable for {uni}: {ve}")
        if not members:
            raise HTTPException(status_code=404, detail=f"no members for universe {uni}")
        chk = _make_member_check(intervals)

        # Pull the metric for all members in the window; keep each symbol's LAST value per month.
        monthly = {}  # (symbol, "YYYY-MM") -> (date, value)
        for doc in mongo_conn.db["indicators"].find(
            {"indicator_type": "truevx", "symbol": {"$in": members},
             "date": {"$gte": start_dt, "$lte": end_dt}},
            {"symbol": 1, "date": 1, f"data.{metric}": 1},
        ):
            v = (doc.get("data") or {}).get(metric)
            if v is None:
                continue
            ym = doc["date"].strftime("%Y-%m")
            key = (doc["symbol"], ym)
            prev = monthly.get(key)
            if prev is None or doc["date"] > prev[0]:
                monthly[key] = (doc["date"], float(v))

        by_month = {}
        for (sym, ym), (d, v) in monthly.items():
            if chk(sym, d):                    # PIT: only if member as-of that date
                by_month.setdefault(ym, []).append(v)

        width = 100.0 / bins
        bin_edges = [round(i * width, 2) for i in range(bins + 1)]
        months = []
        for ym in sorted(by_month.keys()):
            vals = by_month[ym]
            counts, pct = bin_strength_values(vals, bins)
            svals = sorted(vals)
            mean = round(sum(vals) / len(vals), 2)
            median = round(svals[len(svals) // 2], 2)
            months.append({"date": ym, "n": len(vals), "counts": counts, "pct": pct,
                           "mean": mean, "median": median})

        return JSONResponse(content={
            "universe": uni, "metric": metric, "n_members": len(members),
            "point_in_time": point_in_time, "bins": bins, "bin_edges": bin_edges, "months": months,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing strength distribution: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to compute distribution: {str(e)}")


@router.get("/api/analytics/strength-distribution-all")
async def strength_distribution_all(universe: str = "NIFTY50", start: str = "2020-01-01",
                                    end: str = "2026-08-30", bins: int = 20, point_in_time: bool = True):
    """Like /strength-distribution but computes ALL metrics in a single DB pass
    (so a 'complete picture' page can render every metric without N queries).
    Returns {universe, n_members, bins, bin_edges, metrics: {<metric>: [month rows]}}."""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        if bins < 2 or bins > 100:
            raise HTTPException(status_code=400, detail="bins must be in [2,100]")
        metrics = ["mean_long", "mean_mid", "mean_short", "truevx_score"]

        uni = {"NIFTY 500": "NIFTY500", "NIFTY 100": "NIFTY100", "NIFTY 50": "NIFTY50",
               "NIFTY 200": "NIFTY200"}.get(universe, universe)
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        try:
            members, intervals = _universe_members(uni, start_dt, end_dt, point_in_time)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=f"point-in-time membership unavailable for {uni}: {ve}")
        if not members:
            raise HTTPException(status_code=404, detail=f"no members for universe {uni}")
        chk = _make_member_check(intervals)

        # One pass: keep each symbol's LAST doc per month, carrying all metric values.
        monthly = {}  # (symbol, "YYYY-MM") -> (date, {metric: value})
        for doc in mongo_conn.db["indicators"].find(
            {"indicator_type": "truevx", "symbol": {"$in": members},
             "date": {"$gte": start_dt, "$lte": end_dt}},
            {"symbol": 1, "date": 1, "data": 1},
        ):
            data = doc.get("data") or {}
            ym = doc["date"].strftime("%Y-%m")
            key = (doc["symbol"], ym)
            prev = monthly.get(key)
            if prev is None or doc["date"] > prev[0]:
                monthly[key] = (doc["date"], {m: data.get(m) for m in metrics})

        # values[metric][ym] = [vals]  (PIT: only symbols that were members as-of that month)
        values = {m: {} for m in metrics}
        for (sym, ym), (d, mv) in monthly.items():
            if not chk(sym, d):
                continue
            for m in metrics:
                v = mv.get(m)
                if v is not None:
                    values[m].setdefault(ym, []).append(float(v))

        width = 100.0 / bins
        bin_edges = [round(i * width, 2) for i in range(bins + 1)]
        all_months = sorted({ym for m in metrics for ym in values[m].keys()})
        out = {}
        for m in metrics:
            rows = []
            for ym in all_months:
                vals = values[m].get(ym, [])
                counts, pct = bin_strength_values(vals, bins)
                if vals:
                    svals = sorted(vals)
                    mean = round(sum(vals) / len(vals), 2)
                    median = round(svals[len(svals) // 2], 2)
                else:
                    mean = median = 0.0
                rows.append({"date": ym, "n": len(vals), "counts": counts, "pct": pct,
                             "mean": mean, "median": median})
            out[m] = rows

        return JSONResponse(content={
            "universe": uni, "n_members": len(members), "point_in_time": point_in_time, "bins": bins,
            "bin_edges": bin_edges, "metrics": out,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing strength distribution (all): {e}")
        raise HTTPException(status_code=500, detail=f"Failed: {str(e)}")


def sma_series(values, window):
    """Trailing simple moving average over `window` items; None until enough history."""
    out = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= window:
            s -= values[i - window]
        if i >= window - 1:
            out[i] = s / window
    return out


@router.get("/api/analytics/strength-distribution-long")
async def strength_distribution_long(universe: str = "NIFTY50", start: str = "2020-01-01",
                                     end: str = "2026-08-30", bins: int = 20, windows: str = "222,444,666",
                                     point_in_time: bool = True):
    """Distribution of LONG-horizon RS = trailing SMAs of truevx_score, computed on the fly
    (222/444/666 trading days). 444/666 aren't stored, so we pull the full truevx_score series
    from a 2018 warmup floor and roll. Same month x bin matrix shape as the batch endpoint."""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        if bins < 2 or bins > 100:
            raise HTTPException(status_code=400, detail="bins must be in [2,100]")
        try:
            wins = [int(w) for w in windows.split(",") if w.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="windows must be comma-separated ints")
        if not wins or any(w < 2 or w > 2000 for w in wins):
            raise HTTPException(status_code=400, detail="each window must be in [2,2000]")

        uni = {"NIFTY 500": "NIFTY500", "NIFTY 100": "NIFTY100", "NIFTY 50": "NIFTY50",
               "NIFTY 200": "NIFTY200"}.get(universe, universe)
        warmup = datetime(2018, 1, 1)  # truevx_score persisted from here; longest window needs ~3y prior
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        try:
            members, intervals = _universe_members(uni, start_dt, end_dt, point_in_time)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=f"point-in-time membership unavailable for {uni}: {ve}")
        if not members:
            raise HTTPException(status_code=404, detail=f"no members for universe {uni}")
        chk = _make_member_check(intervals)

        per_sym = {}  # symbol -> list[(date, truevx_score)]
        for doc in mongo_conn.db["indicators"].find(
            {"indicator_type": "truevx", "symbol": {"$in": members},
             "date": {"$gte": warmup, "$lte": end_dt}},
            {"symbol": 1, "date": 1, "data.truevx_score": 1},
        ):
            v = (doc.get("data") or {}).get("truevx_score")
            if v is not None:
                per_sym.setdefault(doc["symbol"], []).append((doc["date"], float(v)))

        # values[window]["YYYY-MM"] = [last-of-month SMA across symbols]
        values = {w: {} for w in wins}
        for sym, dv in per_sym.items():
            dv.sort(key=lambda x: x[0])
            vals = [v for _, v in dv]
            for w in wins:
                sm = sma_series(vals, w)
                month_last = {}  # ym -> (date, sma) (overwritten in date order → keeps last)
                for (d, _), s in zip(dv, sm):
                    if s is None or d < start_dt or d > end_dt:
                        continue
                    month_last[d.strftime("%Y-%m")] = (d, s)
                for ym, (d, s) in month_last.items():
                    if chk(sym, d):            # PIT: member as-of that month
                        values[w].setdefault(ym, []).append(s)

        width = 100.0 / bins
        bin_edges = [round(i * width, 2) for i in range(bins + 1)]
        all_months = sorted({ym for w in wins for ym in values[w].keys()})
        out = {}
        for w in wins:
            rows = []
            for ym in all_months:
                xs = values[w].get(ym, [])
                counts, pct = bin_strength_values(xs, bins)
                if xs:
                    sx = sorted(xs)
                    mean = round(sum(xs) / len(xs), 2)
                    median = round(sx[len(sx) // 2], 2)
                else:
                    mean = median = 0.0
                rows.append({"date": ym, "n": len(xs), "counts": counts, "pct": pct,
                             "mean": mean, "median": median})
            out[f"sma_{w}"] = rows

        return JSONResponse(content={
            "universe": uni, "n_members": len(members), "point_in_time": point_in_time, "bins": bins,
            "bin_edges": bin_edges, "windows": wins, "metrics": out,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing long-horizon distribution: {e}")
        raise HTTPException(status_code=500, detail=f"Failed: {str(e)}")


@router.get("/api/analytics/price-return-distribution")
async def price_return_distribution(universe: str = "NIFTY50", start: str = "2020-01-01", end: str = "",
                                    bins: int = 20, range_min: float = -80.0, range_max: float = 400.0,
                                    point_in_time: bool = True):
    """Cross-sectional distribution of CUMULATIVE price return % since `start`, evolving monthly
    to `end` (default = today). Per stock: (close / baseline - 1) * 100 where baseline = first close
    on/after start. Universe-wise with PIT membership. Same month x bin matrix shape as the signature
    endpoints, but bins span [range_min, range_max] %."""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        if bins < 2 or bins > 100:
            raise HTTPException(status_code=400, detail="bins must be in [2,100]")
        if range_max <= range_min:
            raise HTTPException(status_code=400, detail="range_max must exceed range_min")
        if not end:
            end = datetime.now().strftime("%Y-%m-%d")

        uni = {"NIFTY 500": "NIFTY500", "NIFTY 100": "NIFTY100", "NIFTY 50": "NIFTY50",
               "NIFTY 200": "NIFTY200"}.get(universe, universe)
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        try:
            members, intervals = _universe_members(uni, start_dt, end_dt, point_in_time)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=f"point-in-time membership unavailable for {uni}: {ve}")
        if not members:
            raise HTTPException(status_code=404, detail=f"no members for universe {uni}")
        chk = _make_member_check(intervals)

        # Pull close prices for members in window across 5-year partition collections.
        partitions = sorted({f"prices_{(y // 5) * 5}_{(y // 5) * 5 + 4}"
                             for y in range(start_dt.year, end_dt.year + 1)})
        per_sym = {}  # symbol -> list[(date, close)]
        for col in partitions:
            if col not in mongo_conn.db.list_collection_names():
                continue
            for doc in mongo_conn.db[col].find(
                {"symbol": {"$in": members}, "date": {"$gte": start_dt, "$lte": end_dt}},
                {"symbol": 1, "date": 1, "close_price": 1},
            ):
                c = doc.get("close_price")
                if c is not None:
                    per_sym.setdefault(doc["symbol"], []).append((doc["date"], float(c)))

        # baseline = first close >= start; monthly last close -> cumulative return %
        by_month = {}  # ym -> [returns]
        for sym, dv in per_sym.items():
            dv.sort(key=lambda x: x[0])
            baseline = dv[0][1] if dv else 0.0
            if baseline <= 0:
                continue
            month_last = {}  # ym -> (date, close)
            for d, c in dv:
                month_last[d.strftime("%Y-%m")] = (d, c)
            for ym, (d, c) in month_last.items():
                if chk(sym, d):
                    by_month.setdefault(ym, []).append((c / baseline - 1.0) * 100.0)

        width = (range_max - range_min) / bins
        bin_edges = [round(range_min + i * width, 2) for i in range(bins + 1)]
        months = []
        for ym in sorted(by_month.keys()):
            rets = by_month[ym]
            counts, pct = bin_values_range(rets, bins, range_min, range_max)
            srt = sorted(rets)
            mean = round(sum(rets) / len(rets), 2)
            median = round(srt[len(srt) // 2], 2)
            pct_pos = round(100.0 * sum(1 for r in rets if r > 0) / len(rets), 1)
            months.append({"date": ym, "n": len(rets), "counts": counts, "pct": pct,
                           "mean": mean, "median": median, "pct_pos": pct_pos})

        return JSONResponse(content={
            "universe": uni, "n_members": len(members), "point_in_time": point_in_time,
            "start": start, "end": end, "bins": bins, "range_min": range_min, "range_max": range_max,
            "bin_edges": bin_edges, "months": months,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error computing price-return distribution: {e}")
        raise HTTPException(status_code=500, detail=f"Failed: {str(e)}")


async def get_available_indices():
    """Helper function to get available indices"""
    try:
        if mongo_conn.db is None:
            return []
        
        collection = mongo_conn.db.index_meta
        pipeline = [
            {
                "$group": {
                    "_id": "$index_name",
                    "count": {"$sum": 1}
                }
            }
        ]
        index_stats = list(collection.aggregate(pipeline))
        return [stat["_id"] for stat in index_stats if stat["count"] > 10]
    except Exception:
        return ["NIFTY50", "NIFTY100", "NIFTY 500"]

@router.get("/api/analytics/index-distribution")
async def get_index_distribution(
    index_symbol: str = "NIFTY50", 
    start_date: Optional[str] = None, 
    end_date: Optional[str] = None,
    score_ranges: Optional[str] = None,
    metric: Optional[str] = "truevx_score",  # New parameter for metric selection
    include_price: Optional[bool] = True,  # New parameter to include price data
    include_symbols: Optional[bool] = False  # New parameter to include symbol breakdown
):
    """
    Get TrueValueX score distribution for an index over time
    
    Args:
        index_symbol: Index to analyze (default: "NIFTY50")
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        score_ranges: Comma-separated score ranges (e.g., "0-20,20-40,40-60,60-80,80-100")
        metric: Metric to analyze (truevx_score, mean_short, mean_mid, mean_long)
        include_price: Whether to include base symbol price data (default: True)
        include_symbols: Whether to include detailed symbol breakdown for each date (default: False)
    
    Returns:
        Historical score distribution data with optional price data and symbol breakdown for visualization
    """
    try:
        from indicator_data_manager import IndicatorDataManager
        from datetime import datetime, timedelta
        import pandas as pd
        from collections import defaultdict
        
        logger.info(f"🔍 Getting index distribution for: {index_symbol}, metric: {metric}, include_price: {include_price}, include_symbols: {include_symbols}")
        
        # Validate metric parameter
        valid_metrics = ["truevx_score", "mean_short", "mean_mid", "mean_long"]
        if metric not in valid_metrics:
            raise HTTPException(status_code=400, detail=f"Invalid metric. Must be one of: {valid_metrics}")
        
        # Set default date range (last 5 years)
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')
        if not start_date:
            start_dt = datetime.now() - timedelta(days=5*365)
            start_date = start_dt.strftime('%Y-%m-%d')
        
        logger.info(f"📅 Date range: {start_date} to {end_date}")
        
        # Default score ranges
        if not score_ranges:
            score_ranges = "0-20,20-40,40-60,60-80,80-100"
        
        ranges = []
        for range_str in score_ranges.split(','):
            start_score, end_score = map(int, range_str.split('-'))
            ranges.append((start_score, end_score))
        
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        # First, get all stock symbols that belong to this index
        index_meta_coll = mongo_conn.db.index_meta
        index_stocks_cursor = index_meta_coll.find({"index_name": index_symbol}, {"Symbol": 1})
        index_stock_symbols = [doc["Symbol"] for doc in index_stocks_cursor]
        
        logger.info(f"📊 Found {len(index_stock_symbols)} stocks in {index_symbol} index")
        
        if not index_stock_symbols:
            return JSONResponse(content={
                "success": False,
                "error": f"No stocks found for index: {index_symbol}",
                "available_indices": await get_available_indices()
            })
        
        # Get stock metadata for company names if symbols are requested
        stock_metadata = {}
        if include_symbols:
            try:
                stock_meta_coll = mongo_conn.db.symbol_mappings
                for stock_doc in stock_meta_coll.find({"symbol": {"$in": index_stock_symbols}}):
                    stock_metadata[stock_doc["symbol"]] = {
                        "company_name": stock_doc.get("company_name", stock_doc["symbol"]),
                        "industry": stock_doc.get("industry", "Unknown"),
                        "sector": stock_doc.get("sector", "Unknown")
                    }
                logger.info(f"📋 Retrieved metadata for {len(stock_metadata)} symbols")
            except Exception as e:
                logger.warning(f"⚠️ Could not retrieve stock metadata: {e}")
                # Fallback: use symbol as company name
                for symbol in index_stock_symbols:
                    stock_metadata[symbol] = {
                        "company_name": symbol,
                        "industry": "Unknown",
                        "sector": "Unknown"
                    }
        
        async with IndicatorDataManager() as data_manager:
            # Get all available indicators for these stocks
            indicators_coll = data_manager.db[data_manager.indicators_collection]
            
            # Query for TrueValueX indicators for stocks in this index
            query = {
                "indicator_type": "truevx",
                "symbol": {"$in": index_stock_symbols},
                "date": {
                    "$gte": datetime.strptime(start_date, "%Y-%m-%d"),
                    "$lte": datetime.strptime(end_date, "%Y-%m-%d")
                }
            }
            
            logger.info(f"🔍 Querying indicators with: {query}")
            
            # Get all data points
            cursor = indicators_coll.find(query).sort("date", 1)
            
            # Process data for distribution analysis
            distribution_data = defaultdict(lambda: defaultdict(int))
            date_symbols = defaultdict(set)
            date_symbols_by_range = defaultdict(lambda: defaultdict(list))  # For detailed symbol breakdown
            
            doc_count = 0
            for doc in cursor:
                doc_count += 1
                date_str = doc["date"].strftime('%Y-%m-%d')
                symbol = doc["symbol"]
                
                # Get the specified metric value, defaulting to 0 if not found
                metric_value = doc["data"].get(metric, 0)
                
                # Skip if metric value is None or invalid
                if metric_value is None:
                    continue
                
                # Add symbol to date tracking
                date_symbols[date_str].add(symbol)
                
                # Categorize score into ranges
                for i, (range_start, range_end) in enumerate(ranges):
                    if range_start <= metric_value < range_end or (i == len(ranges)-1 and metric_value >= range_start):
                        range_label = f"{range_start}-{range_end}"
                        distribution_data[date_str][range_label] += 1
                        
                        # Store symbol details if requested
                        if include_symbols:
                            symbol_info = {
                                "symbol": symbol,
                                "value": round(metric_value, 2),
                                "company_name": stock_metadata.get(symbol, {}).get("company_name", symbol),
                                "industry": stock_metadata.get(symbol, {}).get("industry", "Unknown"),
                                "sector": stock_metadata.get(symbol, {}).get("sector", "Unknown")
                            }
                            date_symbols_by_range[date_str][range_label].append(symbol_info)
                        break
            
            logger.info(f"📈 Processed {doc_count} indicator data points")
            
            # Fetch base symbol price data if requested
            price_data = {}
            actual_base_symbol = None
            
            if include_price:
                try:
                    # First, get the base_symbol from one of the TrueValueX records for this index
                    sample_record = indicators_coll.find_one(
                        {"indicator_type": "truevx", "symbol": {"$in": index_stock_symbols}},
                        {"base_symbol": 1}
                    )
                    
                    if sample_record and "base_symbol" in sample_record:
                        actual_base_symbol = sample_record["base_symbol"]
                        logger.info(f"💰 Detected base symbol: {actual_base_symbol}")
                        
                        # Fetch price data for the actual base symbol used in calculations
                        logger.info(f"💰 Fetching {actual_base_symbol} price data for date range: {start_date} to {end_date}")
                        
                        # Query price data directly from database to avoid API sorting limitations
                        # The stock data API sorts newest first, but we need full historical range
                        from stock_data_manager import StockDataManager
                        
                        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                        days_diff = (end_dt - start_dt).days
                        
                        logger.info(f"💰 Querying {actual_base_symbol} price data directly from database for {days_diff} days ({days_diff/365:.1f} years)")
                        
                        async with StockDataManager() as price_manager:
                            # Get all price data for the date range, sorted oldest first
                            price_records = await price_manager.get_price_data(
                                symbol=actual_base_symbol,
                                start_date=start_dt,
                                end_date=end_dt,
                                limit=None,  # No limit - get all available data
                                sort_order=1  # 1 for ascending (oldest first)
                            )
                            
                            logger.info(f"💰 Retrieved {len(price_records)} price records from database")
                            
                            # Convert to date-indexed dictionary
                            for price_record in price_records:
                                date_str = price_record.date.strftime('%Y-%m-%d')
                                price_data[date_str] = {
                                    "open": price_record.open_price,
                                    "high": price_record.high_price,
                                    "low": price_record.low_price,
                                    "close": price_record.close_price,
                                    "volume": price_record.volume
                                }
                            
                            logger.info(f"💰 Fetched {len(price_data)} {actual_base_symbol} price records from database")
                            
                    else:
                        logger.warning("💰 No base_symbol found in TrueValueX records, skipping price data")
                        
                except Exception as price_error:
                    logger.error(f"💰 Error fetching base symbol price data via API: {price_error}")
                    price_data = {}
            
            # Format data for frontend
            result_data = []
            all_dates = sorted(distribution_data.keys())
            
            for date_str in all_dates:
                date_data = {
                    "date": date_str,
                    "total_symbols": len(date_symbols[date_str]),
                    "distribution": {}
                }
                
                # Add price data if available
                if include_price and date_str in price_data:
                    date_data["price"] = price_data[date_str]
                
                # Add all score ranges with counts
                for range_start, range_end in ranges:
                    range_label = f"{range_start}-{range_end}"
                    count = distribution_data[date_str].get(range_label, 0)
                    total = len(date_symbols[date_str])
                    percentage = (count / total * 100) if total > 0 else 0
                    
                    range_data = {
                        "count": count,
                        "percentage": round(percentage, 2)
                    }
                    
                    # Add symbol details if requested
                    if include_symbols:
                        symbols_in_range = date_symbols_by_range[date_str].get(range_label, [])
                        # Sort symbols by value (highest first within each range)
                        symbols_in_range.sort(key=lambda x: x["value"], reverse=True)
                        range_data["symbols"] = symbols_in_range
                    
                    date_data["distribution"][range_label] = range_data
                
                result_data.append(date_data)
            
            # Summary statistics
            total_data_points = sum(len(date_symbols[date]) for date in date_symbols)
            unique_symbols = set()
            for symbols in date_symbols.values():
                unique_symbols.update(symbols)
            
            return JSONResponse(content={
                "success": True,
                "index_symbol": index_symbol,
                "metric": metric,
                "include_price": include_price,
                "include_symbols": include_symbols,
                "base_symbol": actual_base_symbol,  # Include the actual base symbol used
                "date_range": {
                    "start": start_date,
                    "end": end_date
                },
                "score_ranges": [f"{start}-{end}" for start, end in ranges],
                "summary": {
                    "total_data_points": total_data_points,
                    "unique_symbols": len(unique_symbols),
                    "date_count": len(all_dates),
                    "symbols_per_date_avg": round(total_data_points / len(all_dates), 2) if all_dates else 0,
                    "price_data_points": len(price_data) if include_price else 0
                },
                "data": result_data
            })
            
    except Exception as e:
        logger.error(f"❌ Error in get_index_distribution: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
                "available_indices": await get_available_indices()
            }
        )
            
    except Exception as e:
        logger.error(f"❌ Error in get_index_distribution: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
                "available_indices": await get_available_indices()
            }
        )

@router.get("/api/analytics/index-distribution/symbols")
async def get_index_distribution_symbols(
    index_symbol: str = "NIFTY50",
    date: str = None,  # Specific date to get symbol breakdown
    metric: str = "truevx_score"
):
    """
    Get detailed symbol breakdown by score ranges for a specific date
    
    Args:
        index_symbol: Index to analyze (default: "NIFTY50")
        date: Specific date to analyze (YYYY-MM-DD format)
        metric: Metric to analyze (truevx_score, mean_short, mean_mid, mean_long)
        
    Returns:
        Detailed breakdown of symbols in each score range for the specified date
    """
    try:
        from indicator_data_manager import IndicatorDataManager
        from datetime import datetime
        from collections import defaultdict
        
        logger.info(f"🔍 Getting symbol breakdown for: {index_symbol}, date: {date}, metric: {metric}")
        
        # Validate inputs
        if not date:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Date parameter is required"}
            )
        
        # Parse and validate date
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Invalid date format. Use YYYY-MM-DD"}
            )
        
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        # Get stock symbols for this index
        index_meta_coll = mongo_conn.db.index_meta
        index_stocks_cursor = index_meta_coll.find({"index_name": index_symbol}, {"Symbol": 1})
        index_stock_symbols = [doc["Symbol"] for doc in index_stocks_cursor]
        
        if not index_stock_symbols:
            return JSONResponse(content={
                "success": False,
                "error": f"No stocks found for index: {index_symbol}"
            })
        
        # Get stock metadata for company names
        stock_meta_coll = mongo_conn.db.symbol_mappings
        stock_metadata = {}
        for stock_doc in stock_meta_coll.find({"symbol": {"$in": index_stock_symbols}}):
            stock_metadata[stock_doc["symbol"]] = {
                "name": stock_doc.get("name", stock_doc["symbol"]),
                "industry": stock_doc.get("industry", "Unknown"),
                "sector": stock_doc.get("sector", "Unknown")
            }
        
        async with IndicatorDataManager() as data_manager:
            indicators_coll = data_manager.db[data_manager.indicators_collection]
            
            # Query for TrueValueX indicators for this specific date
            query = {
                "indicator_type": "truevx",
                "symbol": {"$in": index_stock_symbols},
                "date": target_date
            }
            
            # Get indicator data for the specific date
            cursor = indicators_coll.find(query)
            
            # Define score ranges
            score_ranges = [
                (0, 20, "Weak (0-20)", "#ef4444"),
                (20, 40, "Below Average (20-40)", "#f97316"),
                (40, 60, "Average (40-60)", "#eab308"),
                (60, 80, "Above Average (60-80)", "#3b82f6"),
                (80, 100, "Strong (80-100)", "#22c55e")
            ]
            
            # Organize symbols by score ranges
            range_symbols = defaultdict(list)
            total_symbols = 0
            
            for doc in cursor:
                symbol = doc["symbol"]
                metric_value = doc["data"].get(metric, 0)
                
                if metric_value is None:
                    continue
                
                total_symbols += 1
                
                # Find appropriate range
                for range_start, range_end, range_label, color in score_ranges:
                    if range_start <= metric_value < range_end or (range_start == 80 and metric_value >= range_start):
                        range_key = f"{range_start}-{range_end}"
                        
                        symbol_info = {
                            "symbol": symbol,
                            "name": stock_metadata.get(symbol, {}).get("name", symbol),
                            "industry": stock_metadata.get(symbol, {}).get("industry", "Unknown"),
                            "sector": stock_metadata.get(symbol, {}).get("sector", "Unknown"),
                            "score": round(metric_value, 2),
                            "range_label": range_label,
                            "color": color
                        }
                        range_symbols[range_key].append(symbol_info)
                        break
            
            # Sort symbols within each range by score (descending)
            for range_key in range_symbols:
                range_symbols[range_key].sort(key=lambda x: x["score"], reverse=True)
            
            # Format response
            result = {
                "success": True,
                "index_symbol": index_symbol,
                "date": date,
                "metric": metric,
                "total_symbols": total_symbols,
                "ranges": {}
            }
            
            # Add range data
            for range_start, range_end, range_label, color in score_ranges:
                range_key = f"{range_start}-{range_end}"
                symbols_in_range = range_symbols.get(range_key, [])
                
                result["ranges"][range_key] = {
                    "label": range_label,
                    "color": color,
                    "count": len(symbols_in_range),
                    "percentage": round((len(symbols_in_range) / total_symbols * 100), 2) if total_symbols > 0 else 0,
                    "symbols": symbols_in_range
                }
            
            return JSONResponse(content=result)
            
    except Exception as e:
        logger.error(f"❌ Error in get_index_distribution_symbols: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )
        
    except Exception as e:
        logger.error(f"❌ Failed to get index distribution: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get index distribution: {str(e)}")



@router.get("/api/analytics/index-ratios")
async def get_index_ratios(
    index_symbol: str = "NIFTY500",
    lookback: Optional[str] = "5Y",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    metric: Optional[str] = "truevx_score",
    point_in_time: bool = True,
    include_price: bool = True,
):
    """Band-count ratios for an index over time.

    Counts alone depend on how many names the index holds; ratios do not, so they stay
    comparable across NIFTY50 and NIFTY500MICRO750 and across a rebalance.

    Two things make this fast enough to be interactive where the sibling
    /index-distribution endpoint is not:
      * Mongo does the bucketing (one $group with a $switch), so the wire carries one row
        per (date, band) instead of one document per (symbol, date) -- ~6k rows, not ~560k.
      * point-in-time membership is applied by splitting the window into the ~20 epochs
        over which the member set is constant, one indexed query each, rather than
        filtering symbol-by-symbol per date.
    """
    import time
    from datetime import date as _date, datetime as _dt

    from analytics_ratios import (
        VALID_METRICS, assemble, band_labels, band_switch,
        membership_epochs, ratio_keys, resolve_window,
    )

    t0 = time.time()
    if metric not in VALID_METRICS:
        raise HTTPException(status_code=400,
                            detail=f"Invalid metric. Must be one of: {list(VALID_METRICS)}")
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")

    db = mongo_conn.db
    lo, hi = resolve_window(lookback, start_date, end_date, _date.today())
    if lo >= hi:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")
    lo_dt, hi_dt = _dt.combine(lo, _dt.min.time()), _dt.combine(hi, _dt.max.time())
    field = f"$data.{metric}"

    def _group(match):
        return list(db.indicators.aggregate(
            [{"$match": match},
             {"$group": {"_id": {"d": "$date", "b": band_switch(field)}, "n": {"$sum": 1}}}],
            allowDiskUse=True))

    per_date: dict = {}
    epochs_used = 0
    pit_applied = False

    if point_in_time:
        try:
            from membership import load_membership, members_as_of
            intervals = load_membership(db, index_symbol, _date.today())
            epochs = membership_epochs(intervals, lo, hi, members_as_of)
        except Exception as exc:                       # no reconstitution series for this index
            logger.warning(f"index-ratios: no PIT membership for {index_symbol} ({exc}); "
                           "falling back to current constituents")
            epochs = []
        if epochs:
            pit_applied = True
            epochs_used = len(epochs)
            for ep in epochs:
                rows = _group({"indicator_type": "truevx",
                               "symbol": {"$in": list(ep.members)},
                               "date": {"$gte": _dt.combine(ep.start, _dt.min.time()),
                                        "$lt": _dt.combine(ep.end, _dt.min.time())},
                               f"data.{metric}": {"$ne": None}})
                for r in rows:
                    per_date.setdefault(r["_id"]["d"].strftime("%Y-%m-%d"), {})[r["_id"]["b"]] = r["n"]

    if not pit_applied:
        members = [d["Symbol"] for d in
                   db.index_meta.find({"index_name": index_symbol}, {"Symbol": 1})]
        if not members:
            return JSONResponse(status_code=404, content={
                "success": False,
                "error": f"No stocks found for index: {index_symbol}",
                "available_indices": await get_available_indices()})
        for r in _group({"indicator_type": "truevx", "symbol": {"$in": members},
                         "date": {"$gte": lo_dt, "$lte": hi_dt},
                         f"data.{metric}": {"$ne": None}}):
            per_date.setdefault(r["_id"]["d"].strftime("%Y-%m-%d"), {})[r["_id"]["b"]] = r["n"]

    # benchmark price overlay -- the series the indicator is measured against, so the
    # ratios can be read next to what the market actually did
    prices, base_symbol = {}, None
    if include_price:
        sample = db.indicators.find_one({"indicator_type": "truevx"}, {"base_symbol": 1})
        base_symbol = (sample or {}).get("base_symbol")
        if base_symbol:
            for coll in sorted(c for c in db.list_collection_names() if c.startswith("prices_")):
                for p in db[coll].find(
                        {"symbol": base_symbol, "date": {"$gte": lo_dt, "$lte": hi_dt}},
                        {"_id": 0, "date": 1, "close_price": 1}):
                    if p.get("close_price") is not None:
                        prices[p["date"].strftime("%Y-%m-%d")] = {"close": float(p["close_price"])}

    data = assemble(per_date, prices)
    return JSONResponse(content={
        "success": True,
        "index_symbol": index_symbol,
        "metric": metric,
        "point_in_time": pit_applied,
        "point_in_time_requested": point_in_time,
        "base_symbol": base_symbol,
        "bands": band_labels(),
        "ratios": ratio_keys(),
        "date_range": {"start": lo.isoformat(), "end": hi.isoformat()},
        "summary": {
            "date_count": len(data),
            "epochs": epochs_used,
            "symbols_latest": data[-1]["total"] if data else 0,
            "elapsed_ms": round((time.time() - t0) * 1000),
        },
        "data": data,
    })
