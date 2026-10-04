"""Portfolio endpoints: CRUD + run/preview/last-run (moved verbatim from api_server)."""
import asyncio
import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.db import mongo_conn
from app.routers.simulation import SimulationParams, _known_strategy_ids
from app.serialization import sanitize_for_json
from engine.stateless import (apply_strategy_rules, calculate_stock_momentum,
                              calculate_stock_momentum_fast, qualify_by_filter,
                              run_strategy_simulation, _filter_grids,
                              _parse_filter_groups)
from indicator_data_manager import IndicatorDataManager
from portfolio_composition import validate_portfolio, compose_portfolio, build_rebalance_plan
from portfolio_models import Portfolio

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/portfolio")
async def create_portfolio(portfolio: Portfolio):
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    data = portfolio.model_dump()
    errors = validate_portfolio(data, _known_strategy_ids())
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    data["id"] = f"portfolio_{int(datetime.now().timestamp() * 1000)}"
    data["created_at"] = datetime.now().isoformat()
    data["last_modified"] = data["created_at"]
    mongo_conn.db.portfolios.insert_one(data)
    return JSONResponse(content={"success": True, "portfolio_id": data["id"]})


@router.get("/api/portfolio")
async def list_portfolios():
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    out = []
    for doc in mongo_conn.db.portfolios.find().sort("last_modified", -1):
        doc["_id"] = str(doc["_id"])
        out.append(doc)
    return JSONResponse(content={"success": True, "portfolios": out})


@router.get("/api/portfolio/{portfolio_id}")
async def get_portfolio(portfolio_id: str):
    doc = mongo_conn.db.portfolios.find_one({"id": portfolio_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    doc["_id"] = str(doc["_id"])
    return JSONResponse(content={"success": True, "portfolio": doc})


@router.put("/api/portfolio/{portfolio_id}")
async def update_portfolio(portfolio_id: str, portfolio: Portfolio):
    existing = mongo_conn.db.portfolios.find_one({"id": portfolio_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    data = portfolio.model_dump()
    errors = validate_portfolio(data, _known_strategy_ids())
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    data.pop("id", None)
    data["last_modified"] = datetime.now().isoformat()
    mongo_conn.db.portfolios.update_one({"id": portfolio_id}, {"$set": data})
    return JSONResponse(content={"success": True, "portfolio_id": portfolio_id})


@router.delete("/api/portfolio/{portfolio_id}")
async def delete_portfolio(portfolio_id: str):
    result = mongo_conn.db.portfolios.delete_one({"id": portfolio_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return JSONResponse(content={"success": True, "portfolio_id": portfolio_id})

# ---------------------------------------------------------------------------
# Async job store for long-running portfolio runs
# ---------------------------------------------------------------------------
# Run-job state (collection: portfolio_run_jobs). Mongo-backed so job status
# survives process restarts and is visible to every worker — a process-local
# dict loses jobs on restart and breaks under multiple uvicorn workers.
# ---------------------------------------------------------------------------

def _job_set(job_id: str, **fields) -> None:
    """Upsert job fields. created_at is set once on insert, never clobbered."""
    created_at = fields.pop("created_at", None)
    update: dict = {"$set": fields}
    update["$setOnInsert"] = {"created_at": created_at or datetime.now().isoformat()}
    mongo_conn.db.portfolio_run_jobs.update_one({"_id": job_id}, update, upsert=True)


def _job_get(job_id: str) -> dict | None:
    return mongo_conn.db.portfolio_run_jobs.find_one({"_id": job_id})


def _prune_run_jobs(max_jobs: int = 50) -> None:
    coll = mongo_conn.db.portfolio_run_jobs
    excess = coll.count_documents({}) - max_jobs
    if excess > 0:
        oldest = coll.find({}, {"_id": 1}).sort("created_at", 1).limit(excess)
        coll.delete_many({"_id": {"$in": [d["_id"] for d in oldest]}})


# ---------------------------------------------------------------------------
# Persistent run-result cache (collection: portfolio_runs)
# Key = (portfolio_id, config_hash, inception, end_key). end_key is the explicit
# end_date, or "latest:<data_version>" for open-ended runs so a data refresh
# naturally invalidates while same-data reruns hit the cache.
# ---------------------------------------------------------------------------

def _latest_data_date() -> str:
    """Most recent 'Nifty 50' price date — the dataset version stamp."""
    names = sorted((c for c in mongo_conn.db.list_collection_names()
                    if c.startswith("prices_")), reverse=True)
    for cname in names:
        doc = mongo_conn.db[cname].find_one({"symbol": "Nifty 50"}, sort=[("date", -1)])
        if doc:
            return doc["date"].strftime("%Y-%m-%d")
    return "unknown"


_SLEEVE_HASH_KEYS = ("strategy_id", "weight", "universe", "rebalance_frequency",
                     "rebalance_date", "rebalance_type", "max_holdings",
                     "momentum_ranking", "include_brokerage", "exchange",
                     "slippage_bps", "regime_gate", "gate_symbol", "gate_sma",
                     "gate_mode", "gate_fib_lookback", "gate_fib_level",
                     "stock_gate", "portfolio_gate")


def _portfolio_config_hash(p: dict) -> str:
    """Hash of everything that changes a run's output except the date window and
    dataset: sleeve execution params + referenced strategy definitions + capital
    + PIT flag. Editing a strategy's filter invalidates too."""
    import hashlib
    import json as _json
    cfg = {
        "engine_ver": 6,  # bump when engine changes alter results (invalidates cache); 6 = warm-up window
        "total_capital": p.get("total_capital"),
        "pit": p.get("point_in_time_membership", True),
        "benchmark": p.get("benchmark_symbol"),
        "sleeves": [],
    }
    for s in p.get("sleeves", []):
        entry = {k: s.get(k) for k in _SLEEVE_HASH_KEYS}
        entry["strategy_def"] = mongo_conn.db.simulation_strategies.find_one(
            {"id": s.get("strategy_id")}, {"_id": 0, "filter": 1, "rules": 1, "wave": 1, "book": 1})
        if (entry["strategy_def"] or {}).get("book"):   # only book sleeves read it; keeps other hashes unchanged
            entry["book_weekday"] = int(s.get("book_weekday", 0) or 0)
        cfg["sleeves"].append(entry)
    return hashlib.sha256(_json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()


def _cache_end_key(end_date: str | None, data_version: str) -> str:
    return end_date if end_date else f"latest:{data_version}"


def _store_portfolio_run(portfolio_id: str, config_hash: str, inception: str,
                         end_key: str, data_version: str, result: dict) -> None:
    mongo_conn.db.portfolio_runs.replace_one(
        {"portfolio_id": portfolio_id, "config_hash": config_hash,
         "inception": inception, "end_key": end_key},
        {"portfolio_id": portfolio_id, "config_hash": config_hash,
         "inception": inception, "end_key": end_key, "data_version": data_version,
         "created_at": datetime.now().isoformat(), "result": result},
        upsert=True)
    # Keep the newest 10 cached runs per portfolio.
    stale = list(mongo_conn.db.portfolio_runs
                 .find({"portfolio_id": portfolio_id}, {"_id": 1})
                 .sort("created_at", -1).skip(10))
    if stale:
        mongo_conn.db.portfolio_runs.delete_many({"_id": {"$in": [d["_id"] for d in stale]}})


_TIER_SPECS = [
    ("NIFTY50", "Nifty 50"),
    ("NIFTY100", "Nifty 100"),
    ("NIFTYMIDCAP150", "Midcap 150"),
    ("NIFTYSMALLCAP250", "Smallcap 250"),
    ("NIFTYMICROCAP250", "Microcap 250"),
    ("NIFTY500", "Nifty 500 (other)"),
]


def _symbol_meta_for(symbols: set[str]) -> dict:
    """{symbol: {index_tier, sector}} from current index_meta snapshots.
    Tier = most specific cap bucket (Nifty50 ⊂ 100; Mid/Small/Micro exclusive)."""
    syms = list(symbols)
    meta = {s: {} for s in syms}
    assigned: set[str] = set()
    for index_name, label in _TIER_SPECS:
        members = {d["Symbol"] for d in mongo_conn.db.index_meta.find(
            {"index_name": index_name, "Symbol": {"$in": syms}}, {"Symbol": 1})}
        for s in members - assigned:
            meta[s]["index_tier"] = label
            assigned.add(s)
    # Historical fallback: names no longer in any current snapshot (delisted /
    # dropped) get the most specific tier they EVER belonged to, from the
    # reconstitution timeline — else the table shows a misleading "Other".
    missing = [s for s in syms if "index_tier" not in meta[s]]
    if missing:
        from membership import load_membership
        for index_name, label in _TIER_SPECS:
            if not missing:
                break
            try:
                iv = load_membership(mongo_conn.db, index_name, datetime.now().date())
            except ValueError:
                continue
            ever = set()
            for interval in iv:
                ever.add(interval.symbol)
            hit = [s for s in missing if s in ever]
            for s in hit:
                meta[s]["index_tier"] = label
            missing = [s for s in missing if s not in hit]
    for d in mongo_conn.db.index_meta.find(
            {"Symbol": {"$in": syms}, "Industry": {"$nin": [None, ""]}},
            {"Symbol": 1, "Industry": 1}):
        meta[d["Symbol"]].setdefault("sector", d["Industry"])
    return meta


async def _run_book_sleeve(sleeve: dict, book: dict, inception: str, as_of: str, total_capital: float,
                           pit: bool, offset_sessions: int):
    """A sleeve whose strategy is a book runs on the fund desk engine (docs/specs/book-strategies-in-simulator.md).
    Gates, the lag knob and a survivorship-biased universe are refused for it, never silently dropped."""
    from engine.book_adapter import book_run_errors, run_book_simulation
    run_cfg = {"start_date": inception, "end_date": as_of, "point_in_time_membership": pit,
               "book_weekday": int(sleeve.get("book_weekday", 0) or 0),
               "rebalance_offset_sessions": int(offset_sessions or 0),
               **{k: bool(sleeve.get(k, False)) for k in ("regime_gate", "stock_gate", "portfolio_gate")}}
    errors, _ = book_run_errors(book, run_cfg)
    if errors:
        raise HTTPException(status_code=400, detail=f"sleeve '{sleeve['label']}': " + "; ".join(errors))
    try:
        run = await asyncio.to_thread(
            run_book_simulation, mongo_conn.db, book, sleeve["universe"], inception, as_of,
            round(total_capital * float(sleeve["weight"]) / 100), float(sleeve.get("slippage_bps", 0) or 0),
            bool(sleeve.get("include_brokerage", True)), run_cfg["book_weekday"], "Nifty 50")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=f"sleeve '{sleeve['label']}': {ve}")
    return ({"id": sleeve["id"], "label": sleeve["label"], "weight": sleeve["weight"]}, run)


async def _compute_portfolio_run(portfolio: dict, inception: str, as_of: str,
                                 offset_sessions: int = 0) -> dict:
    """Heavy computation extracted from run_portfolio — no DB re-query, pure compute."""
    from indicator_data_manager import IndicatorDataManager
    from stock_data_manager import StockDataManager

    total_capital = float(portfolio["total_capital"])
    pit = bool(portfolio.get("point_in_time_membership", True))

    async def _run_sleeve(sleeve):
        strategy = mongo_conn.db.simulation_strategies.find_one({"id": sleeve["strategy_id"]})
        if not strategy:
            raise HTTPException(status_code=400, detail=f"strategy {sleeve['strategy_id']} not found")

        if strategy.get("book"):
            return await _run_book_sleeve(sleeve, strategy["book"], inception, as_of, total_capital, pit,
                                          offset_sessions)

        universe = sleeve["universe"]
        params = SimulationParams(
            strategy_id=sleeve["strategy_id"],
            portfolio_base_value=round(total_capital * float(sleeve["weight"]) / 100),
            rebalance_frequency=sleeve.get("rebalance_frequency", "monthly"),
            rebalance_date=sleeve.get("rebalance_date", "first"),
            rebalance_type=sleeve.get("rebalance_type", "equal_weight"),
            universe=universe,
            benchmark_symbol="Nifty 50",
            max_holdings=int(sleeve.get("max_holdings", 10)),
            momentum_ranking=sleeve.get("momentum_ranking", "20_day_return"),
            start_date=inception,
            end_date=as_of,
            include_brokerage=bool(sleeve.get("include_brokerage", True)),
            slippage_bps=float(sleeve.get("slippage_bps", 0) or 0),
            exchange=sleeve.get("exchange", "NSE"),
            point_in_time_membership=pit,
            regime_gate=bool(sleeve.get("regime_gate", False)),
            gate_symbol=sleeve.get("gate_symbol") or "Nifty 500",
            gate_sma=int(sleeve.get("gate_sma", 200) or 200),
            gate_mode=sleeve.get("gate_mode", "sma") or "sma",
            gate_fib_lookback=int(sleeve.get("gate_fib_lookback", 222) or 222),
            gate_fib_level=float(sleeve.get("gate_fib_level", 0.66) or 0.66),
            stock_gate=bool(sleeve.get("stock_gate", False)),
            portfolio_gate=bool(sleeve.get("portfolio_gate", False)),
        )
        if offset_sessions:
            params.rebalance_offset_sessions = int(offset_sessions)

        # Resolve the universe symbols exactly as /api/simulation/run does.
        membership = None
        if pit:
            from membership import load_membership, union_over
            try:
                membership = load_membership(mongo_conn.db, universe, datetime.now().date())
            except ValueError as ve:
                raise HTTPException(status_code=400, detail=(
                    f"Point-in-time membership unavailable for {universe}: {ve} "
                    f"Load its reconstitution events first."))
            s_d = datetime.strptime(inception, "%Y-%m-%d").date()
            e_d = datetime.strptime(as_of, "%Y-%m-%d").date()
            universe_symbols = sorted(union_over(membership, s_d, e_d))
        else:
            universe_symbols = [d["Symbol"] for d in
                                mongo_conn.db.index_meta.find({"index_name": universe}, {"Symbol": 1})]

        try:
            async with IndicatorDataManager() as dm:
                run = await run_strategy_simulation(dm, strategy, universe_symbols, params,
                                                    membership=membership)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"sleeve '{sleeve['label']}' failed: {e}")

        return ({"id": sleeve["id"], "label": sleeve["label"], "weight": sleeve["weight"]}, run)

    gathered = await asyncio.gather(*[_run_sleeve(s) for s in portfolio["sleeves"]])
    sleeve_meta = [g[0] for g in gathered]
    sleeve_runs = [g[1] for g in gathered]

    # Nifty 50 closes over the window (single benchmark series).
    benchmark_closes = {}
    async with StockDataManager() as sm:
        bench = await sm.get_price_data(symbol="Nifty 50",
                                        start_date=datetime.strptime(inception, "%Y-%m-%d"),
                                        end_date=datetime.strptime(as_of, "%Y-%m-%d"),
                                        limit=10000, sort_order=1)
        for rec in bench:
            benchmark_closes[rec.date.strftime("%Y-%m-%d")] = float(rec.close_price)

    book_symbols: set[str] = set()
    for run in sleeve_runs:
        for day in run.get("results", []):
            book_symbols |= {h.get("symbol") for h in day.get("holdings", [])}
    book_symbols.discard(None)
    symbol_meta = _symbol_meta_for(book_symbols) if book_symbols else {}

    composed = compose_portfolio(
        sleeves=sleeve_meta, sleeve_runs=sleeve_runs, total_capital=total_capital,
        benchmark_closes=benchmark_closes,
        portfolio_meta={"id": portfolio["id"], "name": portfolio["name"], "point_in_time": pit},
        as_of=as_of, symbol_meta=symbol_meta)

    return sanitize_for_json({"success": True, **composed})


def _compute_portfolio_run_sync(portfolio: dict, inception: str, as_of: str,
                                offset_sessions: int = 0) -> dict:
    # Runs the async compute in a fresh event loop on a worker thread, so the
    # CPU-bound simulation never blocks the server's main event loop.
    return asyncio.run(_compute_portfolio_run(portfolio, inception, as_of,
                                              offset_sessions))


async def _run_portfolio_job(job_id: str, portfolio: dict, inception: str, as_of: str,
                             cache_key: dict | None = None,
                             offset_sessions: int = 0) -> None:
    try:
        result = await asyncio.to_thread(_compute_portfolio_run_sync, portfolio,
                                         inception, as_of, offset_sessions)
        result["cached"] = False
        result["computed_at"] = datetime.now().isoformat()
        if cache_key:
            result["data_version"] = cache_key["data_version"]
            try:
                _store_portfolio_run(result=result, **cache_key)
            except Exception as ce:
                logger.error(f"cache store failed (run still returned): {ce}")
        _job_set(job_id, status="completed", result=result)
    except HTTPException as he:
        _job_set(job_id, status="failed", error=str(he.detail))
    except Exception as e:
        _job_set(job_id, status="failed", error=str(e))


@router.get("/api/portfolio/run-status/{job_id}")
async def get_portfolio_run_status(job_id: str):
    job = _job_get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Run job not found")
    return JSONResponse(content={"success": True, "status": job["status"],
                                 "result": job.get("result"), "error": job.get("error")})


@router.post("/api/portfolio/{portfolio_id}/run")
async def run_portfolio(portfolio_id: str, end_date: str | None = None,
                        start_date: str | None = None, force: bool = False,
                        offset_sessions: int = 0):
    """`offset_sessions` shifts every rebalance N trading sessions later.

    RESEARCH KNOB. The backtest ranks on a date's close and fills at that same
    close, which cannot happen live — the refresh, the run and the order all come
    after the market shut. Offsets of 1-3 measure what trading a session or two
    later actually costs. 0 is the stored, unchanged behaviour.
    """
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    p = mongo_conn.db.portfolios.find_one({"id": portfolio_id})
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    as_of = end_date or datetime.now().strftime("%Y-%m-%d")
    inception = start_date or p["inception_date"]

    if start_date is not None:
        if start_date < "2022-01-01":
            raise HTTPException(status_code=400,
                                detail=f"start_date {start_date} is before the data floor 2022-01-01")
        if start_date > as_of:
            raise HTTPException(status_code=400,
                                detail=f"start_date {start_date} is after as_of {as_of}")

    data_version = _latest_data_date()
    config_hash = _portfolio_config_hash(p)
    end_key = _cache_end_key(end_date, data_version)
    cache_key = {"portfolio_id": portfolio_id, "config_hash": config_hash,
                 "inception": inception, "end_key": end_key, "data_version": data_version}

    job_id = f"run_{int(datetime.now().timestamp() * 1000)}_{portfolio_id[-6:]}"

    # A research run (offset_sessions != 0) is EPHEMERAL: never read from the cache
    # and never written to it. Persisting it would make it the newest run, and every
    # live read — reconciliation target, sync plan, last-run page — takes the newest.
    # A lag-3 sweep silently becoming the book we place orders against is the failure
    # this prevents, and preventing it by construction beats filtering it out in four
    # separate read paths.
    if not force and not offset_sessions:
        hit = mongo_conn.db.portfolio_runs.find_one(
            {"portfolio_id": portfolio_id, "config_hash": config_hash,
             "inception": inception, "end_key": end_key}, {"_id": 0})
        if hit:
            result = dict(hit["result"])
            result["cached"] = True
            result["computed_at"] = hit["created_at"]
            _job_set(job_id, status="completed", result=result, error=None)
            _prune_run_jobs()
            return JSONResponse(status_code=202, content={
                "success": True, "job_id": job_id, "status": "completed", "cached": True})

    _job_set(job_id, status="running", result=None, error=None)
    _prune_run_jobs()
    if offset_sessions:
        cache_key = None            # ephemeral: compute, return, store nothing
    asyncio.create_task(_run_portfolio_job(job_id, p, inception, as_of, cache_key,
                                           offset_sessions))
    return JSONResponse(status_code=202, content={"success": True, "job_id": job_id,
                                                  "status": "running", "cached": False})


@router.get("/api/portfolio/{portfolio_id}/last-run")
async def last_portfolio_run(portfolio_id: str):
    """Newest cached run for instant page load, with staleness flags."""
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = mongo_conn.db.portfolio_runs.find_one(
        {"portfolio_id": portfolio_id}, {"_id": 0}, sort=[("created_at", -1)])
    if not doc:
        return JSONResponse(content={"success": True, "result": None})
    p = mongo_conn.db.portfolios.find_one({"id": portfolio_id})
    config_changed = bool(p) and _portfolio_config_hash(p) != doc["config_hash"]
    data_stale = (doc["end_key"].startswith("latest:")
                  and doc.get("data_version") != _latest_data_date())
    result = dict(doc["result"])
    result["cached"] = True
    result["computed_at"] = doc["created_at"]
    return JSONResponse(content=sanitize_for_json({
        "success": True, "result": result, "created_at": doc["created_at"],
        "config_changed": config_changed, "data_stale": data_stale}))


# ---------------------------------------------------------------------------
# Next-rebalance preview: replay each sleeve's selection (filter gate ->
# momentum rank -> top-N) on the LATEST indicator date and diff against the
# sleeve's current book from the newest cached run. Business-day estimate for
# the next rebalance date (holidays unknowable ahead of time).
# ---------------------------------------------------------------------------

def _busdays_between(d0, d1):
    out, d = [], d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _next_rebalance_estimate(freq: str, which: str, today) -> str:
    import calendar as _cal

    def month_days(y, m):
        return _busdays_between(date(y, m, 1), date(y, m, _cal.monthrange(y, m)[1]))

    def pick(days):
        return {"first": days[0], "mid": days[len(days) // 2], "last": days[-1]}.get(which, days[0])

    if freq == "weekly":
        wk = _busdays_between(today - timedelta(days=today.weekday()),
                              today + timedelta(days=4 - today.weekday()))
        d = pick(wk)
        if d < today:
            d = pick([x + timedelta(days=7) for x in wk])
        return d.isoformat()
    if freq == "quarterly":
        q0 = 3 * ((today.month - 1) // 3) + 1
        days = []
        for m in (q0, q0 + 1, q0 + 2):
            days += month_days(today.year, m)
        d = pick(days)
        if d < today:
            nq0, ny = (q0 + 3, today.year) if q0 < 10 else (1, today.year + 1)
            days = []
            for m in (nq0, nq0 + 1, nq0 + 2):
                days += month_days(ny, m)
            d = pick(days)
        return d.isoformat()
    # monthly (and dynamic falls back to next business day)
    if freq == "dynamic":
        d = today + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d.isoformat()
    d = pick(month_days(today.year, today.month))
    if d < today:
        ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        d = pick(month_days(ny, nm))
    return d.isoformat()


def _preview_sleeve_sync(sleeve: dict, pit: bool, current_holdings: list[str]) -> dict:
    db = mongo_conn.db
    strategy = db.simulation_strategies.find_one({"id": sleeve["strategy_id"]})
    if not strategy:
        raise HTTPException(status_code=400, detail=f"strategy {sleeve['strategy_id']} not found")
    if strategy.get("book"):
        # The equal-weight "probable adds" preview would misstate a book (no trims, tranche caps). Book sleeves
        # are traded from the fund desk, which projects the real next-session orders.
        raise HTTPException(status_code=400, detail=(
            f"sleeve '{sleeve.get('label')}' runs a book strategy; the rebalance preview and plan are not "
            "available for book sleeves — use the fund desk ticket"))
    universe = sleeve["universe"]

    # Universe as of today.
    if pit:
        from membership import load_membership, members_as_of
        try:
            iv = load_membership(db, universe, datetime.now().date())
            members = members_as_of(iv, datetime.now().date())
        except ValueError:
            members = {d["Symbol"] for d in db.index_meta.find({"index_name": universe}, {"Symbol": 1})}
    else:
        members = {d["Symbol"] for d in db.index_meta.find({"index_name": universe}, {"Symbol": 1})}
    members = sorted(members)

    # Latest indicator date + ~150-calendar-day warmup (cross-state filters and
    # 22-day indicator ROC rankings both need history; compare filters ignore it).
    latest = db.indicators.find_one({"indicator_type": "truevx"}, {"date": 1}, sort=[("date", -1)])
    if not latest:
        raise HTTPException(status_code=400, detail="no indicator data")
    d_latest = latest["date"]
    warm_start = d_latest - timedelta(days=150)

    day_map: dict[str, dict] = {}
    ind_series: dict[str, tuple] = {}
    cur = db.indicators.find(
        {"indicator_type": "truevx", "symbol": {"$in": members},
         "date": {"$gte": warm_start, "$lte": d_latest}},
        {"symbol": 1, "date": 1, "data": 1, "_id": 0}).sort("date", 1)
    for doc in cur:
        ds = doc["date"].strftime("%Y-%m-%d")
        rec = {
            "symbol": doc["symbol"],
            "truevx_score": (doc.get("data") or {}).get("truevx_score") or 0,
            "mean_short": (doc.get("data") or {}).get("mean_short") or 0,
            "mean_mid": (doc.get("data") or {}).get("mean_mid") or 0,
            "mean_long": (doc.get("data") or {}).get("mean_long") or 0,
        }
        day_map.setdefault(ds, {})[doc["symbol"]] = rec
        dates, data = ind_series.setdefault(doc["symbol"], ([], []))
        dates.append(doc["date"])
        data.append(rec)

    days = sorted(day_map)
    if not days:
        raise HTTPException(status_code=400, detail=f"no indicators for {universe}")

    # Filter gate replayed over the warmup window (keeps CrossState honest).
    filter_src = strategy.get("filter")
    if filter_src:
        groups = _parse_filter_groups(filter_src)
        grids = _filter_grids(groups)
        cross_states: dict = {}
        qualified = []
        for ds in days:
            qualified = qualify_by_filter(day_map[ds], groups, cross_states, grids)
    else:
        qualified = apply_strategy_rules(day_map[days[-1]], strategy.get("rules") or [])
    q_syms = [s["symbol"] for s in qualified]

    # Momentum rank on the latest date; prices only for qualified + current names.
    method = sleeve.get("momentum_ranking", "20_day_return")
    need_prices = sorted(set(q_syms) | set(current_holdings))
    px_series: dict[str, tuple] = {}
    if need_prices:
        px_start = d_latest - timedelta(days=320)
        for cname in sorted(c for c in db.list_collection_names() if c.startswith("prices_")):
            cur = db[cname].find(
                {"symbol": {"$in": need_prices}, "date": {"$gte": px_start, "$lte": d_latest}},
                {"symbol": 1, "date": 1, "close_price": 1, "_id": 0})
            for doc in cur:
                dates, closes = px_series.setdefault(doc["symbol"], ([], []))
                dates.append(doc["date"])
                closes.append(float(doc["close_price"]))
        for sym, (dates, closes) in px_series.items():
            order = sorted(range(len(dates)), key=lambda i: dates[i])
            px_series[sym] = ([dates[i] for i in order], [closes[i] for i in order])

    def score(sym):
        return round(calculate_stock_momentum_fast(
            px_series.get(sym), ind_series.get(sym), d_latest, method), 2)

    # Same pool the engine ranks: current holdings compete with the qualified.
    pool = sorted(set(q_syms) | set(current_holdings))
    scored = sorted(((s, score(s)) for s in pool), key=lambda x: -x[1])
    cap = sleeve.get("max_holdings")
    if method == "none":
        # 'none' = hold all eligible. No scorer implements it, so every name scores
        # 0.0 and a cap would silently hand back an ALPHABETICAL top-N. Drop the cap
        # rather than publish a fake selection. Matches SimulationParams' coercion.
        if cap:
            logger.info(f"↔ sleeve {sleeve.get('id')}: momentum_ranking='none' → "
                        f"ignoring max_holdings={cap} (hold all eligible)")
        cap = None
    selected = [s for s, _ in (scored[:int(cap)] if cap else scored)]
    scores = dict(scored)

    cur_set, sel_set = set(current_holdings), set(selected)

    def _last_px(sym: str) -> float:
        closes = (px_series.get(sym) or ([], []))[1]
        return round(closes[-1], 2) if closes else 0.0

    return {
        "id": sleeve["id"],
        "label": sleeve["label"],
        "universe": universe,
        "data_as_of": d_latest.strftime("%Y-%m-%d"),
        "next_due_estimate": _next_rebalance_estimate(
            sleeve.get("rebalance_frequency", "monthly"),
            sleeve.get("rebalance_date", "first"), datetime.now().date()),
        "eligible_count": len(q_syms),
        "current_count": len(current_holdings),
        "probable_adds": [{"symbol": s, "score": scores.get(s, 0.0), "price": _last_px(s)}
                          for s in selected if s not in cur_set],
        "probable_exits": [{"symbol": s, "score": scores.get(s, 0.0), "price": _last_px(s)}
                           for s in sorted(cur_set - sel_set)],
        "holds": [{"symbol": s, "score": scores.get(s, 0.0), "price": _last_px(s)}
                  for s in selected if s in cur_set],
        # Equal-weight sizing needs the divisor the engine actually uses.
        "selected_count": len(selected),
    }


def _compute_preview_sync(p: dict) -> dict:
    pit = bool(p.get("point_in_time_membership", True))
    # Current per-sleeve books from the newest cached run (else empty diff base).
    last = mongo_conn.db.portfolio_runs.find_one(
        {"portfolio_id": p["id"]},
        {"result.sleeves": 1, "result.current_book": 1, "result.summary": 1},
        sort=[("created_at", -1)])
    last_result = (last or {}).get("result") or {}
    holdings_by_id, return_by_id = {}, {}
    for a in last_result.get("sleeves", []):
        holdings_by_id[a.get("id")] = a.get("final_holdings") or []
        return_by_id[a.get("id")] = float(a.get("total_return") or 0.0)
    sleeves = [_preview_sleeve_sync(s, pit, holdings_by_id.get(s["id"], []))
               for s in p["sleeves"]]
    # Tier/sector context for everything the preview mentions.
    syms = {x["symbol"] for sl in sleeves
            for k in ("probable_adds", "probable_exits", "holds") for x in sl[k]}

    # Executable plan. Only sleeves whose next rebalance has actually arrived get
    # re-targeted; the rest keep the book they hold, or the plan would show every
    # sleeve turning over at once and wildly overstate the trades to place.
    today_iso = datetime.now().date().isoformat()
    total_capital = float(p.get("total_capital") or 0.0)
    value_by_symbol = {r["symbol"]: float(r.get("market_value") or 0.0)
                       for r in (last_result.get("current_book") or [])}
    # A name two sleeves both hold is one merged book row; split it between them
    # so an untouched sleeve doesn't claim the other sleeve's share as its target.
    sleeves_holding: dict[str, int] = {}
    for cfg in p["sleeves"]:
        for sym in holdings_by_id.get(cfg["id"], []):
            sleeves_holding[sym] = sleeves_holding.get(sym, 0) + 1

    targets, prices = [], {}
    for cfg, sl in zip(p["sleeves"], sleeves):
        label = cfg.get("label") or cfg["id"]
        due = str(sl.get("next_due_estimate") or "") <= today_iso
        sl["is_due"] = due
        for key in ("holds", "probable_adds", "probable_exits"):
            for x in sl[key]:
                if x.get("price"):
                    prices[x["symbol"]] = x["price"]
        if due:
            n = int(sl.get("selected_count") or 0)
            if not n:
                continue
            grown = 1 + return_by_id.get(cfg["id"], 0.0) / 100
            sleeve_value = total_capital * float(cfg.get("weight") or 0.0) / 100 * grown
            per_name = sleeve_value / n
            for key in ("holds", "probable_adds"):
                for x in sl[key]:
                    targets.append({"symbol": x["symbol"], "sleeve": label,
                                    "target_value": per_name})
        else:
            # Not due: target == what it already holds, so every row nets to `hold`.
            for sym in holdings_by_id.get(cfg["id"], []):
                share = max(sleeves_holding.get(sym, 1), 1)
                targets.append({"symbol": sym, "sleeve": label,
                                "target_value": value_by_symbol.get(sym, 0.0) / share})

    current_book = last_result.get("current_book") or []
    # Tier/sector resolved BEFORE the plan — the resulting book is grouped by tier,
    # so the plan needs the labels rather than the UI bolting them on afterwards.
    meta = _symbol_meta_for(
        syms | {t["symbol"] for t in targets} | {r["symbol"] for r in current_book}) if (
        syms or targets or current_book) else {}
    plan = build_rebalance_plan(current_book, targets, prices, symbol_meta=meta)

    return sanitize_for_json({
        "success": True, "sleeves": sleeves,
        "plan": plan,
        "due_sleeves": [s["label"] for s in sleeves if s.get("is_due")],
        "symbol_meta": meta,
        "generated_at": datetime.now().isoformat(),
        "based_on_run": bool(last),
    })


@router.get("/api/portfolio/{portfolio_id}/preview")
async def preview_portfolio(portfolio_id: str):
    """Probable adds/exits at the next rebalance, from the latest data."""
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    p = mongo_conn.db.portfolios.find_one({"id": portfolio_id})
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return JSONResponse(content=await asyncio.to_thread(_compute_preview_sync, p))


@router.get("/api/portfolio/{portfolio_id}/preview.pdf")
async def preview_portfolio_pdf(portfolio_id: str):
    """The same rebalance plan as a shareable trade sheet."""
    import io as _io

    from fastapi.responses import StreamingResponse

    from rebalance_pdf import build_rebalance_pdf

    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    p = mongo_conn.db.portfolios.find_one({"id": portfolio_id})
    if not p:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    preview = await asyncio.to_thread(_compute_preview_sync, p)
    plan = preview.get("plan")
    if not plan:
        raise HTTPException(status_code=400, detail="no rebalance plan available for this portfolio")

    as_of = next((s.get("data_as_of") for s in preview.get("sleeves", []) if s.get("data_as_of")), "")
    pdf = await asyncio.to_thread(
        build_rebalance_pdf, plan,
        portfolio_name=p.get("name") or portfolio_id,
        as_of=as_of,
        due_sleeves=preview.get("due_sleeves") or [],
        symbol_meta=preview.get("symbol_meta") or {},
        generated_at=preview.get("generated_at", "")[:16].replace("T", " "),
    )
    safe = "".join(c for c in (p.get("name") or portfolio_id) if c.isalnum() or c in " -_").strip()
    filename = f"{safe or portfolio_id}_rebalance_{as_of or 'plan'}.pdf"
    return StreamingResponse(
        _io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------------------
# Broker connectivity — reads (holdings / funds / profile) plus the plan-scoped
# execution verbs in app/routers/broker.py.
# Security: no credentials accepted or returned over HTTP except the Kite
# request_token exchange (one-time, server-side secret). Guard: when
# MH_API_TOKEN is set, every /api/broker/* call must carry X-API-Key; when
# unset (local dev), only localhost clients are allowed.
# ⚠ Order placement IS implemented (Stage 2 shipped). No endpoint accepts a
# caller-supplied symbol/quantity/price — orders are built only from an approved
# sync-plan, behind the kill switch, caps, market hours and dry-run default.
# ---------------------------------------------------------------------------
import hmac as _hmac
import os

from fastapi import Header, Request


