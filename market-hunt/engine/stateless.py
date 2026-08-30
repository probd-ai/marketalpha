"""Stateless strategy simulation engine (moved verbatim from api_server).

Pure compute: every function receives its data via parameters (data_manager,
price/indicator history dicts) — no module-level DB access.
"""
import asyncio
import bisect
import json
import logging
import random
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import HTTPException

from app.serialization import sanitize_for_json
from brokerage_calculator import (
    BrokerageCalculator,
    TradeDetails,
    calculate_single_trade_charges,
    estimate_portfolio_charges,
)
from wave_engine import SHORT_MAINTENANCE_MARGIN
from wave_models import Signal
from wave_signals import CrossState, any_group_fires

logger = logging.getLogger(__name__)


def calculate_monthly_metrics(daily_results, base_value):
    """Calculate monthly-level metrics from daily simulation results"""
    from datetime import datetime
    from collections import defaultdict
    
    # Group results by month
    monthly_groups = defaultdict(list)
    for day_result in daily_results:
        date_obj = datetime.strptime(day_result["date"], "%Y-%m-%d")
        month_key = f"{date_obj.year}-{date_obj.month:02d}"
        monthly_groups[month_key].append(day_result)
    
    # Calculate monthly returns
    monthly_returns = []
    monthly_churn_rates = []
    
    for month_key in sorted(monthly_groups.keys()):
        month_days = monthly_groups[month_key]
        
        if len(month_days) > 0:
            first_day = month_days[0]
            last_day = month_days[-1]
            
            # Monthly return
            month_return = ((last_day["portfolio_value"] / first_day["portfolio_value"]) - 1) * 100
            monthly_returns.append({
                "month": month_key,
                "return": round(month_return, 2)
            })
            
            # Monthly churn (count rebalances in this month)
            month_trades = sum(len(day.get("new_added", [])) + len(day.get("exited", [])) for day in month_days)
            # Average portfolio size for the month
            avg_portfolio_size = sum(len(day.get("holdings", [])) for day in month_days) / len(month_days)
            churn_rate = (month_trades / (avg_portfolio_size * 2)) * 100 if avg_portfolio_size > 0 else 0
            monthly_churn_rates.append(churn_rate)
    
    # Calculate metrics
    avg_monthly_return = sum(m["return"] for m in monthly_returns) / len(monthly_returns) if monthly_returns else 0
    monthly_win_rate = (sum(1 for m in monthly_returns if m["return"] > 0) / len(monthly_returns) * 100) if monthly_returns else 0
    avg_monthly_churn = sum(monthly_churn_rates) / len(monthly_churn_rates) if monthly_churn_rates else 0
    
    # Calculate volatility (standard deviation of monthly returns)
    if len(monthly_returns) > 1:
        import statistics
        volatility = statistics.stdev([m["return"] for m in monthly_returns])
    else:
        volatility = 0
    
    return {
        "monthly_returns": monthly_returns,
        "avg_monthly_return": round(avg_monthly_return, 2),
        "monthly_win_rate": round(monthly_win_rate, 2),
        "avg_monthly_churn": round(avg_monthly_churn, 2),
        "volatility": round(volatility, 2)
    }


def build_price_series(price_data_history: dict) -> dict:
    """Precompute, once, per-symbol ascending (dates, closes) parallel arrays.

    Parses each date string exactly once (vs re-parsing per momentum call).
    Iterating sorted date keys yields each symbol's arrays already ascending.
    """
    series: dict = {}
    for date_str in sorted(price_data_history.keys()):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        for symbol, prices in price_data_history[date_str].items():
            entry = series.get(symbol)
            if entry is None:
                entry = ([], [])
                series[symbol] = entry
            entry[0].append(d)
            entry[1].append(prices["close_price"])
    return series


def build_indicator_series(indicator_data_history) -> dict:
    """Per-symbol ascending (dates, data_dicts) parallel arrays; {} if no data."""
    series: dict = {}
    if not indicator_data_history:
        return series
    for date_str in sorted(indicator_data_history.keys()):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        for symbol, data in indicator_data_history[date_str].items():
            entry = series.get(symbol)
            if entry is None:
                entry = ([], [])
                series[symbol] = entry
            entry[0].append(d)
            entry[1].append(data)
    return series


def calculate_stock_momentum_fast(price_sym, indicator_sym, current_date_obj, method):
    """Pure, synchronous reproduction of calculate_stock_momentum for one symbol,
    using bisect over precomputed ascending series. Same numbers, no re-parsing."""
    try:
        if method == "price_roc_66d":
            lookback_days, _required = 90, 66
        elif method == "price_roc_222d":
            lookback_days, _required = 300, 222
        elif method == "price_roc_22d":
            lookback_days, _required = 35, 22
        else:
            lookback_days, _required = 30, 20

        if method in ("truevx_roc", "short_mean_roc", "mid_mean_roc",
                      "long_mean_roc", "stock_score_roc"):
            if not indicator_sym:
                return 0.0
            i_dates, i_data = indicator_sym
            hi = bisect.bisect_right(i_dates, current_date_obj)
            recs = i_data[:hi]                       # ascending; date <= current
            if len(recs) <= 22:
                return 0.0
            current_indicators = recs[-1]            # most recent
            past_indicators = recs[-23]              # 22 records back

            def safe_get(data, key):
                val = data.get(key)
                return val if val is not None else 0

            if method == "truevx_roc":
                c, p = safe_get(current_indicators, "truevx_score"), safe_get(past_indicators, "truevx_score")
            elif method == "short_mean_roc":
                c, p = safe_get(current_indicators, "mean_short"), safe_get(past_indicators, "mean_short")
            elif method == "mid_mean_roc":
                c, p = safe_get(current_indicators, "mean_mid"), safe_get(past_indicators, "mean_mid")
            elif method == "long_mean_roc":
                c, p = safe_get(current_indicators, "mean_long"), safe_get(past_indicators, "mean_long")
            else:  # stock_score_roc
                c = (0.1 * safe_get(current_indicators, "truevx_score") + 0.2 * safe_get(current_indicators, "mean_short")
                     + 0.3 * safe_get(current_indicators, "mean_mid") + 0.4 * safe_get(current_indicators, "mean_long"))
                p = (0.1 * safe_get(past_indicators, "truevx_score") + 0.2 * safe_get(past_indicators, "mean_short")
                     + 0.3 * safe_get(past_indicators, "mean_mid") + 0.4 * safe_get(past_indicators, "mean_long"))
            return ((c - p) / p) * 100 if p > 0 else 0.0

        # ---- price-based methods ----
        if not price_sym:
            return 0.0
        dates, closes = price_sym
        start_date = current_date_obj - timedelta(days=lookback_days)
        lo = bisect.bisect_left(dates, start_date)
        hi = bisect.bisect_right(dates, current_date_obj)
        window = closes[lo:hi]                       # ascending closes in [start, current]
        if len(window) < 2:
            return 0.0
        current_price = window[-1]

        if method == "20_day_return":
            lb = window[-20] if len(window) >= 20 else window[0]
            return ((current_price / lb) - 1) * 100 if lb > 0 else 0.0
        if method == "price_roc_22d":
            lb = window[-22] if len(window) >= 22 else window[0]
            return ((current_price / lb) - 1) * 100 if lb > 0 else 0.0
        if method == "price_roc_66d":
            lb = window[-66] if len(window) >= 66 else window[0]
            return ((current_price / lb) - 1) * 100 if lb > 0 else 0.0
        if method == "price_roc_222d":
            lb = window[-222] if len(window) >= 222 else window[0]
            return ((current_price / lb) - 1) * 100 if lb > 0 else 0.0
        if method == "risk_adjusted":
            if len(window) < 5:
                return 0.0
            daily = [(window[i] / window[i - 1]) - 1 for i in range(1, len(window)) if window[i - 1] > 0]
            if not daily:
                return 0.0
            mean_return = sum(daily) / len(daily)
            variance = sum((r - mean_return) ** 2 for r in daily) / len(daily)
            std_return = variance ** 0.5
            if std_return > 0:
                return (mean_return / std_return) * (252 ** 0.5) * 100
            return mean_return * 100
        if method == "technical":
            if len(window) < 10:
                return 0.0
            p10 = window[-10:] if len(window) >= 10 else window
            p20 = window[-20:] if len(window) >= 20 else window
            ma_10 = sum(p10) / len(p10)
            ma_20 = sum(p20) / len(p20)
            price_m = ((current_price / ma_20) - 1) * 100 if ma_20 > 0 else 0
            trend_m = ((ma_10 / ma_20) - 1) * 100 if ma_20 > 0 else 0
            return (price_m + trend_m) / 2
        return 0.0
    except Exception:
        return 0.0


async def calculate_stock_momentum(symbol: str, current_date: str, price_data_history: dict,
                                   indicator_data_history: dict = None, method: str = "20_day_return") -> float:
    """Calculate momentum score for a stock based on historical performance"""
    try:
        current_date_obj = datetime.strptime(current_date, "%Y-%m-%d")
        
        # Get lookback period based on method
        if method == "price_roc_66d":
            lookback_days = 90  # 66 trading days + buffer
            required_days = 66
        elif method == "price_roc_222d":
            lookback_days = 300  # 222 trading days + buffer
            required_days = 222
        elif method == "price_roc_22d":
            lookback_days = 35  # 22 trading days + buffer
            required_days = 22
        else:
            lookback_days = 30  # 20-22 trading days + buffer
            required_days = 20
        
        start_date = current_date_obj - timedelta(days=lookback_days)
        
        # Get price history for this symbol
        symbol_prices = []
        for date_str, prices in price_data_history.items():
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            if start_date <= date_obj <= current_date_obj and symbol in prices:
                symbol_prices.append({
                    "date": date_obj,
                    "close": prices[symbol]["close_price"]
                })
        
        # Sort by date
        symbol_prices.sort(key=lambda x: x["date"])
        
        if len(symbol_prices) < 2:
            return 0.0  # No momentum if insufficient data
        
        current_price = symbol_prices[-1]["close"]
        
        if method == "20_day_return":
            # Simple 20-day return (or available period)
            if len(symbol_prices) >= 20:
                lookback_price = symbol_prices[-20]["close"]
            else:
                lookback_price = symbol_prices[0]["close"]
            
            if lookback_price > 0:
                return ((current_price / lookback_price) - 1) * 100  # Return as percentage
            return 0.0
        
        elif method == "price_roc_66d":
            # 66-day price momentum
            if len(symbol_prices) >= 66:
                lookback_price = symbol_prices[-66]["close"]
            else:
                lookback_price = symbol_prices[0]["close"]
            
            if lookback_price > 0:
                return ((current_price / lookback_price) - 1) * 100
            return 0.0
        
        elif method == "price_roc_222d":
            # 222-day price momentum
            if len(symbol_prices) >= 222:
                lookback_price = symbol_prices[-222]["close"]
            else:
                lookback_price = symbol_prices[0]["close"]

            if lookback_price > 0:
                return ((current_price / lookback_price) - 1) * 100
            return 0.0

        elif method == "price_roc_22d":
            # 22-day price momentum
            if len(symbol_prices) >= 22:
                lookback_price = symbol_prices[-22]["close"]
            else:
                lookback_price = symbol_prices[0]["close"]

            if lookback_price > 0:
                return ((current_price / lookback_price) - 1) * 100
            return 0.0
            
        elif method == "risk_adjusted":
            # Risk-adjusted return (Sharpe-like calculation)
            if len(symbol_prices) < 5:
                return 0.0
            
            # Calculate daily returns
            daily_returns = []
            for i in range(1, len(symbol_prices)):
                prev_price = symbol_prices[i-1]["close"]
                curr_price = symbol_prices[i]["close"]
                if prev_price > 0:
                    daily_return = (curr_price / prev_price) - 1
                    daily_returns.append(daily_return)
            
            if not daily_returns:
                return 0.0
                
            # Calculate mean and std of returns
            if len(daily_returns) > 0:
                mean_return = sum(daily_returns) / len(daily_returns)
                variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
                std_return = variance ** 0.5
                
                # Risk-adjusted score (annualized)
                if std_return > 0:
                    sharpe_like = (mean_return / std_return) * (252 ** 0.5)  # Annualized
                    return sharpe_like * 100  # Scale for comparison
                return mean_return * 100
            else:
                return 0.0
            
        elif method == "technical":
            # Technical momentum combining price and trend
            if len(symbol_prices) < 10:
                return 0.0
                
            # Calculate 10-day and 20-day moving averages
            prices_10 = symbol_prices[-10:] if len(symbol_prices) >= 10 else symbol_prices
            prices_20 = symbol_prices[-20:] if len(symbol_prices) >= 20 else symbol_prices
            
            if len(prices_10) > 0 and len(prices_20) > 0:
                ma_10 = sum(p["close"] for p in prices_10) / len(prices_10)
                ma_20 = sum(p["close"] for p in prices_20) / len(prices_20)
                
                # Price momentum (current vs 20-day average)
                price_momentum = ((current_price / ma_20) - 1) * 100 if ma_20 > 0 else 0
                
                # Trend momentum (10-day MA vs 20-day MA)
                trend_momentum = ((ma_10 / ma_20) - 1) * 100 if ma_20 > 0 else 0
            else:
                price_momentum = 0
                trend_momentum = 0
            
            # Combined score
            return (price_momentum + trend_momentum) / 2
        
        elif method in ["truevx_roc", "short_mean_roc", "mid_mean_roc", "long_mean_roc", "stock_score_roc"]:
            # Indicator-based momentum (requires indicator_data_history)
            if not indicator_data_history:
                logger.warning(f"⚠️ No indicator data for {method} on symbol {symbol}")
                return 0.0
            
            # Get indicator history for this symbol (22 trading days lookback)
            indicator_records = []
            for date_str in sorted(indicator_data_history.keys()):
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                if date_obj <= current_date_obj and symbol in indicator_data_history[date_str]:
                    indicator_records.append({
                        "date": date_obj,
                        "data": indicator_data_history[date_str][symbol]
                    })
            
            # Sort by date and get most recent records
            indicator_records.sort(key=lambda x: x["date"], reverse=True)
            
            logger.info(f"📊 {symbol}: Found {len(indicator_records)} indicator records for {method}")
            
            if len(indicator_records) <= 22:
                logger.warning(f"⚠️ {symbol}: Not enough indicator data ({len(indicator_records)} records, need > 22)")
                return 0.0  # Not enough data
            
            # Current (most recent) and past (22 days ago)
            current_indicators = indicator_records[0]["data"]
            past_indicators = indicator_records[22]["data"]
            
            # Extract values with NaN handling (convert None to 0)
            def safe_get(data, key):
                val = data.get(key)
                return val if val is not None else 0
            
            if method == "truevx_roc":
                current_val = safe_get(current_indicators, "truevx_score")
                past_val = safe_get(past_indicators, "truevx_score")
                if past_val > 0:
                    return ((current_val - past_val) / past_val) * 100
                return 0.0
            
            elif method == "short_mean_roc":
                current_val = safe_get(current_indicators, "mean_short")
                past_val = safe_get(past_indicators, "mean_short")
                if past_val > 0:
                    return ((current_val - past_val) / past_val) * 100
                return 0.0
            
            elif method == "mid_mean_roc":
                current_val = safe_get(current_indicators, "mean_mid")
                past_val = safe_get(past_indicators, "mean_mid")
                if past_val > 0:
                    return ((current_val - past_val) / past_val) * 100
                return 0.0
            
            elif method == "long_mean_roc":
                current_val = safe_get(current_indicators, "mean_long")
                past_val = safe_get(past_indicators, "mean_long")
                if past_val > 0:
                    return ((current_val - past_val) / past_val) * 100
                return 0.0
            
            elif method == "stock_score_roc":
                # Calculate StockScore for both periods
                current_truevx = safe_get(current_indicators, "truevx_score")
                current_short = safe_get(current_indicators, "mean_short")
                current_mid = safe_get(current_indicators, "mean_mid")
                current_long = safe_get(current_indicators, "mean_long")
                current_score = (0.1 * current_truevx + 0.2 * current_short + 
                               0.3 * current_mid + 0.4 * current_long)
                
                past_truevx = safe_get(past_indicators, "truevx_score")
                past_short = safe_get(past_indicators, "mean_short")
                past_mid = safe_get(past_indicators, "mean_mid")
                past_long = safe_get(past_indicators, "mean_long")
                past_score = (0.1 * past_truevx + 0.2 * past_short + 
                            0.3 * past_mid + 0.4 * past_long)
                
                if past_score > 0:
                    return ((current_score - past_score) / past_score) * 100
                return 0.0
        
        return 0.0
        
    except Exception as e:
        logger.warning(f"⚠️ Error calculating momentum for {symbol}: {e}")
        return 0.0

async def select_top_stocks_by_momentum(qualified_stocks: list, current_holdings: dict,
                                price_data_history: dict, indicator_data_history: dict,
                                current_date: str, max_holdings: Optional[int],
                                momentum_method: str = "20_day_return",
                                *, price_series=None, indicator_series=None,
                                current_date_obj=None) -> tuple:
    """
    Select top stocks based on momentum ranking when portfolio limit is exceeded.
    A momentum_method with an "_low" suffix inverts the ranking: the LOWEST-ROC
    names are selected (buy-the-dip flavor), e.g. "price_roc_22d_low".
    Returns: (selected_stocks, added_stocks, removed_stocks)
    """
    ascending = momentum_method.endswith("_low")
    if ascending:
        momentum_method = momentum_method[: -len("_low")]
    try:
        # If no limit (all eligible), skip ranking entirely
        total_candidates = len(qualified_stocks) + len(current_holdings)
        if max_holdings is None or total_candidates <= max_holdings:
            # Add all qualified stocks that aren't already held
            new_stocks = [stock for stock in qualified_stocks
                         if stock["symbol"] not in current_holdings]
            return qualified_stocks, new_stocks, []
        
        # Get all candidate stocks (current holdings + newly qualified)
        all_candidates = []

        _cdo = current_date_obj or datetime.strptime(current_date, "%Y-%m-%d")

        async def _momentum(symbol):
            if price_series is not None:
                return calculate_stock_momentum_fast(
                    price_series.get(symbol),
                    (indicator_series or {}).get(symbol), _cdo, momentum_method)
            return await calculate_stock_momentum(
                symbol, current_date, price_data_history, indicator_data_history, momentum_method)

        # Add current holdings with their momentum scores
        for symbol in current_holdings.keys():
            momentum = await _momentum(symbol)
            all_candidates.append({
                "symbol": symbol,
                "momentum_score": momentum,
                "is_current_holding": True,
                "truevx_score": 0  # Will be updated if in qualified_stocks
            })
            logger.info(f"🔍 Momentum ({momentum_method}) for {symbol} (holding): {momentum:.2f}")
        
        # Add newly qualified stocks
        for stock in qualified_stocks:
            symbol = stock["symbol"]
            if symbol not in current_holdings:
                momentum = await _momentum(symbol)
                all_candidates.append({
                    "symbol": symbol,
                    "momentum_score": momentum,
                    "is_current_holding": False,
                    "truevx_score": stock.get("truevx_score", 0)
                })
                logger.info(f"🔍 Momentum ({momentum_method}) for {symbol} (new): {momentum:.2f}")
            else:
                # Update truevx_score for existing holdings
                for candidate in all_candidates:
                    if candidate["symbol"] == symbol:
                        candidate["truevx_score"] = stock.get("truevx_score", 0)
                        break
        
        # Sort by momentum score (descending = highest first; "_low" = ascending)
        all_candidates.sort(key=lambda x: x["momentum_score"], reverse=not ascending)
        
        # Select top stocks up to max_holdings limit (None = no limit)
        selected_candidates = all_candidates if max_holdings is None else all_candidates[:max_holdings]
        selected_symbols = {candidate["symbol"] for candidate in selected_candidates}
        
        # Determine added and removed stocks
        current_symbols = set(current_holdings.keys())
        added_stocks = [candidate["symbol"] for candidate in selected_candidates 
                       if candidate["symbol"] not in current_symbols]
        removed_stocks = [symbol for symbol in current_symbols 
                         if symbol not in selected_symbols]
        
        # Convert selected candidates back to qualified_stocks format
        selected_stocks = []
        for candidate in selected_candidates:
            # Find the original stock data or create it
            original_stock = None
            for stock in qualified_stocks:
                if stock["symbol"] == candidate["symbol"]:
                    original_stock = stock
                    break
            
            if original_stock:
                selected_stocks.append(original_stock)
            else:
                # For existing holdings that remain selected
                selected_stocks.append({
                    "symbol": candidate["symbol"],
                    "truevx_score": candidate["truevx_score"],
                    "momentum_score": candidate["momentum_score"]
                })
        
        logger.info(f"🎯 Portfolio limit applied: {len(all_candidates)} candidates → {len(selected_stocks)} selected")
        logger.info(f"📈 Added: {added_stocks}")
        logger.info(f"📉 Removed: {removed_stocks}")
        
        return selected_stocks, added_stocks, removed_stocks
        
    except Exception as e:
        logger.error(f"❌ Error in momentum selection: {e}")
        # Fallback: just take first max_holdings qualified stocks (None = no limit)
        limited_stocks = qualified_stocks if max_holdings is None else qualified_stocks[:max_holdings]
        new_stocks = [stock for stock in limited_stocks 
                     if stock["symbol"] not in current_holdings]
        return limited_stocks, new_stocks, []

async def run_strategy_simulation_debug(data_manager, strategy, universe_symbols, params):
    """Debug version of simulation with extensive logging"""
    try:
        logger.info(f"🔍 DEBUG: Starting simulation with {len(universe_symbols)} symbols")
        indicators_coll = data_manager.db[data_manager.indicators_collection]
        
        # Parse date range
        start_date = datetime.strptime(params.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(params.end_date, "%Y-%m-%d")
        
        # Get indicator data (simplified for debug)
        indicator_query = {
            "indicator_type": "truevx",
            "symbol": {"$in": universe_symbols},
            "date": {"$gte": start_date, "$lte": end_date}
        }
        
        indicator_data = {}
        cursor = indicators_coll.find(indicator_query).sort("date", 1)
        for doc in cursor:
            date_str = doc["date"].strftime('%Y-%m-%d')
            if date_str not in indicator_data:
                indicator_data[date_str] = {}
            
            indicator_data[date_str][doc["symbol"]] = {
                "symbol": doc["symbol"],
                "truevx_score": doc["data"].get("truevx_score") or 0,
                "mean_short": doc["data"].get("mean_short") or 0,
                "mean_mid": doc["data"].get("mean_mid") or 0,
                "mean_long": doc["data"].get("mean_long") or 0
            }
        
        # Get price data
        from stock_data_manager import StockDataManager
        price_data = {}
        
        async with StockDataManager() as stock_manager:
            for symbol in universe_symbols:
                symbol_prices = await stock_manager.get_price_data(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    limit=10000,  # Increased limit for full date range
                    sort_order=1
                )
                
                for record in symbol_prices:
                    date_str = record.date.strftime('%Y-%m-%d')
                    if date_str not in price_data:
                        price_data[date_str] = {}
                    
                    price_data[date_str][symbol] = {
                        "symbol": symbol,
                        "close_price": float(record.close_price),
                    }
        
        # Initialize simulation
        portfolio_value = params.portfolio_base_value
        current_holdings = {}
        debug_results = []
        
        # Get trading dates
        dates = sorted(set(indicator_data.keys()) & set(price_data.keys()))
        rebalance_dates = get_rebalance_dates(dates, params.rebalance_frequency, params.rebalance_date)
        
        logger.info(f"🧪 DEBUG: Processing {len(dates)} dates")
        logger.info(f"🧪 DEBUG: Rebalance dates: {list(rebalance_dates)}")
        
        for i, date_str in enumerate(dates):
            logger.info(f"\n📅 DEBUG DAY {i+1}: {date_str}")
            
            day_indicators = indicator_data.get(date_str, {})
            day_prices = price_data.get(date_str, {})
            
            logger.info(f"📊 Available indicators: {len(day_indicators)}, Available prices: {len(day_prices)}")
            if day_indicators:
                sample_symbol = list(day_indicators.keys())[0]
                logger.info(f"📈 Sample indicator data for {sample_symbol}: {day_indicators[sample_symbol]}")
            
            if not day_indicators or not day_prices:
                logger.info(f"⚠️ No data for {date_str}, skipping")
                continue
            
            # Log strategy rules for debugging
            logger.info(f"📋 Strategy rules: {strategy['rules']}")
            
            # Apply strategy rules
            qualified_stocks = apply_strategy_rules(day_indicators, strategy["rules"])
            qualified_symbols = [stock["symbol"] for stock in qualified_stocks]
            
            logger.info(f"🎯 Qualified stocks: {qualified_symbols} (from {len(day_indicators)} available)")
            
            # Check if rebalancing
            should_rebalance = (params.rebalance_frequency == "dynamic" or 
                              date_str in rebalance_dates or 
                              i == 0)
            
            logger.info(f"🔄 Should rebalance: {should_rebalance}")
            
            if should_rebalance:
                current_portfolio_value = calculate_current_portfolio_value(current_holdings, day_prices)
                logger.info(f"💰 Current portfolio value: ₹{current_portfolio_value:,.2f}")
                
                # Log detailed holdings
                logger.info(f"📊 Current holdings ({len(current_holdings)}):")
                for symbol, holding in current_holdings.items():
                    if symbol in day_prices:
                        price = day_prices[symbol]["close_price"]
                        value = holding["shares"] * price
                        logger.info(f"  • {symbol}: {holding['shares']:.2f} @ ₹{price:.2f} = ₹{value:,.2f}")
                
                # Simple rebalancing for debug (no momentum for clarity)
                if i == 0:
                    rebalance_value = params.portfolio_base_value
                else:
                    rebalance_value = current_portfolio_value
                
                # Apply momentum-based portfolio limit selection (same as actual simulation)
                selected_stocks, momentum_added, momentum_removed = await select_top_stocks_by_momentum(
                    qualified_stocks=qualified_stocks,
                    current_holdings=current_holdings,
                    price_data_history=price_data,
                    indicator_data_history=indicator_data,
                    current_date=date_str,
                    max_holdings=params.max_holdings,
                    momentum_method=params.momentum_ranking
                )
                
                # Update symbols list to only selected stocks
                selected_symbols = [stock["symbol"] for stock in selected_stocks]
                
                logger.info(f"💵 Rebalance value: ₹{rebalance_value:,.2f}")
                logger.info(f"📊 Selected {len(selected_symbols)}/{len(qualified_symbols)} stocks by momentum")
                
                # Clear holdings and rebuild
                current_holdings = {}
                
                # Equal weight allocation among selected momentum stocks
                if selected_symbols:
                    allocation_per_stock = rebalance_value / len(selected_symbols)
                    logger.info(f"📊 Allocation per stock: ₹{allocation_per_stock:,.2f}")
                    
                    for symbol in selected_symbols:
                        if symbol in day_prices:
                            price = day_prices[symbol]["close_price"]
                            shares = allocation_per_stock / price
                            current_holdings[symbol] = {
                                "shares": shares,
                                "avg_price": price
                            }
                            logger.info(f"  📈 Bought {symbol}: {shares:.2f} shares @ ₹{price:.2f}")
                
                # Recalculate portfolio value
                new_portfolio_value = calculate_current_portfolio_value(current_holdings, day_prices)
                logger.info(f"✅ New portfolio value: ₹{new_portfolio_value:,.2f}")
                portfolio_value = new_portfolio_value
            else:
                # Just update portfolio value
                if current_holdings:
                    portfolio_value = calculate_current_portfolio_value(current_holdings, day_prices)
                    logger.info(f"📊 Updated portfolio value: ₹{portfolio_value:,.2f}")
            
            # Create debug result
            debug_results.append({
                "date": date_str,
                "day_number": i + 1,
                "portfolio_value": portfolio_value,
                "should_rebalance": should_rebalance,
                "qualified_stocks": qualified_symbols,
                "holdings_count": len(current_holdings),
                "holdings_detail": [
                    {
                        "symbol": symbol,
                        "shares": holding["shares"],
                        "price": day_prices.get(symbol, {}).get("close_price", 0),
                        "value": holding["shares"] * day_prices.get(symbol, {}).get("close_price", 0)
                    }
                    for symbol, holding in current_holdings.items()
                    if symbol in day_prices
                ]
            })
        
        return {
            "params": params.dict(),
            "debug_results": debug_results,
            "summary": {
                "total_days": len(debug_results),
                "rebalance_days": len([r for r in debug_results if r["should_rebalance"]]),
                "final_value": debug_results[-1]["portfolio_value"] if debug_results else 0
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Debug simulation error: {e}")
        raise

async def rebalance_portfolio_with_charges(current_holdings: dict, 
                                         selected_symbols: list,
                                         day_prices: dict,
                                         available_capital: float,
                                         params,
                                         holding_periods: dict = None) -> dict:
    """
    Rebalance portfolio accounting for transaction charges
    
    Args:
        current_holdings: Current portfolio holdings {symbol: {"shares": float, "avg_price": float}}
        selected_symbols: List of symbols to hold in the new portfolio
        day_prices: Current day prices {symbol: {"close_price": float, ...}}
        available_capital: Available capital for rebalancing
        params: Simulation parameters including brokerage settings
        
    Returns:
        Dictionary with new holdings, charges, and trade details
    """
    try:
        _slip = float(getattr(params, "slippage_bps", 0) or 0) / 10000.0
        # Initialize brokerage calculator
        calculator = BrokerageCalculator(
            default_exchange=params.exchange,
            custom_brokerage_rate=params.custom_brokerage_rate
        )
        
        rebalance_result = {
            "new_holdings": {},
            "total_buy_charges": 0.0,
            "total_sell_charges": 0.0,
            "net_capital_used": 0.0,
            "charge_breakdown": {},
            "trade_details": [],
            "sell_proceeds": 0.0,
            "remaining_cash": 0.0
        }
        
        # Phase 1: Calculate sell transactions and charges
        sell_trades = []
        total_sell_proceeds = 0.0
        
        for symbol, holding in current_holdings.items():
            if symbol not in selected_symbols and symbol in day_prices:
                # Create sell trade
                sell_trade = {
                    "symbol": symbol,
                    "quantity": holding["shares"],
                    "price": day_prices[symbol]["close_price"] * (1 - _slip)
                }
                sell_trades.append(sell_trade)
        
        # Calculate sell charges if there are sell trades
        if sell_trades:
            sell_result = calculator.calculate_portfolio_rebalance_charges(sell_trades, [])
            rebalance_result["total_sell_charges"] = sell_result["total_sell_charges"]
            rebalance_result["sell_proceeds"] = sell_result["net_sell_proceeds"]
            total_sell_proceeds = sell_result["net_sell_proceeds"]
            
            # Add sell trade details
            for trade_detail in sell_result["sell_trades"]:
                rebalance_result["trade_details"].append(trade_detail)
        
        # Phase 2: Calculate available capital for purchases
        total_available = available_capital + total_sell_proceeds
        
        # Phase 3: Calculate buy transactions with iterative charge adjustment
        if selected_symbols:
            # Get prices for selected symbols
            valid_symbols = [symbol for symbol in selected_symbols if symbol in day_prices]
            
            if valid_symbols:
                if params.rebalance_type == "skewed" and holding_periods is not None:
                    # Use skewed allocation based on holding periods
                    skewed_allocations = calculate_skewed_allocation(
                        selected_symbols=valid_symbols,
                        holding_periods=holding_periods,
                        total_value=total_available
                    )
                    
                    # Estimate total buy charges for skewed allocation
                    estimated_buy_charges = 0.0
                    for symbol in valid_symbols:
                        allocation = skewed_allocations[symbol]
                        estimated_charges = calculator.calculate_transaction_charges(
                            trade_value=allocation,
                            trade_type="BUY"
                        )
                        estimated_buy_charges += estimated_charges.total_charges
                    
                    # Adjust allocations proportionally to account for charges
                    available_for_investment = max(0, total_available - estimated_buy_charges)
                    adjustment_factor = available_for_investment / total_available if total_available > 0 else 0
                    
                    # Create buy trades with skewed allocations
                    buy_trades = []
                    for symbol in valid_symbols:
                        if symbol in day_prices:
                            price = day_prices[symbol]["close_price"] * (1 + _slip)
                            adjusted_allocation = skewed_allocations[symbol] * adjustment_factor
                            shares_to_buy = adjusted_allocation / price if price > 0 else 0
                            
                            if shares_to_buy > 0:
                                buy_trade = {
                                    "symbol": symbol,
                                    "quantity": shares_to_buy,
                                    "price": price
                                }
                                buy_trades.append(buy_trade)
                                
                else:
                    # Original equal allocation logic
                    if valid_symbols:  # Add check to prevent division by zero
                        # Initial allocation estimate (will be adjusted for charges)
                        initial_allocation_per_stock = total_available / len(valid_symbols)
                        
                        # Estimate total buy charges
                        estimated_buy_charges = 0.0
                        for symbol in valid_symbols:
                            estimated_charges = calculator.calculate_transaction_charges(
                                trade_value=initial_allocation_per_stock,
                                trade_type="BUY"
                            )
                            estimated_buy_charges += estimated_charges.total_charges
                        
                        # Adjust allocation to account for estimated charges
                        available_for_investment = max(0, total_available - estimated_buy_charges)
                        target_investment_per_stock = available_for_investment / len(valid_symbols) if valid_symbols else 0
                        
                        # Create buy trades
                        buy_trades = []
                        for symbol in valid_symbols:
                            if symbol in day_prices:
                                price = day_prices[symbol]["close_price"] * (1 + _slip)
                                shares_to_buy = target_investment_per_stock / price if price > 0 else 0
                            
                            if shares_to_buy > 0:
                                buy_trade = {
                                    "symbol": symbol,
                                    "quantity": shares_to_buy,
                                    "price": price
                                }
                                buy_trades.append(buy_trade)
                    else:
                        # No valid symbols for allocation
                        logger.warning(f"⚠️  No valid symbols for charge-aware rebalancing")
                        buy_trades = []
                
                # Calculate actual buy charges
                if buy_trades:
                    buy_result = calculator.calculate_portfolio_rebalance_charges([], buy_trades)
                    rebalance_result["total_buy_charges"] = buy_result["total_buy_charges"]
                    rebalance_result["net_capital_used"] = buy_result["net_buy_cost"]
                    
                    # Add buy trade details
                    for trade_detail in buy_result["buy_trades"]:
                        rebalance_result["trade_details"].append(trade_detail)
                    
                    # Create new holdings
                    for trade in buy_trades:
                        symbol = trade["symbol"]
                        price = trade["price"]
                        shares = trade["quantity"]
                        
                        # Find the corresponding trade detail with charges
                        trade_charges = None
                        for detail in buy_result["buy_trades"]:
                            if detail["symbol"] == symbol:
                                trade_charges = detail["charges"]
                                break
                        
                        rebalance_result["new_holdings"][symbol] = {
                            "shares": shares,
                            "avg_price": price,
                            "total_cost": shares * price + (trade_charges["total_charges"] if trade_charges else 0)
                            # Removed cumulative_charges from individual holdings - this was causing inflation
                        }
        
        # Calculate remaining cash
        total_spent = rebalance_result["net_capital_used"]
        rebalance_result["remaining_cash"] = max(0, total_available - total_spent)
        
        # Aggregate charge breakdown with detailed components
        total_charges = rebalance_result["total_buy_charges"] + rebalance_result["total_sell_charges"]
        
        # Calculate detailed charge breakdown from trade details
        component_breakdown = {
            "stt": 0.0,
            "transaction_charges": 0.0,
            "sebi_charges": 0.0,
            "stamp_duty": 0.0,
            "brokerage": 0.0,
            "gst": 0.0
        }
        
        # Aggregate charges from all trade details
        for trade_detail in rebalance_result["trade_details"]:
            if "charges" in trade_detail:
                charges = trade_detail["charges"]
                component_breakdown["stt"] += charges.get("stt", 0.0)
                component_breakdown["transaction_charges"] += charges.get("transaction_charges", 0.0)
                component_breakdown["sebi_charges"] += charges.get("sebi_charges", 0.0)
                component_breakdown["stamp_duty"] += charges.get("stamp_duty", 0.0)
                component_breakdown["brokerage"] += charges.get("brokerage", 0.0)
                component_breakdown["gst"] += charges.get("gst", 0.0)
        
        rebalance_result["charge_breakdown"] = {
            "total_charges": total_charges,
            "buy_charges": rebalance_result["total_buy_charges"],
            "sell_charges": rebalance_result["total_sell_charges"],
            "net_impact": total_charges,
            "components": component_breakdown,
            "charge_rate_info": {
                "exchange": params.exchange,
                "custom_brokerage_rate": params.custom_brokerage_rate,
                "effective_charge_percent": (total_charges / available_capital * 100) if available_capital > 0 else 0
            }
        }
        
        logger.info(f"💰 Charge-aware rebalancing completed: Total charges = ₹{total_charges:,.2f}")
        logger.info(f"📊 Buy charges: ₹{rebalance_result['total_buy_charges']:,.2f}, Sell charges: ₹{rebalance_result['total_sell_charges']:,.2f}")
        logger.info(f"🏦 Remaining cash: ₹{rebalance_result['remaining_cash']:,.2f}")
        
        return rebalance_result
        
    except Exception as e:
        logger.error(f"❌ Error in charge-aware rebalancing: {e}")
        raise

def calculate_skewed_allocation(selected_symbols: list, 
                               holding_periods: dict, 
                               total_value: float) -> dict:
    """
    Calculate skewed allocation based on holding periods
    
    Args:
        selected_symbols: List of symbols to allocate to
        holding_periods: Dictionary of {symbol: consecutive_periods}
        total_value: Total value to allocate
        
    Returns:
        Dictionary of {symbol: allocation_amount}
    """
    if not selected_symbols:
        return {}
    
    # Calculate weights based on holding periods
    symbol_weights = {}
    total_weight = 0
    
    for symbol in selected_symbols:
        # Get holding period (0 for new stocks)
        periods_held = holding_periods.get(symbol, 0)
        
        # Calculate weight using exponential function
        # Formula: weight = 1 + (holding_periods * 0.3)
        # This gives: 0 periods = weight 1.0, 1 period = weight 1.3, 2 periods = weight 1.6, etc.
        weight = 1.0 + (periods_held * 0.3)
        
        symbol_weights[symbol] = weight
        total_weight += weight
    
    # Calculate allocation amounts
    allocations = {}
    if total_weight > 0:  # Check for division by zero
        for symbol in selected_symbols:
            weight_ratio = symbol_weights[symbol] / total_weight
            allocation = total_value * weight_ratio
            allocations[symbol] = allocation
    else:
        # If total_weight is 0, use equal allocation
        if selected_symbols:
            equal_allocation = total_value / len(selected_symbols)
            for symbol in selected_symbols:
                allocations[symbol] = equal_allocation
    
    logger.info(f"📊 Skewed allocation calculated:")
    for symbol in selected_symbols:
        periods = holding_periods.get(symbol, 0)
        weight = symbol_weights[symbol]
        allocation = allocations[symbol]
        logger.info(f"  📈 {symbol}: {periods} periods held, weight {weight:.1f}, allocation ₹{allocation:,.0f}")
    
    return allocations

def _short_rebalance(current_holdings, selected_symbols, day_prices, equity, params, holding_periods):
    """Full-turnover SHORT rebalance for the stateless path.

    Covers every current short, then opens new equal/skewed-weighted shorts whose
    total notional equals `equity`. Short accounting: opening SELLS borrowed shares
    (cash up by the notional); covering BUYS them back (cash down). After covering
    all positions cash == `equity` (the caller passes the true net short equity =
    cash − Σ short MV); after opening `equity` of new shorts, cash == 2·equity and
    short market value == equity, so net equity (cash − MV) is preserved before
    market drift and charges. Brokerage (when include_brokerage) is charged on BOTH
    the cover (BUY) and the new short-sell (SELL) and deducted from cash.

    Returns a dict mirroring the long rebalance outputs: new_holdings,
    remaining_cash, daily_charges, trade_details.
    """
    calc = (BrokerageCalculator(default_exchange=params.exchange,
                                custom_brokerage_rate=params.custom_brokerage_rate)
            if params.include_brokerage else None)
    components = {"stt": 0.0, "transaction_charges": 0.0, "sebi_charges": 0.0,
                  "stamp_duty": 0.0, "brokerage": 0.0, "gst": 0.0}
    trade_details = []

    def _cd(ch):
        for k in components:
            components[k] += getattr(ch, k)
        return {"brokerage": ch.brokerage, "stt": ch.stt,
                "transaction_charges": ch.transaction_charges, "sebi_charges": ch.sebi_charges,
                "stamp_duty": ch.stamp_duty, "gst": ch.gst, "total_charges": ch.total_charges}

    # 1) Cover all existing shorts. Cash becomes `equity` by construction.
    cover_charges = 0.0
    for sym, h in current_holdings.items():
        if sym in day_prices and calc:
            px = day_prices[sym]["close_price"]
            ch = calc.calculate_transaction_charges(trade_value=h["shares"] * px, trade_type="BUY")
            cover_charges += ch.total_charges
            trade_details.append({"symbol": sym, "action": "COVER", "quantity": h["shares"],
                                  "price": px, "charges": _cd(ch)})
    cash = equity

    # 2) Allocate `equity` notional across the selected names (equal or skewed).
    valid = [s for s in selected_symbols
             if s in day_prices and day_prices[s]["close_price"] > 0]
    if not valid:
        cash -= cover_charges
        return {"new_holdings": {}, "remaining_cash": cash,
                "daily_charges": {"total_charges": cover_charges, "buy_charges": cover_charges,
                                  "sell_charges": 0.0, "components": components},
                "trade_details": trade_details}
    if params.rebalance_type == "skewed" and holding_periods is not None:
        alloc = calculate_skewed_allocation(valid, holding_periods, equity)
    else:
        per = equity / len(valid)
        alloc = {s: per for s in valid}

    # 3) Open new shorts: proceeds add to cash; brokerage on the SELL.
    open_charges = 0.0
    new_holdings = {}
    for s in valid:
        px = day_prices[s]["close_price"]
        notional = alloc[s]
        shares = notional / px
        cash += notional                       # short-sale proceeds
        if calc:
            ch = calc.calculate_transaction_charges(trade_value=notional, trade_type="SELL")
            open_charges += ch.total_charges
            trade_details.append({"symbol": s, "action": "SHORT", "quantity": shares,
                                  "price": px, "charges": _cd(ch)})
        new_holdings[s] = {"shares": shares, "avg_price": px}

    cash -= (cover_charges + open_charges)
    return {"new_holdings": new_holdings, "remaining_cash": cash,
            "daily_charges": {"total_charges": cover_charges + open_charges,
                              "buy_charges": cover_charges, "sell_charges": open_charges,
                              "components": components},
            "trade_details": trade_details}


async def run_strategy_simulation(data_manager, strategy, universe_symbols, params, membership=None):
    """Execute the strategy simulation logic with daily rebalancing.

    When `membership` is provided (point-in-time mode), the eligible universe at each
    rebalance is gated to the index members as-of that date, and holdings that have
    left the index are force-exited — making the run survivorship-bias-free.
    """
    try:
        # Initialize cumulative charges as a local variable scoped to this call
        # (was a function attribute — shared mutable state, unsafe under concurrency)
        cumulative_charges = 0.0
        
        # ── Portfolio-curve gate (shadow method) ─────────────────────────────
        # The fib level must be computed on an ALWAYS-INVESTED shadow run: the
        # realized curve flatlines in cash and would never re-cross its own
        # level (deadlock). Real book exits when the shadow closes below the
        # fib level of the shadow's trailing range, re-enters when it recovers.
        pf_gate_on: dict = {}
        if getattr(params, "portfolio_gate", False):
            shadow_params = params.copy(update={"portfolio_gate": False})
            shadow_run = await run_strategy_simulation(
                data_manager, strategy, universe_symbols, shadow_params,
                membership=membership)
            s_dates = [r["date"] for r in shadow_run.get("results", [])]
            s_vals = [float(r["portfolio_value"]) for r in shadow_run.get("results", [])]
            if s_vals:
                from risk_gates import rolling_fib_gate
                states = rolling_fib_gate(s_vals, s_vals, s_vals,
                                          params.gate_fib_lookback,
                                          params.gate_fib_level)
                pf_gate_on = dict(zip(s_dates, states))
                logger.info(f"🚦 Portfolio-curve gate (shadow): "
                            f"{sum(1 for v in pf_gate_on.values() if not v)} risk-off sessions")

        logger.info(f"🔍 Starting simulation with {len(universe_symbols)} symbols")
        indicators_coll = data_manager.db[data_manager.indicators_collection]
        
        # Parse date range
        start_date = datetime.strptime(params.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(params.end_date, "%Y-%m-%d")
        
        # Get all indicator data for the universe and date range
        indicator_query = {
            "indicator_type": "truevx",
            "symbol": {"$in": universe_symbols},
            "date": {
                "$gte": start_date,
                "$lte": end_date
            }
        }
        
        # Process indicator data by date
        indicator_data = {}
        cursor = indicators_coll.find(indicator_query).sort("date", 1)
        for doc in cursor:
            date_str = doc["date"].strftime('%Y-%m-%d')
            if date_str not in indicator_data:
                indicator_data[date_str] = {}
            
            indicator_data[date_str][doc["symbol"]] = {
                "symbol": doc["symbol"],
                "truevx_score": doc["data"].get("truevx_score") or 0,
                "mean_short": doc["data"].get("mean_short") or 0,
                "mean_mid": doc["data"].get("mean_mid") or 0,
                "mean_long": doc["data"].get("mean_long") or 0
            }

        # Process price data by date using StockDataManager
        from stock_data_manager import StockDataManager
        price_data = {}
        
        logger.info(f"🔄 Loading price data for {len(universe_symbols)} symbols (concurrent)")

        async with StockDataManager() as stock_manager:
            _price_sem = asyncio.Semaphore(20)  # bound concurrency to protect the connection pool

            async def _fetch_symbol_prices(sym):
                async with _price_sem:
                    return sym, await stock_manager.get_price_data(
                        symbol=sym,
                        start_date=start_date,
                        end_date=end_date,
                        limit=10000,
                        sort_order=1,
                    )

            fetched = await asyncio.gather(*[_fetch_symbol_prices(s) for s in universe_symbols])

        for symbol, symbol_prices in fetched:
            for record in symbol_prices:
                date_str = record.date.strftime('%Y-%m-%d')
                if date_str not in price_data:
                    price_data[date_str] = {}
                price_data[date_str][symbol] = {
                    "symbol": symbol,
                    "close_price": float(record.close_price),
                    "open_price": float(record.open_price),
                    "high_price": float(record.high_price),
                    "low_price": float(record.low_price),
                    "volume": int(record.volume) if record.volume else 0,
                }
        
        logger.info(f"📊 Loaded price data for {len(price_data)} trading dates")

        price_series = build_price_series(price_data)
        indicator_series = build_indicator_series(indicator_data)

        # Per-stock Dynamic-Fib eligibility gates (close-based OHLC from price_data).
        stock_gate_on: dict = {}
        if getattr(params, "stock_gate", False):
            from risk_gates import gate_map_fib as _gmf
            _sg_series: dict = {}
            for _ds in sorted(price_data.keys()):
                for _sym, _px in price_data[_ds].items():
                    _e = _sg_series.setdefault(_sym, ([], [], [], []))
                    _e[0].append(_ds)
                    _e[1].append(float(_px.get("high_price") or _px["close_price"]))
                    _e[2].append(float(_px.get("low_price") or _px["close_price"]))
                    _e[3].append(float(_px["close_price"]))
            for _sym, (_d, _h, _l, _c) in _sg_series.items():
                stock_gate_on[_sym] = _gmf(_d, _h, _l, _c,
                                           params.gate_fib_lookback, params.gate_fib_level)
            del _sg_series
            logger.info(f"🚦 Stock-level DynamicFib gate precomputed for {len(stock_gate_on)} symbols")


        # Initialize simulation state
        # side: 'long' (sign=+1, unchanged) | 'short' (sign=-1). A short run holds the
        # SAME selected names but sells-to-open: equity = cash + sign·Σ(shares·price),
        # profit when prices fall, daily borrow cost accrued on short market value.
        sign = -1.0 if getattr(params, "side", "long") == "short" else 1.0
        ruined = False           # short book wiped out (liability ≥ capital → margin call)
        ruin_date = None
        portfolio_value = params.portfolio_base_value
        current_holdings = {}  # {symbol: {"shares": float, "avg_price": float}}
        cash_balance = params.portfolio_base_value  # Track cash separately
        simulation_results = []
        prev_portfolio_value = portfolio_value
        
        # Initialize holding period tracking for skewed allocation
        holding_periods = {}  # {symbol: consecutive_rebalance_periods}
        rebalance_count = 0  # Track number of rebalances for logging
        
        # Initialize benchmark tracking
        benchmark_value = params.portfolio_base_value  # Start with same base value
        prev_benchmark_close = None
        
        # Get benchmark data for comparison
        if params.benchmark_symbol:
            benchmark_symbol = params.benchmark_symbol
        elif params.universe == "NIFTY50":
            benchmark_symbol = "Nifty 50"
        elif params.universe == "NIFTY100":
            benchmark_symbol = "Nifty 100"
        elif params.universe == "NIFTY500":
            benchmark_symbol = "Nifty 500"
        else:
            benchmark_symbol = "Nifty 50"  # Default fallback
            
        benchmark_prices = {}
        
        logger.info(f"📊 Loading benchmark data for {benchmark_symbol}")
        
        async with StockDataManager() as stock_manager:
            benchmark_data = await stock_manager.get_price_data(
                symbol=benchmark_symbol,
                start_date=start_date,
                end_date=end_date,
                limit=10000,
                sort_order=1
            )
            
            for record in benchmark_data:
                date_str = record.date.strftime('%Y-%m-%d')
                benchmark_prices[date_str] = float(record.close_price)

        if not benchmark_prices:
            # Fail loud: a flat benchmark silently reports benchmark_return=0 and
            # alpha equal to the strategy's own return.
            raise HTTPException(
                status_code=400,
                detail=f"No benchmark price data for '{benchmark_symbol}' in "
                       f"{params.start_date}..{params.end_date} — load it or pass benchmark_symbol.")

        logger.info(f"📈 Loaded benchmark data for {len(benchmark_prices)} trading dates")

        # ── Regime gate: risk-off while gate index closes below its SMA ──────────
        # Pre-fetch enough history that the SMA is fully formed on the first sim day.
        gate_on: dict = {}
        gated_days = 0
        prev_gated = False
        if params.regime_gate:
            if sign < 0:
                raise HTTPException(
                    status_code=400,
                    detail="regime_gate is long-only: flattening a short book when the index "
                           "is below trend inverts its meaning. Run side='short' without the gate.")
            gate_symbol = params.gate_symbol or "Nifty 500"
            _gate_mode = getattr(params, "gate_mode", "sma") or "sma"
            _gate_len = (params.gate_fib_lookback if _gate_mode == "dynamic_fib"
                         else params.gate_sma)
            gate_start = start_date - timedelta(days=int(_gate_len * 1.6) + 30)
            async with StockDataManager() as stock_manager:
                gate_records = await stock_manager.get_price_data(
                    symbol=gate_symbol, start_date=gate_start, end_date=end_date,
                    limit=20000, sort_order=1)
            closes = [float(r.close_price) for r in gate_records]
            gdates = [r.date.strftime('%Y-%m-%d') for r in gate_records]
            if len(closes) < min(_gate_len, 60):
                raise HTTPException(
                    status_code=400,
                    detail=f"No/insufficient gate price data for '{gate_symbol}' "
                           f"(need history before {params.start_date}).")
            if _gate_mode == "dynamic_fib":
                from risk_gates import gate_map_fib
                ghighs = [float(r.high_price or r.close_price) for r in gate_records]
                glows = [float(r.low_price or r.close_price) for r in gate_records]
                gate_on = gate_map_fib(gdates, ghighs, glows, closes,
                                       params.gate_fib_lookback, params.gate_fib_level)
                logger.info(f"🚦 Regime gate active: {gate_symbol} vs DynamicFib "
                            f"{params.gate_fib_lookback}@{params.gate_fib_level} "
                            f"({sum(1 for v in gate_on.values() if not v)} risk-off sessions)")
            else:
                running = 0.0
                for k, c in enumerate(closes):
                    running += c
                    if k >= params.gate_sma:
                        running -= closes[k - params.gate_sma]
                    if k >= params.gate_sma - 1:
                        gate_on[gdates[k]] = c >= running / params.gate_sma
                logger.info(f"🚦 Regime gate active: {gate_symbol} vs {params.gate_sma}d SMA "
                            f"({sum(1 for v in gate_on.values() if not v)} risk-off sessions in range)")

        # Get all trading dates where we have both indicator and price data
        dates = sorted(set(indicator_data.keys()) & set(price_data.keys()))
        rebalance_dates = get_rebalance_dates(dates, params.rebalance_frequency, params.rebalance_date)
        
        logger.info(f"📅 Processing {len(dates)} trading days: {dates[:5]}...{dates[-5:] if len(dates) > 5 else ''}")
        logger.info(f"📊 Sample price data for first date: {list(price_data.get(dates[0], {}).keys())[:3] if dates else 'No dates'}")

        _slip_rss = float(getattr(params, "slippage_bps", 0) or 0) / 10000.0

        # Hard per-stock stop-loss. Long-only: on a short book "10% below the entry"
        # is a profit, not a loss, so silently reusing the long rule would invert the
        # control. Fail loud rather than model it wrong.
        _stop_loss_pct = float(getattr(params, "stop_loss_percent", 0) or 0)
        stop_loss_exit_count = 0
        if _stop_loss_pct > 0:
            if sign < 0:
                raise HTTPException(
                    status_code=400,
                    detail="stop_loss_percent is long-only: on a short book a fall below "
                           "the entry price is a gain. Run side='long', or omit the stop.")
            logger.info(f"🛑 Hard stop-loss active: exit any holding closing "
                        f"{_stop_loss_pct:.2f}% below its buy price")
        # Rich filter (compare/last_cross_dir signal groups) supersedes flat rules when present.
        # params.filter (ad-hoc) wins over a strategy's saved filter; absent → legacy rules path.
        _filter_src = getattr(params, "filter", None) or strategy.get("filter")
        filter_groups = _parse_filter_groups(_filter_src) if _filter_src else None
        _filter_cross_states = {}
        _filter_grids_used = _filter_grids(filter_groups) if filter_groups else []
        if filter_groups is not None:
            logger.info(f"🧩 Rich filter active: {len(filter_groups)} group(s)")

        for i, date_str in enumerate(dates):
            day_indicators = indicator_data.get(date_str, {})
            day_prices = price_data.get(date_str, {})
            
            # Skip if no data for this day
            if not day_indicators or not day_prices:
                continue

            # Daily stock-loan financing cost on the short book (no-op when long).
            if sign < 0 and params.annual_borrow_rate:
                short_mv = sum(current_holdings[s]["shares"] * day_prices[s]["close_price"]
                               for s in current_holdings if s in day_prices)
                cash_balance -= (params.annual_borrow_rate / 365.0) * short_mv

            # Qualify stocks: rich filter if present (maintains daily CrossState), else legacy rules.
            if filter_groups is not None:
                qualified_stocks = qualify_by_filter(
                    day_indicators, filter_groups, _filter_cross_states, _filter_grids_used)
            else:
                qualified_stocks = apply_strategy_rules(day_indicators, strategy["rules"])

            # Point-in-time gate: only stocks that were index members as-of this date are eligible.
            eligible_members = None
            if membership is not None:
                from membership import members_as_of as _members_as_of
                eligible_members = _members_as_of(membership, datetime.strptime(date_str, "%Y-%m-%d").date())
                qualified_stocks = [s for s in qualified_stocks if s["symbol"] in eligible_members]

            qualified_symbols = [stock["symbol"] for stock in qualified_stocks]

            # Per-stock Dynamic-Fib gate: a name below its own fib level is not
            # eligible today (holdings failing it will rank out at rebalance).
            if getattr(params, "stock_gate", False) and qualified_stocks:
                qualified_stocks = [s for s in qualified_stocks
                                    if stock_gate_on.get(s["symbol"], {}).get(date_str, True)]
                qualified_symbols = [s["symbol"] for s in qualified_stocks]

            # Book-level gates: index regime gate and/or portfolio-curve self-gate.
            # Risk-off empties the eligible set (forcing a flatten if anything is
            # held); the first risk-on day forces an immediate re-entry.
            force_gate_rebalance = False
            _risk_off = False
            if params.regime_gate and not gate_on.get(date_str, True):
                _risk_off = True
            if getattr(params, "portfolio_gate", False):
                if not pf_gate_on.get(date_str, True):
                    _risk_off = True
            if params.regime_gate or getattr(params, "portfolio_gate", False):
                if _risk_off:
                    gated_days += 1
                    qualified_stocks = []
                    qualified_symbols = []
                    force_gate_rebalance = bool(current_holdings)
                    prev_gated = True
                else:
                    if prev_gated:
                        force_gate_rebalance = True
                    prev_gated = False

            # Track additions and exits
            new_added = []
            exited = []
            exited_details = []  # Initialize detailed exit tracking

            # Rebalance portfolio if needed (dynamic = daily, else use schedule)
            should_rebalance = (params.rebalance_frequency == "dynamic" or
                              date_str in rebalance_dates or
                              force_gate_rebalance or
                              i == 0)  # Always rebalance on first day
            # A wiped-out short book stops trading — no rebalances after ruin.
            if ruined:
                should_rebalance = False
            
            if should_rebalance:
                rebalance_reason = "First day" if i == 0 else ("Dynamic" if params.rebalance_frequency == "dynamic" else "Scheduled")
                logger.info(f"📅 {rebalance_reason} rebalancing triggered on {date_str}")

            # ── Hard per-stock stop-loss (intra-period risk control) ──────────────
            # Sell any holding that closed at/below buy_price*(1-pct) and park the
            # proceeds in cash until the next scheduled rebalance redeploys them.
            # Skipped on rebalance days: the book is re-formed from scratch there, so
            # stopping out first would only add a same-day sell/rebuy round-trip and
            # its charges without changing the resulting portfolio.
            if _stop_loss_pct > 0 and current_holdings and not should_rebalance:
                breached = stop_loss_exits(current_holdings, day_prices, _stop_loss_pct)
                if breached:
                    sl_sell_trades = [{"symbol": b["symbol"], "quantity": b["shares"],
                                       "price": b["exit_price"] * (1 - _slip_rss)}
                                      for b in breached]
                    if params.include_brokerage:
                        _sl_calc = BrokerageCalculator(
                            default_exchange=params.exchange,
                            custom_brokerage_rate=params.custom_brokerage_rate)
                        _sl_res = _sl_calc.calculate_portfolio_rebalance_charges(sl_sell_trades, [])
                        cash_balance += _sl_res["net_sell_proceeds"]
                        cumulative_charges += _sl_res["total_sell_charges"]
                    else:
                        cash_balance += sum(t["quantity"] * t["price"] for t in sl_sell_trades)

                    for b in breached:
                        del current_holdings[b["symbol"]]
                        exited.append(b["symbol"])
                        exited_details.append({
                            "symbol": b["symbol"], "company_name": b["symbol"],
                            "quantity": b["shares"], "avg_price": b["avg_price"],
                            "exit_price": b["exit_price"], "pnl": b["pnl"],
                            "pnl_percent": b["pnl_percent"], "sector": "Unknown",
                            "exit_reason": "stop_loss"})
                        logger.info(f"🛑 Stop-loss exit {b['symbol']} on {date_str}: "
                                    f"{b['pnl_percent']:.2f}% (buy ₹{b['avg_price']:.2f} "
                                    f"→ close ₹{b['exit_price']:.2f})")
                    stop_loss_exit_count += len(breached)

            if should_rebalance and sign < 0:
                # ── SHORT rebalance (full turnover) ───────────────────────────────
                # Same selection as long (rank strongest, top-N) — we just short them.
                current_portfolio_value = calculate_current_portfolio_value(
                    current_holdings, day_prices, cash_balance, sign)
                rebalance_value = (params.portfolio_base_value if i == 0
                                   else max(current_portfolio_value, params.portfolio_base_value * 0.01))

                holdings_for_ranking = current_holdings
                if eligible_members is not None:
                    holdings_for_ranking = {s: h for s, h in current_holdings.items() if s in eligible_members}

                selected_stocks, momentum_added, momentum_removed = await select_top_stocks_by_momentum(
                    qualified_stocks=qualified_stocks,
                    current_holdings=holdings_for_ranking,
                    price_data_history=price_data,
                    indicator_data_history=indicator_data,
                    current_date=date_str,
                    max_holdings=params.max_holdings,
                    momentum_method=params.momentum_ranking,
                    price_series=price_series,
                    indicator_series=indicator_series,
                    current_date_obj=datetime.strptime(date_str, "%Y-%m-%d"),
                )
                selected_symbols = [stock["symbol"] for stock in selected_stocks]
                new_added = momentum_added

                # Exit details (signed P&L) for every name leaving the book.
                exited_symbols = list(momentum_removed) if momentum_removed else []
                for symbol in list(current_holdings.keys()):
                    if symbol not in selected_symbols:
                        holding = current_holdings[symbol]
                        if symbol in day_prices:
                            current_price = day_prices[symbol]["close_price"]
                            exit_pnl = sign * (current_price - holding["avg_price"]) * holding["shares"]
                            exit_pnl_percent = (sign * ((current_price / holding["avg_price"]) - 1) * 100
                                                if holding["avg_price"] > 0 else 0)
                            exited_details.append({
                                "symbol": symbol, "company_name": symbol,
                                "quantity": holding["shares"], "avg_price": holding["avg_price"],
                                "exit_price": current_price, "pnl": exit_pnl,
                                "pnl_percent": exit_pnl_percent, "sector": "Unknown"})
                        if symbol not in exited_symbols:
                            exited_symbols.append(symbol)
                exited = exited_symbols

                sr = _short_rebalance(current_holdings, selected_symbols, day_prices,
                                      rebalance_value, params, holding_periods)
                current_holdings = sr["new_holdings"]
                cash_balance = sr["remaining_cash"]
                daily_charges = sr["daily_charges"]
                rebalance_trade_details = sr["trade_details"]
                portfolio_value = calculate_current_portfolio_value(
                    current_holdings, day_prices, cash_balance, sign)

                rebalance_count += 1
                holding_periods = {s: (holding_periods.get(s, 0) + 1 if s in holding_periods else 0)
                                   for s in selected_symbols}
                logger.info(f"📉 SHORT rebalance on {date_str}: equity ₹{current_portfolio_value:,.2f} "
                            f"→ ₹{portfolio_value:,.2f}, {len(current_holdings)} shorts")

            elif should_rebalance :
                # Calculate current portfolio value before rebalancing
                current_portfolio_value = calculate_current_portfolio_value(current_holdings, day_prices, cash_balance)
                prev_holdings = current_holdings.copy()

                # Use current portfolio value (preserve capital) or base value on first day
                if i == 0:  # First day
                    rebalance_value = params.portfolio_base_value
                else:
                    # Use current portfolio value to preserve capital - don't amplify
                    rebalance_value = current_portfolio_value
                    # Safety floor to prevent portfolio from going to zero
                    rebalance_value = max(rebalance_value, params.portfolio_base_value * 0.01)
                
                logger.info(f"🔄 Rebalancing on {date_str}: Current Value = ₹{current_portfolio_value:,.2f}, Rebalance Value = ₹{rebalance_value:,.2f}")
                
                # Log portfolio before rebalancing
                logger.info(f"📊 BEFORE Rebalancing: {len(current_holdings)} holdings worth ₹{current_portfolio_value:,.2f}")
                for symbol, holding in current_holdings.items():
                    if symbol in day_prices:
                        current_price = day_prices[symbol]["close_price"]
                        market_value = holding["shares"] * current_price
                        logger.info(f"  📈 {symbol}: {holding['shares']:.2f} shares @ ₹{current_price:.2f} = ₹{market_value:,.2f}")
                
                # In point-in-time mode, a holding that has left the index is no longer eligible:
                # drop it from the ranking pool so it isn't retained (the real current_holdings is
                # kept intact for downstream exit accounting, which will sell it).
                holdings_for_ranking = current_holdings
                if eligible_members is not None:
                    holdings_for_ranking = {s: h for s, h in current_holdings.items() if s in eligible_members}

                # Apply momentum-based portfolio limit selection
                selected_stocks, momentum_added, momentum_removed = await select_top_stocks_by_momentum(
                    qualified_stocks=qualified_stocks,
                    current_holdings=holdings_for_ranking,
                    price_data_history=price_data,
                    indicator_data_history=indicator_data,
                    current_date=date_str,
                    max_holdings=params.max_holdings,
                    momentum_method=params.momentum_ranking,
                    price_series=price_series,
                    indicator_series=indicator_series,
                    current_date_obj=datetime.strptime(date_str, "%Y-%m-%d")
                )
                
                # Update symbols list to only selected stocks
                selected_symbols = [stock["symbol"] for stock in selected_stocks]
                
                # Track additions and exits based on momentum selection
                new_added = momentum_added
                # Start with momentum-based exits (just symbol names)
                exited_symbols = list(momentum_removed) if momentum_removed else []
                
                # Calculate exit details for ALL stocks that will be removed BEFORE deleting them
                for symbol in list(current_holdings.keys()):
                    if symbol not in selected_symbols:
                        # Calculate exit performance before deleting
                        holding = current_holdings[symbol]
                        if symbol in day_prices:
                            current_price = day_prices[symbol]["close_price"]
                            exit_pnl = (current_price - holding["avg_price"]) * holding["shares"]
                            exit_pnl_percent = ((current_price / holding["avg_price"]) - 1) * 100 if holding["avg_price"] > 0 else 0
                            
                            exit_details = {
                                "symbol": symbol,
                                "company_name": symbol,  # TODO: Get from company mapping
                                "quantity": holding["shares"],
                                "avg_price": holding["avg_price"],
                                "exit_price": current_price,
                                "pnl": exit_pnl,
                                "pnl_percent": exit_pnl_percent,
                                "sector": "Unknown"  # TODO: Get from mapping
                            }
                            exited_details.append(exit_details)
                            logger.info(f"📤 Exit details calculated for {symbol}: {exit_pnl_percent:.2f}% P&L")
                        
                        # Add to exit symbols list if not already there
                        if symbol not in exited_symbols:
                            exited_symbols.append(symbol)
                
                # Now remove the holdings and add cash from sales
                for symbol in list(current_holdings.keys()):
                    if symbol not in selected_symbols:
                        # Calculate sale proceeds before deleting holding
                        if symbol in day_prices:
                            holding = current_holdings[symbol]
                            sale_price = day_prices[symbol]["close_price"] * (1 - _slip_rss)
                            sale_proceeds = holding["shares"] * sale_price
                            cash_balance += sale_proceeds
                            logger.info(f"💰 Sold {symbol}: {holding['shares']:.2f} shares @ ₹{sale_price:.2f} = ₹{sale_proceeds:,.2f}")
                        
                        del current_holdings[symbol]
                
                # Set the final exited list
                exited = exited_symbols
                
                # Initialize daily charges tracking
                daily_charges = {"total_charges": 0.0, "buy_charges": 0.0, "sell_charges": 0.0, "charge_breakdown": {}}
                rebalance_trade_details = []
                
                # Choose rebalancing method based on brokerage settings
                if params.include_brokerage:
                    # Use charge-aware rebalancing
                    logger.info(f"💰 Using charge-aware rebalancing with {params.exchange} exchange")
                    
                    rebalance_result = await rebalance_portfolio_with_charges(
                        current_holdings=current_holdings,
                        selected_symbols=selected_symbols,
                        day_prices=day_prices,
                        available_capital=rebalance_value,
                        params=params,
                        holding_periods=holding_periods
                    )
                    
                    # Update holdings with charge tracking
                    current_holdings = rebalance_result["new_holdings"]
                    
                    # Track daily charges
                    daily_charges = rebalance_result["charge_breakdown"]
                    rebalance_trade_details = rebalance_result["trade_details"]
                    
                    # Adjust portfolio value for charges (charges are already deducted in rebalance logic)
                    new_portfolio_value = calculate_current_portfolio_value(current_holdings, day_prices, 0)
                    cash_balance = rebalance_result["remaining_cash"]  # Update cash balance from rebalancing
                    new_portfolio_value += cash_balance
                    
                    logger.info(f"💰 Rebalancing with charges: Total charges = ₹{daily_charges['total_charges']:,.2f}")
                    logger.info(f"📊 Buy charges: ₹{daily_charges['buy_charges']:,.2f}, Sell charges: ₹{daily_charges['sell_charges']:,.2f}")
                    logger.info(f"🏦 Remaining cash: ₹{rebalance_result['remaining_cash']:,.2f}")
                    
                else:
                    # Rebalancing without brokerage charges
                    if selected_symbols:
                        if params.rebalance_type == "skewed":
                            # Skewed allocation based on holding periods
                            logger.info(f"📊 Using skewed allocation based on holding periods")
                            skewed_allocations = calculate_skewed_allocation(
                                selected_symbols=selected_symbols,
                                holding_periods=holding_periods,
                                total_value=cash_balance  # Use available cash
                            )
                            
                            # Clear current holdings for fresh allocation
                            current_holdings = {}
                            total_invested = 0
                            
                            # Apply skewed allocations
                            for symbol in selected_symbols:
                                if symbol in day_prices:
                                    allocation_amount = skewed_allocations[symbol]
                                    price = day_prices[symbol]["close_price"]
                                    target_shares = allocation_amount / price
                                    investment_amount = target_shares * price
                                    
                                    current_holdings[symbol] = {
                                        "shares": target_shares,
                                        "avg_price": price
                                    }
                                    
                                    total_invested += investment_amount
                                    logger.info(f"📈 Skewed buy {symbol}: {target_shares:.2f} shares @ ₹{price:.2f} = ₹{investment_amount:,.2f}")
                            
                            # Update cash balance after purchases
                            cash_balance -= total_invested
                            logger.info(f"💵 Cash remaining after skewed allocation: ₹{cash_balance:,.2f}")
                                    
                        else:
                            # Original equal weight allocation
                            if selected_symbols:  # Add check to prevent division by zero
                                # Use available cash for investments
                                available_cash = cash_balance
                                target_value_per_stock = available_cash / len(selected_symbols)
                                
                                logger.info(f"💰 Equal allocation (no charges): ₹{target_value_per_stock:,.2f} per stock across {len(selected_symbols)} stocks")
                                logger.info(f"💵 Available cash for investment: ₹{available_cash:,.2f}")
                                
                                # Clear current holdings for fresh allocation
                                current_holdings = {}
                                total_invested = 0
                                
                                # Rebalance all holdings to equal weights
                                for symbol in selected_symbols:
                                    if symbol in day_prices:
                                        price = day_prices[symbol]["close_price"] * (1 + _slip_rss)
                                        target_shares = target_value_per_stock / price
                                        investment_amount = target_shares * price
                                        
                                        current_holdings[symbol] = {
                                            "shares": target_shares,
                                            "avg_price": price
                                        }
                                        
                                        total_invested += investment_amount
                                        logger.info(f"📈 Bought {symbol}: {target_shares:.2f} shares @ ₹{price:.2f} = ₹{investment_amount:,.2f}")
                                
                                # Update cash balance after purchases
                                cash_balance -= total_invested
                                logger.info(f"💵 Cash remaining after purchases: ₹{cash_balance:,.2f}")
                            else:
                                logger.warning(f"⚠️  No stocks selected for rebalancing on {date_str}, keeping previous holdings")
                                current_holdings = prev_holdings
                    else:
                        current_holdings = prev_holdings
                    
                    # Recalculate portfolio value after rebalancing
                    new_portfolio_value = calculate_current_portfolio_value(current_holdings, day_prices, cash_balance)
                
                # Log portfolio after rebalancing
                logger.info(f"📊 AFTER Rebalancing: {len(current_holdings)} holdings worth ₹{new_portfolio_value:,.2f}")
                for symbol, holding in current_holdings.items():
                    if symbol in day_prices:
                        current_price = day_prices[symbol]["close_price"]
                        market_value = holding["shares"] * current_price
                        logger.info(f"  📈 {symbol}: {holding['shares']:.2f} shares @ ₹{current_price:.2f} = ₹{market_value:,.2f}")
                
                logger.info(f"✅ Rebalancing complete: ₹{current_portfolio_value:,.2f} → ₹{new_portfolio_value:,.2f}")
                portfolio_value = new_portfolio_value
                
                # Update holding periods after rebalancing
                rebalance_count += 1
                logger.info(f"📊 Updating holding periods after rebalance #{rebalance_count}")
                
                # Increment holding periods for stocks that remain in portfolio
                new_holding_periods = {}
                for symbol in selected_symbols:
                    if symbol in holding_periods:
                        # Stock was already held, increment its period
                        new_holding_periods[symbol] = holding_periods[symbol] + 1
                        logger.info(f"  📈 {symbol}: holding period {holding_periods[symbol]} → {new_holding_periods[symbol]}")
                    else:
                        # New stock, start with 0 periods
                        new_holding_periods[symbol] = 0
                        logger.info(f"  🆕 {symbol}: new stock, holding period = 0")
                
                # Replace holding_periods with updated values
                holding_periods = new_holding_periods
            else:
                if current_holdings:
                    # Just update portfolio value based on price changes (signed for shorts)
                    portfolio_value = calculate_current_portfolio_value(current_holdings, day_prices, cash_balance, sign)
                else:
                    # No holdings, portfolio value is just cash
                    portfolio_value = cash_balance

            # Margin-call liquidation (shorts only): force-close the book when net
            # equity falls to the maintenance margin of the short exposure — well
            # before equity reaches zero. Keep the small positive cash residual and
            # halt. Models reality and avoids the near-zero-equity return blow-up that
            # poisons Sharpe/CAGR.
            if not ruined and sign < 0 and current_holdings:
                short_mv = sum(current_holdings[s]["shares"] * day_prices[s]["close_price"]
                               for s in current_holdings if s in day_prices)
                if short_mv > 0 and portfolio_value <= SHORT_MAINTENANCE_MARGIN * short_mv:
                    ruined = True
                    ruin_date = date_str
                    cash_balance = max(portfolio_value, 0.0)
                    current_holdings = {}
                    portfolio_value = cash_balance
                    logger.warning(f"💥 SHORT book margin-called (liquidated) on {date_str}")

            # Calculate day PnL
            day_pnl = portfolio_value - prev_portfolio_value
            prev_portfolio_value = portfolio_value
            
            # Calculate benchmark value
            current_benchmark_close = benchmark_prices.get(date_str)
            if current_benchmark_close is not None:
                if prev_benchmark_close is not None:
                    # Calculate benchmark daily return and apply to benchmark value
                    benchmark_return = (current_benchmark_close / prev_benchmark_close) - 1
                    benchmark_value = benchmark_value * (1 + benchmark_return)
                else:
                    # First day - initialize
                    benchmark_value = params.portfolio_base_value
                
                prev_benchmark_close = current_benchmark_close
            else:
                logger.warning(f"⚠️ No benchmark data for {date_str}, using previous value")
            
            # Create holdings list for frontend
            holdings_list = []
            total_portfolio_value = portfolio_value  # Use current portfolio value for weight calculation
            
            for symbol, holding in current_holdings.items():
                if symbol in day_prices:
                    current_price = day_prices[symbol]["close_price"]
                    market_value = holding["shares"] * current_price
                    # P&L sign-flips for shorts: profit when price falls below avg.
                    pnl = sign * (current_price - holding["avg_price"]) * holding["shares"]
                    pnl_percent = (sign * ((current_price / holding["avg_price"]) - 1) * 100
                                   if holding["avg_price"] > 0 else 0)

                    # Calculate portfolio weight percentage
                    weight_percent = (market_value / total_portfolio_value * 100) if total_portfolio_value > 0 else 0
                    
                    # Get holding period and allocation weight
                    holding_periods_count = holding_periods.get(symbol, 0)
                    allocation_weight = 1.0 + (holding_periods_count * 0.3)
                    
                    holding_info = {
                        "symbol": symbol,
                        "company_name": symbol,  # TODO: Get from company mapping
                        "quantity": holding["shares"],
                        "avg_price": holding["avg_price"],
                        "current_price": current_price,
                        "market_value": market_value,
                        "pnl": pnl,
                        "pnl_percent": pnl_percent,
                        "sector": "Unknown",  # TODO: Get from mapping
                        "weight": weight_percent,  # Portfolio weight percentage
                        "holding_periods": holding_periods_count,  # Number of consecutive periods held
                        "allocation_weight": allocation_weight,  # Skewed allocation weight
                        "side": params.side  # 'long' | 'short' — badge short positions in the UI
                    }
                    
                    # Add charge tracking if available
                    if "total_cost" in holding:
                        holding_info["total_cost"] = holding["total_cost"]
                    # Removed cumulative_charges aggregation from individual holdings
                    
                    holdings_list.append(holding_info)
            
            # Sort holdings by allocation weight (descending) for skewed rebalancing display
            if params.rebalance_type == "skewed":
                holdings_list.sort(key=lambda x: x["allocation_weight"], reverse=True)
            else:
                # For equal weight, sort by market value (descending)
                holdings_list.sort(key=lambda x: x["market_value"], reverse=True)
            
            # Track cumulative charges
            if should_rebalance and params.include_brokerage:
                cumulative_charges += daily_charges.get("total_charges", 0.0)
            
            # Sanitize trade details for JSON serialization
            sanitized_trade_details = []
            if should_rebalance and rebalance_trade_details:
                for trade in rebalance_trade_details:
                    sanitized_trade = {}
                    for key, value in trade.items():
                        if isinstance(value, datetime):
                            sanitized_trade[key] = value.isoformat()
                        elif hasattr(value, '__dict__'):
                            # Handle nested objects (like TransactionCharges)
                            sanitized_nested = {}
                            for nested_key, nested_value in value.__dict__.items():
                                if isinstance(nested_value, datetime):
                                    sanitized_nested[nested_key] = nested_value.isoformat()
                                else:
                                    sanitized_nested[nested_key] = nested_value
                            sanitized_trade[key] = sanitized_nested
                        else:
                            sanitized_trade[key] = value
                    sanitized_trade_details.append(sanitized_trade)
            
            # Create day result with enhanced charge tracking
            day_result = {
                "date": date_str,
                "portfolio_value": portfolio_value,
                "benchmark_value": benchmark_value,
                "holdings": holdings_list,
                "new_added": new_added,
                "exited": exited,
                "exited_details": exited_details,
                "cash": cash_balance,  # Actual cash balance
                "total_pnl": portfolio_value - params.portfolio_base_value,
                "day_pnl": day_pnl,
                "benchmark_price": current_benchmark_close or 0,
                
                # Enhanced charge tracking
                "daily_charges": daily_charges if should_rebalance else {"total_charges": 0.0, "buy_charges": 0.0, "sell_charges": 0.0},
                "cumulative_charges": cumulative_charges,
                "charge_impact_percent": (cumulative_charges / params.portfolio_base_value) * 100,
                "trade_details": sanitized_trade_details,
                "brokerage_enabled": params.include_brokerage,
                "exchange_used": params.exchange if params.include_brokerage else None
            }
            
            # Debug logging for exit details
            if exited_details:
                logger.info(f"📤 Day {date_str}: Created {len(exited_details)} exit details for symbols: {[e['symbol'] for e in exited_details]}")
            elif exited:
                logger.info(f"⚠️ Day {date_str}: Have {len(exited)} exits but no exit details: {exited}")
            
            simulation_results.append(day_result)
        
        # Calculate comprehensive summary statistics with charge analytics
        final_portfolio_value = portfolio_value
        total_return = (final_portfolio_value / params.portfolio_base_value - 1) * 100 if params.portfolio_base_value > 0 else 0
        benchmark_return = (benchmark_value / params.portfolio_base_value - 1) * 100 if params.portfolio_base_value > 0 else 0
        
        # Calculate cumulative charge impact
        total_cumulative_charges = cumulative_charges
        charge_impact_percent = (total_cumulative_charges / params.portfolio_base_value) * 100
        
        # Calculate theoretical return without charges (approximate)
        theoretical_value_without_charges = final_portfolio_value + total_cumulative_charges
        theoretical_return_without_charges = (theoretical_value_without_charges / params.portfolio_base_value - 1) * 100
        
        # Calculate max drawdown
        peak_value = params.portfolio_base_value
        max_drawdown = 0
        drawdown_details = []
        for result in simulation_results:
            peak_value = max(peak_value, result["portfolio_value"])
            drawdown = (result["portfolio_value"] / peak_value - 1) * 100
            max_drawdown = min(max_drawdown, drawdown)
            drawdown_details.append({
                "date": result["date"],
                "portfolio_value": result["portfolio_value"],
                "peak_value": peak_value,
                "drawdown_percent": drawdown
            })
        
        # Calculate rebalance statistics
        rebalance_events = [result for result in simulation_results if result["daily_charges"]["total_charges"] > 0]
        total_rebalances = len(rebalance_events)
        avg_rebalance_cost = (total_cumulative_charges / total_rebalances) if total_rebalances > 0 else 0
        
        # Calculate charge breakdown analytics
        charge_analytics = {
            "total_cumulative_charges": round(total_cumulative_charges, 2),
            "charge_impact_percent": round(charge_impact_percent, 4),
            "total_rebalances": total_rebalances,
            "avg_cost_per_rebalance": round(avg_rebalance_cost, 2),
            "charge_drag_on_returns": round(theoretical_return_without_charges - total_return, 4),
            "theoretical_return_without_charges": round(theoretical_return_without_charges, 2),
            "charge_as_percent_of_final_value": round((total_cumulative_charges / final_portfolio_value) * 100, 4) if final_portfolio_value > 0 else 0
        }
        
        # Enhanced charge breakdown by components (aggregate from all rebalances)
        if params.include_brokerage and rebalance_events:
            aggregate_breakdown = {
                "total_buy_charges": 0.0,
                "total_sell_charges": 0.0,
                "stt_total": 0.0,
                "transaction_charges_total": 0.0,
                "sebi_charges_total": 0.0,
                "stamp_duty_total": 0.0,
                "brokerage_total": 0.0,
                "gst_total": 0.0
            }
            
            # Aggregate charges from all trade details
            for result in simulation_results:
                if result["trade_details"]:
                    for trade in result["trade_details"]:
                        if "charges" in trade and isinstance(trade["charges"], dict):
                            charges = trade["charges"]
                            aggregate_breakdown["stt_total"] += charges.get("stt", 0.0)
                            aggregate_breakdown["transaction_charges_total"] += charges.get("transaction_charges", 0.0)
                            aggregate_breakdown["sebi_charges_total"] += charges.get("sebi_charges", 0.0)
                            aggregate_breakdown["stamp_duty_total"] += charges.get("stamp_duty", 0.0)
                            aggregate_breakdown["brokerage_total"] += charges.get("brokerage", 0.0)
                            aggregate_breakdown["gst_total"] += charges.get("gst", 0.0)
                
                aggregate_breakdown["total_buy_charges"] += result["daily_charges"].get("buy_charges", 0.0)
                aggregate_breakdown["total_sell_charges"] += result["daily_charges"].get("sell_charges", 0.0)
            
            charge_analytics["component_breakdown"] = {
                "stt": round(aggregate_breakdown["stt_total"], 2),
                "transaction_charges": round(aggregate_breakdown["transaction_charges_total"], 2),
                "sebi_charges": round(aggregate_breakdown["sebi_charges_total"], 2),
                "stamp_duty": round(aggregate_breakdown["stamp_duty_total"], 2),
                "brokerage": round(aggregate_breakdown["brokerage_total"], 2),
                "gst": round(aggregate_breakdown["gst_total"], 2),
                "total_buy_charges": round(aggregate_breakdown["total_buy_charges"], 2),
                "total_sell_charges": round(aggregate_breakdown["total_sell_charges"], 2)
            }
        
        # Calculate performance metrics
        daily_returns = []
        for i in range(1, len(simulation_results)):
            prev_portfolio_value = simulation_results[i-1]["portfolio_value"]
            curr_portfolio_value = simulation_results[i]["portfolio_value"]
            
            if prev_portfolio_value > 0:
                daily_return = (curr_portfolio_value / prev_portfolio_value) - 1
                daily_returns.append(daily_return)
            else:
                # Skip this calculation if previous value is 0
                logger.warning(f"⚠️ Skipping daily return calculation: previous portfolio value is 0 on day {i-1}")
                continue
        
        # Calculate Sharpe ratio (assuming 6% risk-free rate)
        if daily_returns:
            import numpy as np
            daily_returns_array = np.array(daily_returns)
            avg_daily_return = np.mean(daily_returns_array)
            daily_volatility = np.std(daily_returns_array)
            
            # Annualize metrics (assuming 252 trading days)
            annual_return = (1 + avg_daily_return) ** 252 - 1
            annual_volatility = daily_volatility * np.sqrt(252)
            
            # Calculate Sharpe ratio with 6% risk-free rate
            risk_free_rate = 0.06
            sharpe_ratio = (annual_return - risk_free_rate) / annual_volatility if annual_volatility > 0 else 0
        else:
            sharpe_ratio = 0
        
        # Point-in-time coverage: of the ever-members, which had price data available.
        point_in_time_coverage = None
        if membership is not None:
            priced_symbols = {s for day in price_data.values() for s in day}
            missing = sorted(s for s in universe_symbols if s not in priced_symbols)
            point_in_time_coverage = {
                "total_ever_members": len(universe_symbols),
                "covered": len(universe_symbols) - len(missing),
                "missing": missing,
            }
            if missing:
                logger.info(f"⚠ Point-in-time coverage: {len(missing)} ever-members had no price data: {missing}")

        # Sanitize all results for JSON serialization
        sanitized_results = sanitize_for_json({
            "params": params.dict(),
            "benchmark_symbol": benchmark_symbol,
            "point_in_time": membership is not None,
            "point_in_time_coverage": point_in_time_coverage,
            "results": simulation_results,
            "charge_analytics": charge_analytics,
            "summary": {
                "total_return": round(total_return, 2),
                "benchmark_return": round(benchmark_return, 2),
                "alpha": round(total_return - benchmark_return, 2),
                "max_drawdown": round(max_drawdown, 2),
                "sharpe_ratio": round(sharpe_ratio, 3),
                "total_trades": total_rebalances,
                "ruined": ruined,
                "ruin_date": ruin_date,
                "brokerage_enabled": params.include_brokerage,
                "exchange": params.exchange if params.include_brokerage else None,
                "theoretical_return_without_charges": round(theoretical_return_without_charges, 2) if params.include_brokerage else None,
                "charge_impact": round(charge_impact_percent, 4) if params.include_brokerage else 0,
                "gated_days": gated_days,
                "stop_loss_percent": _stop_loss_pct or None,
                "stop_loss_exits": stop_loss_exit_count,
                "regime_gate": ({"gate_symbol": params.gate_symbol or "Nifty 500",
                                 "gate_sma": params.gate_sma}
                                if params.regime_gate else None)
            }
        })
        
        return sanitized_results
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"❌ Error in simulation logic: {e}")
        logger.error(f"❌ Full traceback: {error_details}")
        raise

def _parse_filter_groups(src):
    """Normalize a filter into [[Signal,...],...].
    Accepts params.filter (already Signal models) or a strategy doc's raw filter
    (list of list of plain dicts from Mongo)."""
    groups = []
    for g in (src or []):
        groups.append([s if isinstance(s, Signal) else Signal(**s) for s in g])
    return groups


def _filter_grids(filter_groups):
    """(metric, tuple(grid)) pairs the filter's last_cross_dir signals track."""
    grids = set()
    for g in filter_groups:
        for s in g:
            if s.kind == "last_cross_dir":
                grids.add((s.metric, tuple(s.grid)))
    return list(grids)


def qualify_by_filter(day_indicators, filter_groups, cross_states, grids):
    """Rich-filter analogue of apply_strategy_rules: a stock qualifies when ANY
    signal group fires (AND within a group, OR across groups). Maintains per-symbol
    CrossState so last_cross_dir is correct; reuses the wave signal evaluator.
    Returns the same shape apply_strategy_rules returns (list of stock_data dicts)."""
    qualified = []
    for symbol, stock_data in day_indicators.items():
        cs = cross_states.get(symbol)
        if cs is None:
            cs = CrossState(grids)
            cross_states[symbol] = cs
        prev = cs.prev_snapshot()
        cs.update(stock_data)
        if any_group_fires(filter_groups, stock_data, prev, cs.last_dirs):
            qualified.append(stock_data)
    return qualified


def apply_strategy_rules(day_indicators, rules):
    """Apply strategy rules to filter qualified stocks"""
    qualified_stocks = []
    
    for symbol, stock_data in day_indicators.items():
        qualifies = True
        
        for rule in rules:
            metric_value = stock_data.get(rule["metric"]) or 0  # Handle None values
            threshold = rule["threshold"]
            operator = rule["operator"]
            
            if operator == ">" and not (metric_value > threshold):
                qualifies = False
                break
            elif operator == "<" and not (metric_value < threshold):
                qualifies = False
                break
            elif operator == ">=" and not (metric_value >= threshold):
                qualifies = False
                break
            elif operator == "<=" and not (metric_value <= threshold):
                qualifies = False
                break
            elif operator == "==" and not (metric_value == threshold):
                qualifies = False
                break
            elif operator == "!=" and not (metric_value != threshold):
                qualifies = False
                break
        
        if qualifies:
            qualified_stocks.append(stock_data)
    
    return qualified_stocks

def stop_loss_exits(current_holdings: dict, day_prices: dict, stop_loss_percent: float) -> list:
    """Long holdings whose close has breached a hard stop measured from the buy price.

    A position is stopped when close <= avg_price * (1 - pct/100) — 'at or below',
    so an exact -10.00% close triggers. The reference is `avg_price` (where the
    position was actually opened), not a trailing high, so this is a fixed stop.

    A symbol with no print today is never stopped: absence of a price is not
    evidence of a breach, and exiting blind would invent a fill that never existed.

    Returns exit records; the caller performs the sale (slippage, charges, cash).
    """
    pct = float(stop_loss_percent or 0)
    if pct <= 0 or not current_holdings:
        return []

    exits = []
    for symbol, holding in current_holdings.items():
        avg_price = float(holding.get("avg_price") or 0)
        if avg_price <= 0 or symbol not in day_prices:
            continue
        close = float(day_prices[symbol]["close_price"])
        if close <= avg_price * (1 - pct / 100.0):
            shares = holding["shares"]
            exits.append({
                "symbol": symbol,
                "shares": shares,
                "avg_price": avg_price,
                "exit_price": close,
                "pnl": (close - avg_price) * shares,
                "pnl_percent": ((close / avg_price) - 1) * 100,
            })
    return exits


def calculate_current_portfolio_value(current_holdings, day_prices, cash_balance=0, sign=1.0):
    """Calculate current portfolio value (equity) = cash + sign·Σ(shares·price).

    sign=+1 for a long book (default — behaviour unchanged), -1 for a short book
    where the holdings' market value is a buy-back liability subtracted from cash.
    """
    total_value = 0

    # Signed value of stock holdings
    for symbol, holding in current_holdings.items():
        if symbol in day_prices:
            current_price = day_prices[symbol]["close_price"]
            total_value += sign * holding["shares"] * current_price

    # Add cash balance
    total_value += cash_balance

    return total_value

def get_rebalance_dates(dates, frequency, date_type):
    """Generate rebalance dates based on frequency and date type.

    Completed periods: pick first/mid/last of the period's loaded trading days
    (unchanged historical behavior). The FINAL period is usually still
    incomplete on an open-ended run — there, mid/last are anchored to the full
    period's business-day calendar: rebalance on the first loaded trading day
    >= that target, or not at all until the target date arrives. Otherwise
    "mid of July" computed from 3 loaded sessions lands on Jul 2 and drifts
    forward with every data refresh (fires early, rewrites history run-to-run).
    """
    import calendar as _cal

    if frequency not in ("monthly", "weekly", "quarterly"):
        return set()

    def period_key(dobj):
        if frequency == "weekly":
            return (dobj.year, dobj.isocalendar()[1])
        if frequency == "quarterly":
            return (dobj.year, (dobj.month - 1) // 3 + 1)
        return (dobj.year, dobj.month)

    def expected_busdays(pdates):
        """Mon-Fri calendar of the full period containing the period's dates."""
        dlast = datetime.strptime(pdates[-1], "%Y-%m-%d").date()
        if frequency == "weekly":
            monday = dlast - timedelta(days=dlast.weekday())
            return [monday + timedelta(days=i) for i in range(5)]
        if frequency == "quarterly":
            q = (dlast.month - 1) // 3 + 1
            months = range(3 * q - 2, 3 * q + 1)
        else:
            months = [dlast.month]
        out = []
        for m in months:
            for d in range(1, _cal.monthrange(dlast.year, m)[1] + 1):
                dt = date(dlast.year, m, d)
                if dt.weekday() < 5:
                    out.append(dt)
        return out

    groups = {}
    for date_str in dates:
        dobj = datetime.strptime(date_str, "%Y-%m-%d")
        groups.setdefault(period_key(dobj), []).append(date_str)

    rebalance_dates = set()
    keys = sorted(groups.keys())
    for i, key in enumerate(keys):
        pdates = sorted(groups[key])
        if date_type == "first":
            rebalance_dates.add(pdates[0])
            continue
        exp = expected_busdays(pdates)
        incomplete = (i == len(keys) - 1) and exp and pdates[-1] < exp[-1].strftime("%Y-%m-%d")
        if not incomplete:
            if date_type == "last":
                rebalance_dates.add(pdates[-1])
            elif date_type == "mid":
                rebalance_dates.add(pdates[len(pdates) // 2])
        else:
            target = exp[len(exp) // 2] if date_type == "mid" else exp[-1]
            target_s = target.strftime("%Y-%m-%d")
            candidates = [d for d in pdates if d >= target_s]
            if candidates:
                rebalance_dates.add(candidates[0])
            # else: the period hasn't reached its mid/last yet — no rebalance.

    return rebalance_dates

