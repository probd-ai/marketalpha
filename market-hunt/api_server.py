#!/usr/bin/env python3
"""
FastAPI Backend Server for Market Hunt Frontend
Provides RESTful API endpoints for the Next.js frontend
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta, date
from bson import ObjectId
import logging
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, model_validator
import uvicorn
import asyncio
import aiohttp
import pandas as pd  # Added for indicator optimizations
import numpy as np  # Added for enhanced analytics
import bisect  # Added for fast sorted-array lookups
import random  # Added for simulation
import json  # Added for enhanced JSON serialization
import io  # Added for PDF generation
from fastapi.responses import StreamingResponse  # Added for PDF downloads
from brokerage_calculator import (
    BrokerageCalculator,
    TransactionCharges,
    TradeDetails,
    calculate_single_trade_charges,
    estimate_portfolio_charges
)  # Added for brokerage calculation
from portfolio_models import Portfolio
from brokers import config as broker_config
broker_config.load_env()
import settings
from portfolio_composition import validate_portfolio, compose_portfolio
from wave_models import WaveConfig, Signal
from wave_engine import run_wave_simulation, SHORT_MAINTENANCE_MARGIN
from wave_signals import CrossState, any_group_fires

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from app.serialization import sanitize_for_json, serialize_doc

# App lifespan: connect the shared Mongo layer before serving, close on exit.
from contextlib import asynccontextmanager

from app.db import database
from app.middleware import install_middleware


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.connect(settings.MONGO_URI, settings.DB_NAME)
    try:
        yield
    finally:
        database.close()


# FastAPI app
app = FastAPI(
    title="Market Hunt API",
    description="RESTful API for market research and analysis data",
    version="1.0.0",
    lifespan=lifespan,
)
install_middleware(app)

# Domain routers (extracted from this monolith, Module 2)
from app.routers import data as data_routes
from app.routers import indicators as indicators_routes
from app.routers import industries as industries_routes
from app.routers import meta as meta_routes
from app.routers import stock as stock_routes
from app.routers import urls as urls_routes
app.include_router(meta_routes.router)
app.include_router(urls_routes.router)
app.include_router(data_routes.router)
app.include_router(industries_routes.router)
app.include_router(stock_routes.router)
app.include_router(indicators_routes.router)
from app.routers import analytics as analytics_routes
app.include_router(analytics_routes.router)
from app.routers import simulation as simulation_routes
app.include_router(simulation_routes.router)
from app.routers import portfolio as portfolio_routes
app.include_router(portfolio_routes.router)
from app.routers import broker as broker_routes
app.include_router(broker_routes.router)
from app.routers import rsz as rsz_routes
app.include_router(rsz_routes.router)

# CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB connection (central config — see settings.py)
MONGO_URI = settings.MONGO_URI
DB_NAME = settings.DB_NAME

from app.db import mongo_conn

# Simulation models/engine now live in app.routers.simulation / engine.stateless;
# the portfolio block below still uses these:
from app.routers.simulation import SimulationParams, _known_strategy_ids
from engine.stateless import (apply_strategy_rules, calculate_stock_momentum,
                              calculate_stock_momentum_fast, qualify_by_filter,
                              run_strategy_simulation, _filter_grids,
                              _parse_filter_groups)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT, reload=False)
