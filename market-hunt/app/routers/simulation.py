"""Simulation endpoints: strategy CRUD + run/multi-run/debug/charges/tradebook (moved verbatim from api_server)."""
import asyncio
import io
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
from bson import ObjectId
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, model_validator

from app.db import mongo_conn
from app.serialization import sanitize_for_json, serialize_doc
from brokerage_calculator import (
    BrokerageCalculator,
    calculate_single_trade_charges,
    estimate_portfolio_charges,
)
from engine.stateless import (
    apply_strategy_rules,
    build_indicator_series,
    build_price_series,
    calculate_monthly_metrics,
    calculate_stock_momentum,
    calculate_stock_momentum_fast,
    calculate_skewed_allocation,
    get_rebalance_dates,
    qualify_by_filter,
    rebalance_portfolio_with_charges,
    run_strategy_simulation,
    run_strategy_simulation_debug,
    select_top_stocks_by_momentum,
    _filter_grids,
    _parse_filter_groups,
    _short_rebalance,
)
from wave_engine import run_wave_simulation
from wave_models import WaveConfig, Signal

logger = logging.getLogger(__name__)
router = APIRouter()


# =============================================================================
# STRATEGY SIMULATION ENDPOINTS
# =============================================================================

class StrategyRule(BaseModel):
    id: str
    metric: str  # 'truevx_score', 'mean_short', 'mean_mid', 'mean_long'
    operator: str  # '>', '<', '>=', '<=', '==', '!='
    threshold: float
    name: str

class Strategy(BaseModel):
    id: Optional[str] = None
    name: str
    description: str
    rules: List[StrategyRule]
    wave: Optional[WaveConfig] = None
    # Rich stateless filter: signal GROUPS (AND within a group, OR across groups),
    # reusing the wave Signal vocabulary (compare / last_cross_dir). When present it
    # supersedes `rules`; absent → legacy rules path.
    filter: Optional[List[List[Signal]]] = None
    created_at: Optional[str] = None
    last_modified: Optional[str] = None

class SimulationParams(BaseModel):
    strategy_id: Optional[str] = None  # required for stateless path; optional when wave is provided
    portfolio_base_value: float = 100000
    rebalance_frequency: str = "monthly"  # 'monthly', 'weekly', 'quarterly', 'dynamic'
    rebalance_date: str = "first"  # 'first', 'last', 'mid'
    rebalance_type: str = "equal_weight"  # 'equal_weight', 'skewed' - allocation method
    universe: str = "NIFTY50"  # 'NIFTY50', 'NIFTY100', 'NIFTY500'
    benchmark_symbol: Optional[str] = None  # Optional benchmark symbol override
    max_holdings: Optional[int] = None  # Maximum number of stocks; None = all eligible
    momentum_ranking: str = "20_day_return"  # Momentum ranking method - supports:
    # Price-based: '20_day_return', 'price_roc_22d', 'price_roc_66d', 'price_roc_222d', 'risk_adjusted', 'technical'
    # Indicator-based: 'truevx_roc', 'short_mean_roc', 'mid_mean_roc', 'long_mean_roc', 'stock_score_roc'
    # Any method + '_low' suffix inverts the ranking (select LOWEST ROC — buy-the-dip),
    # e.g. 'price_roc_22d_low', 'price_roc_222d_low'. Stateless engine only (wave is v1-limited).
    start_date: str
    end_date: str
    # New brokerage-related parameters
    include_brokerage: bool = True
    slippage_bps: float = 0.0  # per-side execution slippage: buys @ close*(1+s), sells @ close*(1-s)  # Enable/disable brokerage calculation
    exchange: str = "NSE"  # Default exchange for charges ("NSE" or "BSE")
    custom_brokerage_rate: float = 0.0  # Custom brokerage rate override (as decimal, e.g., 0.001 for 0.1%)
    portfolio_turnover_estimate: float = 0.5  # Expected portfolio churn per rebalance (0.0-1.0)
    point_in_time_membership: bool = False  # Gate the universe to index membership as-of each rebalance date (survivorship-bias-free)
    side: str = "long"  # 'long' (default) | 'short'. Short runs the SAME strategy/selection but sells-to-open: profit when price falls. Inverts position sign across both engines.
    annual_borrow_rate: float = 0.0  # Stock-loan financing cost (decimal/yr, e.g. 0.04 = 4%) accrued daily on short market value. Ignored when side='long'. No margin/dividend-owed modelling in v1.
    wave: Optional[WaveConfig] = None  # When set, route to wave engine instead of stateless path
    filter: Optional[List[List[Signal]]] = None  # Rich stateless filter (compare/last_cross_dir groups); supersedes the strategy's rules
    regime_gate: bool = False  # Flatten the book to cash while the gate index is risk-off; re-enter the day it recovers. Long-only.
    gate_symbol: Optional[str] = None  # Gate index override; default "Nifty 500"
    gate_sma: int = 200  # SMA length (trading days) — used when gate_mode == "sma"
    gate_mode: str = "sma"  # "sma" | "dynamic_fib" (close vs fib level of trailing HH/LL range)
    gate_fib_lookback: int = 222  # Dynamic-Fib trailing range (trading days)
    gate_fib_level: float = 0.66  # risk-on when close >= LL + (HH-LL)*level
    stop_loss_percent: float = 0.0  # Hard per-stock stop (percent, e.g. 10 = 10%). On any non-rebalance day a holding closing at/below buy_price*(1-pct/100) is sold at that close; proceeds wait in cash for the next rebalance. 0 disables. Long-only.
    stock_gate: bool = False  # per-stock eligibility: each name must be above ITS OWN fib level
    portfolio_gate: bool = False  # self-gate on the portfolio equity curve (same fib params)

    @model_validator(mode="after")
    def require_strategy_id_for_stateless(self):
        if self.wave is None and not self.strategy_id:
            raise ValueError("strategy_id is required when wave is not provided")
        return self


class HoldingsMultiParams(BaseModel):
    """Parameters for holdings multi-dimension simulation"""
    strategy_id: str
    portfolio_base_value: float = 100000
    rebalance_frequency: str = "monthly"
    rebalance_date: str = "first"
    rebalance_type: str = "equal_weight"
    universe: str = "NIFTY50"
    benchmark_symbol: Optional[str] = None
    base_max_holdings: int = 10  # Base holdings value
    momentum_ranking: str = "20_day_return"
    start_date: str
    end_date: str
    include_brokerage: bool = True
    exchange: str = "NSE"
    custom_brokerage_rate: float = 0.0
    portfolio_turnover_estimate: float = 0.5
    multipliers: list[int] = [1, 2, 3, 4]  # Holding size multipliers
    multipliers: list = [1, 2, 3, 4]  # Multipliers for base_max_holdings

@router.post("/api/simulation/strategies")
async def save_strategy(strategy: Strategy):
    """Save a new trading strategy"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        strategies_coll = mongo_conn.db.simulation_strategies
        
        # Generate ID and timestamps
        strategy_data = strategy.dict()
        strategy_data["id"] = f"strategy_{int(datetime.now().timestamp() * 1000)}"
        strategy_data["created_at"] = datetime.now().isoformat()
        strategy_data["last_modified"] = datetime.now().isoformat()
        
        # Insert into database
        result = strategies_coll.insert_one(strategy_data)
        strategy_data["_id"] = str(result.inserted_id)
        
        logger.info(f"💾 Saved strategy: {strategy.name} with {len(strategy.rules)} rules")
        
        return JSONResponse(content={
            "success": True,
            "id": strategy_data["id"],
            "strategy_id": strategy_data["id"],
            "message": "Strategy saved successfully"
        })
        
    except Exception as e:
        logger.error(f"❌ Error saving strategy: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save strategy: {str(e)}")

@router.get("/api/simulation/strategies")
async def get_strategies():
    """Get all saved trading strategies"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        strategies_coll = mongo_conn.db.simulation_strategies
        
        # Get all strategies
        strategies = []
        for strategy_doc in strategies_coll.find().sort("last_modified", -1):
            strategy_doc["_id"] = str(strategy_doc["_id"])
            strategies.append(strategy_doc)
        
        logger.info(f"📋 Retrieved {len(strategies)} strategies")
        
        return JSONResponse(content={
            "success": True,
            "strategies": strategies
        })
        
    except Exception as e:
        logger.error(f"❌ Error getting strategies: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get strategies: {str(e)}")

@router.put("/api/simulation/strategies/{strategy_id}")
async def update_strategy(strategy_id: str, strategy: Strategy):
    """Update an existing trading strategy"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        strategies_coll = mongo_conn.db.simulation_strategies
        
        # Check if strategy exists
        existing_strategy = strategies_coll.find_one({"id": strategy_id})
        if not existing_strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        
        # Update strategy data (preserve the ID)
        strategy_data = strategy.dict()
        strategy_data["last_modified"] = datetime.now().isoformat()
        
        # Remove id from update data to prevent overwriting
        if "id" in strategy_data:
            del strategy_data["id"]
        
        # Update in database
        result = strategies_coll.update_one(
            {"id": strategy_id}, 
            {"$set": strategy_data}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=400, detail="No changes made to strategy")
        
        logger.info(f"✏️ Updated strategy: {strategy.name} with {len(strategy.rules)} rules")
        
        return JSONResponse(content={
            "success": True,
            "strategy_id": strategy_id,
            "message": "Strategy updated successfully"
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error updating strategy: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update strategy: {str(e)}")

@router.delete("/api/simulation/strategies/{strategy_id}")
async def delete_strategy(strategy_id: str):
    """Delete a trading strategy"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        strategies_coll = mongo_conn.db.simulation_strategies
        
        # Check if strategy exists
        existing_strategy = strategies_coll.find_one({"id": strategy_id})
        if not existing_strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        
        # Delete from database
        result = strategies_coll.delete_one({"id": strategy_id})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=400, detail="Failed to delete strategy")
        
        logger.info(f"🗑️ Deleted strategy: {existing_strategy.get('name', 'Unknown')}")
        
        return JSONResponse(content={
            "success": True,
            "strategy_id": strategy_id,
            "message": "Strategy deleted successfully"
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error deleting strategy: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete strategy: {str(e)}")


def _known_strategy_ids() -> set:
    return {d["id"] for d in mongo_conn.db.simulation_strategies.find({}, {"id": 1})}



def _wave_response(result: dict, params: "SimulationParams") -> JSONResponse:
    """Map run_wave_simulation result dict to the HTTP response shape.

    Mirrors the top-level shape the stateless path returns so the existing
    results UI can render it: {"success": True, "simulation": {...}}.
    The inner simulation dict carries "summary", "results", and "wave_diagnostics".
    """
    sim = sanitize_for_json({
        "params": params.dict(),
        "summary": result["summary"],
        "results": result["results"],
        "wave_diagnostics": result["wave_diagnostics"],
        "diagnostics": result.get("diagnostics"),
    })
    return JSONResponse(content={"success": True, "simulation": sim})


@router.post("/api/simulation/run")
async def run_simulation(params: SimulationParams):
    """Run a trading strategy simulation"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        logger.info(f"🚀 Starting simulation for strategy {params.strategy_id}")
        logger.info(f"📅 Period: {params.start_date} to {params.end_date}")
        logger.info(f"🎯 Universe: {params.universe}")

        # Normalize universe name to match database format
        universe_mapping = {
            "NIFTY50": "NIFTY50",
            "NIFTY100": "NIFTY100",
            "NIFTY500": "NIFTY500",   # stored without a space in index_meta
            "NIFTY 500": "NIFTY500"   # tolerate the spaced variant from the UI
        }

        normalized_universe = universe_mapping.get(params.universe, params.universe)
        logger.info(f"🔄 Normalized universe: {params.universe} → {normalized_universe}")

        # Resolve the saved strategy up-front (if any) so a strategy that carries a
        # persisted wave runs as a wave even when invoked by id alone. An explicit
        # wave in the request still wins (ad-hoc run from the unsaved builder).
        strat_doc = None
        if params.strategy_id:
            strat_doc = mongo_conn.db.simulation_strategies.find_one({"id": params.strategy_id})

        effective_wave = params.wave
        if effective_wave is None and strat_doc and strat_doc.get("wave"):
            try:
                effective_wave = WaveConfig(**strat_doc["wave"])
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Saved wave config is invalid: {e}")

        # ── WAVE PATH ──────────────────────────────────────────────────────────
        if effective_wave is not None:
            logger.info("🌊 Wave block present — routing to wave engine")
            # Capital is a RUN-TIME concern: use the run's Portfolio Base Value as the
            # wave capital (one capital source, consistent with the stateless path so
            # the results chart shares a baseline). The wave config's own capital_N is
            # superseded here; tranche size = capital / tranches.
            if params.portfolio_base_value and params.portfolio_base_value > 0:
                effective_wave = effective_wave.model_copy(
                    update={"capital_N": float(params.portfolio_base_value)})
            from membership import load_membership, union_over
            from datetime import datetime as _dt
            from stock_data_manager import StockDataManager

            anchor = _dt.now().date()
            try:
                intervals = load_membership(mongo_conn.db, normalized_universe, anchor)
            except ValueError as ve:
                raise HTTPException(
                    status_code=400,
                    detail=(f"Point-in-time membership unavailable for {normalized_universe}: {ve} "
                            f"Load its reconstitution events first (see capture_index_rebalance).")
                )

            start_dt = _dt.strptime(params.start_date, "%Y-%m-%d")
            end_dt = _dt.strptime(params.end_date, "%Y-%m-%d")
            start_d = start_dt.date()
            end_d = end_dt.date()
            ever = sorted(union_over(intervals, start_d, end_d))
            logger.info(f"🌊 Wave: {len(ever)} ever-members in {normalized_universe}")

            # Load indicators: {symbol: {date_str: {mean_long, mean_short, mean_mid, truevx_score}}}
            indicators: dict = {s: {} for s in ever}
            indicators_coll = mongo_conn.db["indicators"]
            for doc in indicators_coll.find(
                {"indicator_type": "truevx", "symbol": {"$in": ever},
                 "date": {"$gte": start_dt, "$lte": end_dt}},
                {"symbol": 1, "date": 1, "data": 1},
            ):
                dd = doc.get("data") or {}
                indicators[doc["symbol"]][doc["date"].strftime("%Y-%m-%d")] = {
                    "mean_long": dd.get("mean_long") or 0.0,
                    "mean_short": dd.get("mean_short") or 0.0,
                    "mean_mid": dd.get("mean_mid") or 0.0,
                    "truevx_score": dd.get("truevx_score") or 0.0,
                }

            # Load prices: {symbol: {date_str: close_float}}
            prices: dict = {s: {} for s in ever}
            _price_sem = asyncio.Semaphore(20)

            async with StockDataManager() as stock_manager:
                async def _fetch_px(sym):
                    async with _price_sem:
                        return sym, await stock_manager.get_price_data(
                            symbol=sym, start_date=start_dt, end_date=end_dt,
                            limit=10000, sort_order=1)

                fetched = await asyncio.gather(*[_fetch_px(s) for s in ever])

            for sym, records in fetched:
                for rec in records:
                    prices[sym][rec.date.strftime("%Y-%m-%d")] = float(rec.close_price)

            # Benchmark symbol
            bench_sym = params.benchmark_symbol or {
                "NIFTY50": "Nifty 50", "NIFTY100": "Nifty 100", "NIFTY500": "Nifty 500"
            }.get(normalized_universe, "Nifty 50")

            bench: dict = {}
            async with StockDataManager() as stock_manager:
                bench_records = await stock_manager.get_price_data(
                    symbol=bench_sym, start_date=start_dt, end_date=end_dt,
                    limit=10000, sort_order=1)
            for rec in bench_records:
                bench[rec.date.strftime("%Y-%m-%d")] = float(rec.close_price)

            if not bench:
                # Fail loud: an empty benchmark would flow through summarize() as
                # benchmark_cagr=0 and report alpha equal to the strategy's own CAGR.
                raise HTTPException(
                    status_code=400,
                    detail=f"No benchmark price data for '{bench_sym}' in "
                           f"{params.start_date}..{params.end_date} — load it or pass benchmark_symbol.")

            # Trading calendar: dates with both indicator and price data for any ever-member
            all_dates: set = set()
            for s in ever:
                all_dates |= set(indicators[s].keys()) & set(prices[s].keys())
            dates = sorted(all_dates)
            logger.info(f"🌊 Wave: {len(dates)} trading dates loaded")

            # Resolve strategy rules for the pre-gate (optional — None means no pre-gate)
            strategy_rules = strat_doc.get("rules", []) if strat_doc else None

            result = run_wave_simulation(
                ever, intervals, prices, indicators, bench, dates, effective_wave,
                rules=strategy_rules,
                max_holdings=params.max_holdings,
                momentum_method=params.momentum_ranking,
                include_brokerage=params.include_brokerage,
                custom_brokerage_rate=params.custom_brokerage_rate,
                point_in_time=params.point_in_time_membership,
                side=params.side,
                annual_borrow_rate=params.annual_borrow_rate,
            )
            logger.info(f"✅ Wave simulation complete: {len(result['results'])} days")
            return _wave_response(result, params)

        # ── STATELESS PATH (unchanged) ─────────────────────────────────────────
        # Get strategy details
        strategies_coll = mongo_conn.db.simulation_strategies
        strategy = strategies_coll.find_one({"id": params.strategy_id})

        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")

        # Resolve the universe. Two modes:
        #  - point-in-time OFF (default): the current constituent snapshot (survivorship-biased).
        #  - point-in-time ON: every symbol that was EVER a member in the window (union), with a
        #    membership timeline so selection can be gated to as-of members at each rebalance.
        membership = None
        if params.point_in_time_membership:
            from membership import load_membership, union_over
            from datetime import datetime as _dt
            try:
                anchor = _dt.now().date()
                membership = load_membership(mongo_conn.db, normalized_universe, anchor)
            except ValueError as ve:
                # Never silently fall back to the biased universe — fail loud.
                raise HTTPException(
                    status_code=400,
                    detail=(f"Point-in-time membership unavailable for {normalized_universe}: {ve} "
                            f"Load its reconstitution events first (see capture_index_rebalance).")
                )
            start_d = _dt.strptime(params.start_date, "%Y-%m-%d").date()
            end_d = _dt.strptime(params.end_date, "%Y-%m-%d").date()
            universe_symbols = sorted(union_over(membership, start_d, end_d))
            logger.info(f"📊 Point-in-time: {len(universe_symbols)} ever-members in {normalized_universe} [{params.start_date}..{params.end_date}]")
        else:
            index_meta_coll = mongo_conn.db.index_meta
            universe_symbols = []
            for doc in index_meta_coll.find({"index_name": normalized_universe}, {"Symbol": 1}):
                universe_symbols.append(doc["Symbol"])
            logger.info(f"📊 Found {len(universe_symbols)} symbols in {normalized_universe} (current snapshot)")

        # Run simulation
        from indicator_data_manager import IndicatorDataManager

        async with IndicatorDataManager() as data_manager:
            simulation_results = await run_strategy_simulation(
                data_manager,
                strategy,
                universe_symbols,
                params,
                membership=membership
            )

        logger.info(f"✅ Simulation completed with {len(simulation_results['results'])} data points")

        return JSONResponse(content={
            "success": True,
            "simulation": simulation_results
        })

    except HTTPException:
        raise  # preserve intended status codes (400 PIT-unavailable, 404 strategy-not-found)
    except Exception as e:
        logger.error(f"❌ Error running simulation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to run simulation: {str(e)}")


@router.post("/api/simulation/multi-run")
async def run_multi_dimension_simulation(params: SimulationParams):
    """Run parallel simulations across multiple time periods ending on the same date"""
    try:
        import asyncio
        from datetime import datetime
        from dateutil.relativedelta import relativedelta
        
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        logger.info(f"🎯 Starting multi-dimension simulation for strategy {params.strategy_id}")
        logger.info(f"📅 Base period: {params.start_date} to {params.end_date}")
        
        # Parse end date
        end_date = datetime.strptime(params.end_date, "%Y-%m-%d")
        base_start_date = datetime.strptime(params.start_date, "%Y-%m-%d")
        
        # Generate time periods - all ending on same end_date
        # Calculate years between start and end
        years_diff = end_date.year - base_start_date.year
        
        periods = []
        # Generate periods: original, -1 year, -2 years, -3 years, -4 years (if applicable)
        for year_offset in range(min(years_diff + 1, 5)):
            period_start = base_start_date + relativedelta(years=year_offset)
            
            # Skip if start date would be after end date
            if period_start >= end_date:
                continue
                
            periods.append({
                "label": f"{period_start.year}-{end_date.year}",
                "start_date": period_start.strftime("%Y-%m-%d"),
                "end_date": params.end_date
            })
        
        logger.info(f"🔢 Generated {len(periods)} time periods for parallel simulation")
        for p in periods:
            logger.info(f"  📆 {p['label']}: {p['start_date']} to {p['end_date']}")
        
        # Normalize universe
        universe_mapping = {
            "NIFTY50": "NIFTY50",
            "NIFTY100": "NIFTY100", 
            "NIFTY500": "NIFTY500",
            "NIFTY 500": "NIFTY500"
        }
        normalized_universe = universe_mapping.get(params.universe, params.universe)
        
        # Get strategy and universe symbols
        strategies_coll = mongo_conn.db.simulation_strategies
        strategy = strategies_coll.find_one({"id": params.strategy_id})
        
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        
        index_meta_coll = mongo_conn.db.index_meta
        universe_symbols = []
        for doc in index_meta_coll.find({"index_name": normalized_universe}, {"Symbol": 1}):
            universe_symbols.append(doc["Symbol"])
        
        logger.info(f"📊 Using {len(universe_symbols)} symbols from {normalized_universe}")
        
        # Run simulations in parallel
        from indicator_data_manager import IndicatorDataManager
        
        async def run_single_period(period_config):
            """Run simulation for a single time period"""
            try:
                logger.info(f"⚡ Starting simulation for {period_config['label']}")
                
                # Create params for this period
                period_params = params.copy()
                period_params.start_date = period_config["start_date"]
                period_params.end_date = period_config["end_date"]
                
                async with IndicatorDataManager() as data_manager:
                    results = await run_strategy_simulation(
                        data_manager,
                        strategy,
                        universe_symbols,
                        period_params
                    )
                
                # Extract key metrics from summary (which has all calculated metrics)
                summary = results.get("summary", {})
                
                total_return = summary.get("total_return", 0)
                benchmark_return = summary.get("benchmark_return", 0)
                alpha = summary.get("alpha", 0)
                max_drawdown_summary = summary.get("max_drawdown", 0)
                sharpe_ratio_summary = summary.get("sharpe_ratio", 0)
                total_trades_summary = summary.get("total_trades", 0)
                
                # Get final portfolio value
                last_day = results["results"][-1] if results["results"] else None
                final_portfolio_value = last_day["portfolio_value"] if last_day else params.portfolio_base_value
                
                # Get final portfolio value
                last_day = results["results"][-1] if results["results"] else None
                final_portfolio_value = last_day["portfolio_value"] if last_day else params.portfolio_base_value
                
                logger.info(f"✅ Completed {period_config['label']}: {total_return:.2f}% return, Alpha: {alpha:.2f}%")
                
                return {
                    "period_label": period_config["label"],
                    "start_date": period_config["start_date"],
                    "end_date": period_config["end_date"],
                    "total_return": round(total_return, 2),
                    "benchmark_return": round(benchmark_return, 2),
                    "alpha": round(alpha, 2),
                    "max_drawdown": round(max_drawdown_summary, 2),
                    "sharpe_ratio": round(sharpe_ratio_summary, 2),
                    "total_trades": total_trades_summary,
                    "final_portfolio_value": final_portfolio_value,
                    "days_count": len(results["results"]),
                    "status": "completed"
                }
                
            except Exception as e:
                logger.error(f"❌ Error in simulation for {period_config['label']}: {e}")
                return {
                    "period_label": period_config["label"],
                    "start_date": period_config["start_date"],
                    "end_date": period_config["end_date"],
                    "total_return": 0,
                    "benchmark_return": 0,
                    "alpha": 0,
                    "max_drawdown": 0,
                    "sharpe_ratio": 0,
                    "total_trades": 0,
                    "final_portfolio_value": params.portfolio_base_value,
                    "days_count": 0,
                    "status": "error",
                    "error_message": str(e)
                }
        
        # Execute all simulations in parallel
        logger.info(f"🚀 Launching {len(periods)} parallel simulations...")
        period_results = await asyncio.gather(*[run_single_period(p) for p in periods])
        
        # Calculate aggregate metrics
        completed_results = [r for r in period_results if r["status"] == "completed"]
        
        if completed_results:
            avg_return = sum(r["total_return"] for r in completed_results) / len(completed_results)
            avg_alpha = sum(r["alpha"] for r in completed_results) / len(completed_results)
            avg_sharpe = sum(r["sharpe_ratio"] for r in completed_results) / len(completed_results)
            
            best_period = max(completed_results, key=lambda x: x["total_return"])
            worst_period = min(completed_results, key=lambda x: x["total_return"])
            
            # Consistency score: how close are returns to average (lower std = higher consistency)
            returns = [r["total_return"] for r in completed_results]
            if len(returns) > 1:
                import statistics
                std_returns = statistics.stdev(returns)
                # Convert to 0-100 scale (100 = perfect consistency)
                consistency_score = max(0, 100 - (std_returns / max(abs(avg_return), 1) * 100))
            else:
                consistency_score = 100
        else:
            avg_return = avg_alpha = avg_sharpe = consistency_score = 0
            best_period = worst_period = {"period_label": "N/A"}
        
        multi_simulation_result = {
            "params": params.dict(),
            "periods": period_results,
            "aggregate_metrics": {
                "avg_return": round(avg_return, 2),
                "avg_alpha": round(avg_alpha, 2),
                "avg_sharpe": round(avg_sharpe, 2),
                "best_period": best_period["period_label"],
                "worst_period": worst_period["period_label"],
                "consistency_score": round(consistency_score, 1)
            }
        }
        
        logger.info(f"🎉 Multi-dimension simulation completed!")
        logger.info(f"📊 Avg Return: {avg_return:.2f}%, Avg Alpha: {avg_alpha:.2f}%, Consistency: {consistency_score:.1f}%")
        
        return JSONResponse(content={
            "success": True,
            "multi_simulation": multi_simulation_result
        })
        
    except Exception as e:
        logger.error(f"❌ Error in multi-dimension simulation: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to run multi-dimension simulation: {str(e)}")


@router.post("/api/simulation/holdings-multi-run")
async def run_holdings_multi_dimension_simulation(params: HoldingsMultiParams):
    """Run parallel simulations with different max holdings (base * multipliers)"""
    try:
        import asyncio
        from datetime import datetime
        
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        logger.info(f"🎯 Starting holdings multi-dimension simulation for strategy {params.strategy_id}")
        logger.info(f"📊 Base max holdings: {params.base_max_holdings}, Multipliers: {params.multipliers}")
        
        # Normalize universe
        universe_mapping = {
            "NIFTY50": "NIFTY50",
            "NIFTY100": "NIFTY100", 
            "NIFTY500": "NIFTY500",
            "NIFTY 500": "NIFTY500"
        }
        normalized_universe = universe_mapping.get(params.universe, params.universe)
        
        # Get strategy and universe symbols
        strategies_coll = mongo_conn.db.simulation_strategies
        strategy = strategies_coll.find_one({"id": params.strategy_id})
        
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        
        index_meta_coll = mongo_conn.db.index_meta
        universe_symbols = []
        for doc in index_meta_coll.find({"index_name": normalized_universe}, {"Symbol": 1}):
            universe_symbols.append(doc["Symbol"])
        
        logger.info(f"📊 Using {len(universe_symbols)} symbols from {normalized_universe}")
        
        # Generate holding configurations
        base_holdings = params.base_max_holdings
        holding_configs = []
        for multiplier in params.multipliers:
            holding_size = base_holdings * multiplier
            holding_configs.append({
                "multiplier": multiplier,
                "holding_size": holding_size
            })
        
        logger.info(f"🔢 Generated {len(holding_configs)} holding configurations: {[h['holding_size'] for h in holding_configs]}")
        
        # Run simulations in parallel
        from indicator_data_manager import IndicatorDataManager
        
        async def run_single_holding_simulation(holding_config):
            """Run simulation for a single holding size"""
            try:
                holding_size = holding_config["holding_size"]
                logger.info(f"⚡ Starting simulation for {holding_size} holdings")
                
                # Create SimulationParams for this holding size
                holding_params = SimulationParams(
                    strategy_id=params.strategy_id,
                    portfolio_base_value=params.portfolio_base_value,
                    rebalance_frequency=params.rebalance_frequency,
                    rebalance_date=params.rebalance_date,
                    rebalance_type=params.rebalance_type,
                    universe=params.universe,
                    benchmark_symbol=params.benchmark_symbol,
                    max_holdings=holding_size,
                    momentum_ranking=params.momentum_ranking,
                    start_date=params.start_date,
                    end_date=params.end_date,
                    include_brokerage=params.include_brokerage,
                    exchange=params.exchange,
                    custom_brokerage_rate=params.custom_brokerage_rate,
                    portfolio_turnover_estimate=params.portfolio_turnover_estimate
                )
                
                async with IndicatorDataManager() as data_manager:
                    results = await run_strategy_simulation(
                        data_manager,
                        strategy,
                        universe_symbols,
                        holding_params
                    )
                
                # Extract metrics from summary
                summary = results.get("summary", {})
                
                total_return = summary.get("total_return", 0)
                benchmark_return = summary.get("benchmark_return", 0)
                alpha = summary.get("alpha", 0)
                max_drawdown_summary = summary.get("max_drawdown", 0)
                sharpe_ratio_summary = summary.get("sharpe_ratio", 0)
                total_trades_summary = summary.get("total_trades", 0)
                
                # Calculate volatility from daily returns
                portfolio_values = [day["portfolio_value"] for day in results["results"]]
                daily_returns = []
                for i in range(1, len(portfolio_values)):
                    daily_ret = (portfolio_values[i] - portfolio_values[i-1]) / portfolio_values[i-1] * 100
                    daily_returns.append(daily_ret)
                
                import statistics
                volatility = statistics.stdev(daily_returns) * (252 ** 0.5) if len(daily_returns) > 1 else 0
                
                # Calculate monthly metrics
                monthly_data = {}
                for day in results["results"]:
                    date_str = day["date"]
                    month_key = date_str[:7]  # YYYY-MM
                    
                    if month_key not in monthly_data:
                        monthly_data[month_key] = {
                            "start_value": day["portfolio_value"],
                            "end_value": day["portfolio_value"],
                            "trades": 0
                        }
                    
                    monthly_data[month_key]["end_value"] = day["portfolio_value"]
                    monthly_data[month_key]["trades"] += len(day.get("new_added", [])) + len(day.get("exited", []))
                
                # Calculate monthly returns and churn
                monthly_returns = []
                monthly_churns = []
                for month_key, data in monthly_data.items():
                    monthly_return = (data["end_value"] - data["start_value"]) / data["start_value"] * 100
                    monthly_returns.append(monthly_return)
                    
                    # Churn = trades / (holdings * 2) to normalize
                    monthly_churn = (data["trades"] / (holding_size * 2)) * 100 if holding_size > 0 else 0
                    monthly_churns.append(monthly_churn)
                
                # Calculate monthly win rate
                positive_months = sum(1 for r in monthly_returns if r > 0)
                monthly_win_rate = (positive_months / len(monthly_returns)) * 100 if monthly_returns else 0
                
                avg_monthly_churn = statistics.mean(monthly_churns) if monthly_churns else 0
                
                # Prepare portfolio values for charting
                portfolio_values_data = [
                    {
                        "date": day["date"],
                        "value": day["portfolio_value"],
                        "benchmark": day["benchmark_value"]
                    }
                    for day in results["results"]
                ]
                
                # Get final portfolio value
                last_day = results["results"][-1] if results["results"] else None
                final_portfolio_value = last_day["portfolio_value"] if last_day else params.portfolio_base_value
                
                logger.info(f"✅ Completed {holding_size} holdings: {total_return:.2f}% return, Sharpe: {sharpe_ratio_summary:.2f}")
                
                return {
                    "holding_size": holding_size,
                    "total_return": round(total_return, 2),
                    "benchmark_return": round(benchmark_return, 2),
                    "alpha": round(alpha, 2),
                    "max_drawdown": round(max_drawdown_summary, 2),
                    "sharpe_ratio": round(sharpe_ratio_summary, 2),
                    "volatility": round(volatility, 2),
                    "total_trades": total_trades_summary,
                    "avg_monthly_churn": round(avg_monthly_churn, 2),
                    "monthly_win_rate": round(monthly_win_rate, 2),
                    "final_portfolio_value": final_portfolio_value,
                    "days_count": len(results["results"]),
                    "monthly_returns": [round(r, 2) for r in monthly_returns],
                    "portfolio_values": portfolio_values_data,
                    "status": "completed"
                }
                
            except Exception as e:
                logger.error(f"❌ Error in simulation for {holding_config['holding_size']} holdings: {e}")
                return {
                    "holding_size": holding_config["holding_size"],
                    "total_return": 0,
                    "benchmark_return": 0,
                    "alpha": 0,
                    "max_drawdown": 0,
                    "sharpe_ratio": 0,
                    "volatility": 0,
                    "total_trades": 0,
                    "avg_monthly_churn": 0,
                    "monthly_win_rate": 0,
                    "final_portfolio_value": params.portfolio_base_value,
                    "days_count": 0,
                    "monthly_returns": [],
                    "portfolio_values": [],
                    "status": "error",
                    "error_message": str(e)
                }
        
        # Execute all simulations sequentially to avoid resource conflicts
        logger.info(f"🚀 Starting {len(holding_configs)} sequential simulations...")
        holding_results = []
        for holding_config in holding_configs:
            result = await run_single_holding_simulation(holding_config)
            holding_results.append(result)
        
        # Calculate aggregate metrics
        completed_results = [r for r in holding_results if r["status"] == "completed"]
        
        if completed_results:
            avg_return = sum(r["total_return"] for r in completed_results) / len(completed_results)
            avg_alpha = sum(r["alpha"] for r in completed_results) / len(completed_results)
            avg_sharpe = sum(r["sharpe_ratio"] for r in completed_results) / len(completed_results)
            
            best_return_result = max(completed_results, key=lambda x: x["total_return"])
            worst_return_result = min(completed_results, key=lambda x: x["total_return"])
            
            # Find optimal risk-adjusted (best Sharpe ratio)
            best_sharpe_result = max(completed_results, key=lambda x: x["sharpe_ratio"])
        else:
            avg_return = avg_alpha = avg_sharpe = 0
            best_return_result = worst_return_result = best_sharpe_result = {"holding_size": 0}
        
        multi_holdings_result = {
            "params": params.dict(),
            "holdings_results": holding_results,
            "aggregate_metrics": {
                "average_return": round(avg_return, 2),
                "average_alpha": round(avg_alpha, 2),
                "average_sharpe": round(avg_sharpe, 2),
                "best_holding_size": best_return_result["holding_size"],
                "worst_holding_size": worst_return_result["holding_size"],
                "optimal_risk_adjusted": best_sharpe_result["holding_size"]
            }
        }
        
        logger.info(f"🎉 Holdings multi-dimension simulation completed!")
        logger.info(f"📊 Best: {best_return_result['holding_size']} holdings, Optimal: {best_sharpe_result['holding_size']} holdings")
        
        return JSONResponse(content={
            "success": True,
            "multi_holdings_simulation": multi_holdings_result
        })
        
    except Exception as e:
        logger.error(f"❌ Error in holdings multi-dimension simulation: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to run holdings multi-dimension simulation: {str(e)}")


@router.post("/api/simulation/debug")
async def debug_simulation(params: SimulationParams):
    """Run simulation with detailed debugging for first few days"""
    try:
        if mongo_conn.db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")
        
        logger.info(f"🔍 DEBUG MODE: Starting simulation for strategy {params.strategy_id}")
        
        # Normalize universe name to match database format
        universe_mapping = {
            "NIFTY50": "NIFTY50",
            "NIFTY100": "NIFTY100",
            "NIFTY500": "NIFTY500",   # stored without a space in index_meta
            "NIFTY 500": "NIFTY500"   # tolerate the spaced variant from the UI
        }
        
        normalized_universe = universe_mapping.get(params.universe, params.universe)
        logger.info(f"🔄 DEBUG: Normalized universe: {params.universe} → {normalized_universe}")
        
        # Get strategy details
        strategies_coll = mongo_conn.db.simulation_strategies
        strategy = strategies_coll.find_one({"id": params.strategy_id})
        
        if not strategy:
            raise HTTPException(status_code=404, detail="Strategy not found")
        
        # Get universe symbols (limit to first 20 for debugging)
        index_meta_coll = mongo_conn.db.index_meta
        universe_symbols = []
        for doc in index_meta_coll.find({"index_name": normalized_universe}, {"Symbol": 1}).limit(20):
            universe_symbols.append(doc["Symbol"])
        
        logger.info(f"🧪 DEBUG: Using {len(universe_symbols)} symbols: {universe_symbols}")
        
        # Use the full date range provided by user (no artificial limits)
        debug_params = params.copy()
        # Keep original start_date and end_date from user
        
        from indicator_data_manager import IndicatorDataManager
        
        async with IndicatorDataManager() as data_manager:
            simulation_results = await run_strategy_simulation_debug(
                data_manager, 
                strategy, 
                universe_symbols, 
                debug_params
            )
        
        total_days = len(simulation_results["debug_results"]) if simulation_results["debug_results"] else 0
        rebalance_days = len([r for r in simulation_results["debug_results"] if r["should_rebalance"]]) if simulation_results["debug_results"] else 0
        
        return JSONResponse(content={
            "success": True,
            "debug_simulation": simulation_results,
            "message": f"Debug simulation completed for {total_days} days with {rebalance_days} rebalance events"
        })
        
    except Exception as e:
        logger.error(f"❌ Error in debug simulation: {e}")
        raise HTTPException(status_code=500, detail=f"Debug simulation failed: {str(e)}")

@router.post("/api/simulation/estimate-charges")
async def estimate_simulation_charges(params: SimulationParams):
    """Estimate total charges for a simulation run"""
    try:
        logger.info(f"🧮 Estimating charges for strategy {params.strategy_id}")
        
        # Calculate simulation duration
        start_date = datetime.strptime(params.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(params.end_date, "%Y-%m-%d")
        simulation_days = (end_date - start_date).days
        
        # Initialize brokerage calculator
        calculator = BrokerageCalculator(
            default_exchange=params.exchange,
            custom_brokerage_rate=params.custom_brokerage_rate
        )
        
        # Estimate annual charge impact
        charge_estimate = calculator.estimate_annual_charge_impact(
            portfolio_value=params.portfolio_base_value,
            rebalance_frequency=params.rebalance_frequency,
            average_portfolio_churn=params.portfolio_turnover_estimate
        )
        
        # Adjust for actual simulation period
        simulation_years = simulation_days / 365.0
        estimated_total_charges = charge_estimate["charge_breakdown"]["total"] * simulation_years
        
        # Get charge rate information
        charge_info = calculator.get_charge_rates_info()
        
        response = {
            "success": True,
            "simulation_params": {
                "portfolio_value": params.portfolio_base_value,
                "rebalance_frequency": params.rebalance_frequency,
                "simulation_days": simulation_days,
                "simulation_years": round(simulation_years, 2),
                "exchange": params.exchange,
                "custom_brokerage_rate": params.custom_brokerage_rate,
                "portfolio_turnover_estimate": params.portfolio_turnover_estimate
            },
            "charge_estimate": {
                "total_estimated_charges": round(estimated_total_charges, 2),
                "estimated_charge_percentage": round((estimated_total_charges / params.portfolio_base_value) * 100, 3),
                "annual_charge_percentage": round(charge_estimate["impact_metrics"]["annual_charge_percentage"], 3),
                "monthly_charge_percentage": round(charge_estimate["impact_metrics"]["monthly_charge_percentage"], 4),
                "charge_per_rebalance": round(charge_estimate["impact_metrics"]["charge_per_rebalance"], 2),
                "breakdown": {
                    "stt": round(charge_estimate["charge_breakdown"]["stt"] * simulation_years, 2),
                    "transaction_charges": round(charge_estimate["charge_breakdown"]["transaction_charges"] * simulation_years, 2),
                    "sebi_charges": round(charge_estimate["charge_breakdown"]["sebi_charges"] * simulation_years, 2),
                    "stamp_duty": round(charge_estimate["charge_breakdown"]["stamp_duty"] * simulation_years, 2),
                    "brokerage": round(charge_estimate["charge_breakdown"]["brokerage"] * simulation_years, 2),
                    "gst": round(charge_estimate["charge_breakdown"]["gst"] * simulation_years, 2)
                }
            },
            "comparison": {
                "without_charges_final_value": params.portfolio_base_value,  # This would need actual simulation
                "with_charges_final_value": params.portfolio_base_value - estimated_total_charges,
                "impact_on_returns": round((estimated_total_charges / params.portfolio_base_value) * 100, 3)
            },
            "charge_rates": charge_info["charge_rates"],
            "rate_descriptions": charge_info["rate_descriptions"]
        }
        
        logger.info(f"📊 Charge estimation completed: {response['charge_estimate']['estimated_charge_percentage']:.3f}% of portfolio")
        
        return JSONResponse(content=response)
        
    except Exception as e:
        logger.error(f"❌ Error estimating charges: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to estimate charges: {str(e)}")

@router.get("/api/simulation/charge-rates")
async def get_charge_rates():
    """Get current Indian equity delivery charge rates"""
    try:
        calculator = BrokerageCalculator()
        charge_info = calculator.get_charge_rates_info()
        
        return JSONResponse(content={
            "success": True,
            "charge_rates": charge_info["charge_rates"],
            "rate_descriptions": charge_info["rate_descriptions"],
            "configuration": charge_info["configuration"],
            "examples": {
                "buy_1_lakh_nse": calculate_single_trade_charges(100000, "BUY", "NSE"),
                "sell_1_lakh_nse": calculate_single_trade_charges(100000, "SELL", "NSE"),
                "buy_1_lakh_bse": calculate_single_trade_charges(100000, "BUY", "BSE"),
                "sell_1_lakh_bse": calculate_single_trade_charges(100000, "SELL", "BSE")
            }
        })
        
    except Exception as e:
        logger.error(f"❌ Error getting charge rates: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get charge rates: {str(e)}")

@router.post("/api/simulation/download-tradebook")
async def download_tradebook(request: dict):
    """Generate and download PDF tradebook for simulation results"""
    try:
        from tradebook_pdf_generator import generate_tradebook_pdf
        
        logger.info("📄 Generating PDF tradebook download")
        
        # Extract simulation results and strategy name from request
        simulation_results = request.get("simulation_results", {})
        strategy_name = request.get("strategy_name", "strategy_report")
        
        # Debug: Log the data structure being received
        logger.info(f"🔍 Debug - Summary data: {simulation_results.get('summary', 'No summary')}")
        logger.info(f"🔍 Debug - Final portfolio value: {simulation_results.get('final_portfolio_value', 'No final value')}")
        logger.info(f"🔍 Debug - Benchmark data: {simulation_results.get('final_benchmark_value', 'No benchmark')}")
        
        if not simulation_results:
            raise HTTPException(status_code=400, detail="Simulation results required")
        
        # Generate PDF
        pdf_bytes = generate_tradebook_pdf(simulation_results, strategy_name)
        
        # Create filename
        safe_strategy_name = "".join(c for c in strategy_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        filename = f"{safe_strategy_name}_tradebook.pdf"
        
        # Return PDF as streaming response
        pdf_stream = io.BytesIO(pdf_bytes)
        
        headers = {
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': 'application/pdf'
        }
        
        logger.info(f"✅ PDF tradebook generated successfully: {filename} ({len(pdf_bytes)} bytes)")
        
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type='application/pdf',
            headers=headers
        )
        
    except Exception as e:
        logger.error(f"❌ Error generating PDF tradebook: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate tradebook: {str(e)}")


