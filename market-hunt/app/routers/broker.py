"""Broker endpoints: status/sessions/holdings, broker-mapping CRUD, sync plans,
order execution, fresh prices, notifications, live-vs-sim (moved verbatim from api_server).

SECURITY-SENSITIVE: every route calls _require_broker_auth (X-API-Key via
constant-time compare when MH_API_TOKEN is set; localhost-only otherwise).
Order execution is additionally gated by order_execution.py (kill switch,
value caps, market-hours guard).
"""
import asyncio
import hmac as _hmac
import logging
import os
from datetime import datetime, timedelta

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.db import mongo_conn
from app.serialization import sanitize_for_json

logger = logging.getLogger(__name__)
router = APIRouter()



def _require_broker_auth(request: Request, x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("MH_API_TOKEN", "").strip()
    if expected:
        if not x_api_key or not _hmac.compare_digest(x_api_key, expected):
            raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    else:
        host = (request.client.host if request.client else "") or ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient-local"):
            raise HTTPException(status_code=403,
                                detail="broker API is localhost-only until MH_API_TOKEN is set")


def _broker(name: str, account: str | None = None):
    from brokers import DhanClient, KiteClient
    if name == "kite":
        return KiteClient()
    if name == "dhan":
        return DhanClient(account or "default")
    raise HTTPException(status_code=404, detail=f"unknown broker: {name}")


def _adv_values(symbols: list[str], days: int = 20) -> dict[str, float]:
    """20-session average daily traded value (close × volume) per symbol,
    from the newest price partitions. Liquidity input for sync plans."""
    out: dict[str, list[float]] = {s: [] for s in symbols}
    remaining = set(symbols)
    for cname in sorted((c for c in mongo_conn.db.list_collection_names()
                         if c.startswith("prices_")), reverse=True):
        if not remaining:
            break
        for doc in mongo_conn.db[cname].aggregate([
                {"$match": {"symbol": {"$in": list(remaining)}}},
                {"$sort": {"date": -1}},
                {"$group": {"_id": "$symbol",
                            "rows": {"$push": {"c": "$close_price", "v": "$volume"}}}},
                {"$project": {"rows": {"$slice": ["$rows", days]}}}]):
            sym = doc["_id"]
            vals = [float(r.get("c") or 0) * float(r.get("v") or 0)
                    for r in doc["rows"] if r.get("c") and r.get("v")]
            if vals:
                out[sym] = vals
                if len(vals) >= days:
                    remaining.discard(sym)
    return {s: (sum(v) / len(v)) for s, v in out.items() if v}


@router.get("/api/broker/status")
async def broker_status(request: Request, x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    from brokers import DhanClient, KiteClient, list_dhan_accounts
    kite = KiteClient()
    accounts = {a: DhanClient(a).configured for a in list_dhan_accounts()}
    dhan = DhanClient()
    return JSONResponse(content={"success": True, "brokers": {
        "kite": {"configured": kite.configured, "session_active": kite.session_active,
                 "login_url": kite.login_url() if kite.configured else None},
        "dhan": {"configured": dhan.configured, "session_active": dhan.session_active,
                 "accounts": accounts},
    }, "auth_mode": "api_key" if os.environ.get("MH_API_TOKEN", "").strip() else "localhost_only",
       "trading_enabled": os.environ.get("MH_TRADING_ENABLED", "").strip().lower() == "true"})


@router.post("/api/broker/kite/session")
async def broker_kite_session(payload: dict, request: Request,
                              x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    request_token = (payload or {}).get("request_token", "").strip()
    if not request_token:
        raise HTTPException(status_code=400, detail="request_token required")
    from brokers import BrokerError, KiteClient
    try:
        info = await asyncio.to_thread(KiteClient().create_session, request_token)
    except BrokerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content={"success": True, "session": info})


@router.delete("/api/broker/kite/session")
async def broker_kite_logout(request: Request, x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    from brokers import KiteClient
    KiteClient().logout()
    return JSONResponse(content={"success": True})


@router.get("/api/broker/{name}/holdings")
async def broker_holdings(name: str, request: Request, account: str | None = None,
                          x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    from brokers import BrokerError
    try:
        rows = await asyncio.to_thread(lambda: [h.to_dict() for h in _broker(name, account).holdings()])
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return JSONResponse(content={"success": True, "holdings": rows})


@router.get("/api/broker/{name}/funds")
async def broker_funds(name: str, request: Request, account: str | None = None,
                       x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    from brokers import BrokerError
    try:
        f = await asyncio.to_thread(lambda: _broker(name, account).funds().to_dict())
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return JSONResponse(content={"success": True, "funds": f})


@router.get("/api/broker/{name}/profile")
async def broker_profile(name: str, request: Request, account: str | None = None,
                         x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    from brokers import BrokerError
    try:
        prof = await asyncio.to_thread(_broker(name, account).profile)
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return JSONResponse(content={"success": True, "profile": prof})


# ---------------------------------------------------------------------------
# Broker-account <-> portfolio mapping + sync plans.
# A mapping ties one portfolio to one broker account (the account should be
# DEDICATED to that portfolio — unknown holdings are never auto-sold unless
# sell_unknown is enabled). Sync plans are persisted in `sync_plans` with row
# lifecycle status "planned"; Stage 2 execution will consume the same rows.
# ---------------------------------------------------------------------------

@router.post("/api/broker-mapping")
async def create_broker_mapping(payload: dict, request: Request,
                                x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    import broker_sync
    portfolio_id = (payload or {}).get("portfolio_id")
    broker = (payload or {}).get("broker")
    if broker not in ("dhan", "kite"):
        raise HTTPException(status_code=400, detail="broker must be dhan or kite")
    if not mongo_conn.db.portfolios.find_one({"id": portfolio_id}):
        raise HTTPException(status_code=400, detail="unknown portfolio_id")
    account = (payload.get("account") or "default").lower()
    doc = {
        "id": f"map_{int(datetime.now().timestamp() * 1000)}",
        "portfolio_id": portfolio_id,
        "broker": broker,
        "account": account,
        "label": payload.get("label") or f"{broker}:{account} · {portfolio_id[-6:]}",
        "settings": broker_sync.normalize_settings(payload.get("settings")),
        "created_at": datetime.now().isoformat(),
        "last_modified": datetime.now().isoformat(),
    }
    mongo_conn.db.broker_mappings.insert_one(doc)
    doc.pop("_id", None)
    return JSONResponse(content={"success": True, "mapping": doc})


@router.get("/api/broker-mapping")
async def list_broker_mappings(request: Request,
                               x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    names = {p["id"]: p["name"] for p in mongo_conn.db.portfolios.find({}, {"id": 1, "name": 1})}
    out = []
    for m in mongo_conn.db.broker_mappings.find().sort("created_at", -1):
        m.pop("_id", None)
        m["portfolio_name"] = names.get(m["portfolio_id"], m["portfolio_id"])
        out.append(m)
    return JSONResponse(content={"success": True, "mappings": out})


@router.put("/api/broker-mapping/{mapping_id}")
async def update_broker_mapping(mapping_id: str, payload: dict, request: Request,
                                x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    import broker_sync
    m = mongo_conn.db.broker_mappings.find_one({"id": mapping_id})
    if not m:
        raise HTTPException(status_code=404, detail="mapping not found")
    upd = {"last_modified": datetime.now().isoformat()}
    if "label" in payload:
        upd["label"] = payload["label"]
    if "settings" in payload:
        upd["settings"] = broker_sync.normalize_settings(payload["settings"])
    mongo_conn.db.broker_mappings.update_one({"id": mapping_id}, {"$set": upd})
    return JSONResponse(content={"success": True, "mapping_id": mapping_id})


@router.delete("/api/broker-mapping/{mapping_id}")
async def delete_broker_mapping(mapping_id: str, request: Request,
                                x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    r = mongo_conn.db.broker_mappings.delete_one({"id": mapping_id})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="mapping not found")
    return JSONResponse(content={"success": True})


@router.post("/api/broker-mapping/{mapping_id}/sync-plan")
async def create_sync_plan(mapping_id: str, request: Request,
                           x_api_key: str | None = Header(default=None)):
    """Compute the current sim-vs-broker diff and persist it as a draft plan."""
    _require_broker_auth(request, x_api_key)
    import broker_sync
    from brokers import BrokerError
    m = mongo_conn.db.broker_mappings.find_one({"id": mapping_id})
    if not m:
        raise HTTPException(status_code=404, detail="mapping not found")
    run_doc = mongo_conn.db.portfolio_runs.find_one(
        {"portfolio_id": m["portfolio_id"]},
        {"result.current_book": 1, "created_at": 1, "data_version": 1},
        sort=[("created_at", -1)])
    if not run_doc:
        raise HTTPException(status_code=400,
                            detail="no cached run — run the portfolio first")
    sim_book = (run_doc.get("result") or {}).get("current_book", [])

    client = _broker(m["broker"], m.get("account"))
    try:
        live = await asyncio.to_thread(lambda: [h.to_dict() for h in client.holdings()])
        funds = await asyncio.to_thread(lambda: client.funds().to_dict())
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))

    candidates = sorted({b["symbol"] for b in sim_book} | {h["symbol"] for h in live})
    adv = await asyncio.to_thread(_adv_values, candidates)

    # Live LTPs from TradingView (fail-soft: missing symbols fall back to
    # broker LTP / last close) so LIMIT prices anchor to the current market.
    from fresh_prices import get_ltp_map
    fresh = await asyncio.to_thread(get_ltp_map, candidates)
    logger.info(f"🔄 fresh quotes: {len(fresh)}/{len(candidates)} symbols")

    plan_body = broker_sync.build_sync_plan(
        sim_book=sim_book, live_holdings=live,
        available_cash=funds.get("available_cash") or 0.0,
        settings=m.get("settings"), adv_values=adv, fresh_prices=fresh)
    plan = {
        "plan_id": f"plan_{int(datetime.now().timestamp() * 1000)}",
        "mapping_id": mapping_id,
        "portfolio_id": m["portfolio_id"],
        "broker": m["broker"],
        "account": m.get("account", "default"),
        "generated_at": datetime.now().isoformat(),
        "sim_as_of": run_doc["created_at"],
        "data_version": run_doc.get("data_version"),
        "status": "draft",
        **plan_body,
    }
    mongo_conn.db.sync_plans.insert_one(dict(plan))
    plan.pop("_id", None)
    # keep the 20 newest plans per mapping
    stale = list(mongo_conn.db.sync_plans.find({"mapping_id": mapping_id}, {"_id": 1})
                 .sort("generated_at", -1).skip(20))
    if stale:
        mongo_conn.db.sync_plans.delete_many({"_id": {"$in": [d["_id"] for d in stale]}})
    return JSONResponse(content=sanitize_for_json({"success": True, "plan": plan}))


@router.get("/api/broker-mapping/{mapping_id}/sync-plan/latest")
async def latest_sync_plan(mapping_id: str, request: Request,
                           x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    doc = mongo_conn.db.sync_plans.find_one({"mapping_id": mapping_id}, {"_id": 0},
                                            sort=[("generated_at", -1)])
    return JSONResponse(content=sanitize_for_json({"success": True, "plan": doc}))


# ---------------------------------------------------------------------------
# Stage 2 — approve & execute sync plans.
# Guards live in order_execution.py (kill switch, approval, freshness, caps,
# market hours, dry-run default). Every attempt is written to execution_log.
# ---------------------------------------------------------------------------

DHAN_INSTRUMENT_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


def _refresh_dhan_instruments() -> int:
    """Download Dhan's scrip master and cache NSE equity securityIds in Mongo.
    Header names are detected defensively — fails loud if the format changed."""
    import csv
    import io as _io

    import requests as _rq
    r = _rq.get(DHAN_INSTRUMENT_MASTER_URL, timeout=120)
    r.raise_for_status()
    reader = csv.DictReader(_io.StringIO(r.text))
    cols = reader.fieldnames or []

    def find(*needles):
        for c in cols:
            u = c.upper()
            if all(n in u for n in needles):
                return c
        return None

    c_exch = find("EXM", "EXCH") or find("EXCH", "ID")
    c_secid = find("SECURITY", "ID")
    c_sym = find("TRADING", "SYMBOL")
    c_series = find("SERIES")
    c_instr = find("INSTRUMENT", "NAME")
    if not (c_exch and c_secid and c_sym):
        raise RuntimeError(f"Dhan scrip master format changed — headers: {cols[:12]}")

    ops = []
    for row in reader:
        if (row.get(c_exch) or "").strip().upper() != "NSE":
            continue
        if c_instr and "EQUITY" not in (row.get(c_instr) or "").upper():
            continue
        series = (row.get(c_series) or "").strip().upper() if c_series else ""
        if c_series and series not in ("EQ", "BE", "BZ", "SM", "ST", ""):
            continue
        sym = (row.get(c_sym) or "").strip()
        sec = (row.get(c_secid) or "").strip()
        if sym and sec:
            ops.append({"symbol": sym, "security_id": sec, "series": series})
    if not ops:
        raise RuntimeError("Dhan scrip master parsed to zero NSE equity rows")
    coll = mongo_conn.db.dhan_instruments
    coll.delete_many({})
    coll.insert_many(ops)
    coll.create_index("symbol")
    return len(ops)


def _dhan_security_ids(symbols: list[str]) -> dict[str, str]:
    coll = mongo_conn.db.dhan_instruments
    if coll.estimated_document_count() == 0:
        _refresh_dhan_instruments()
    out: dict[str, str] = {}
    for d in coll.find({"symbol": {"$in": symbols}}, {"symbol": 1, "security_id": 1, "series": 1}):
        # prefer EQ series when a symbol appears in multiple series
        if d["symbol"] not in out or d.get("series") == "EQ":
            out[d["symbol"]] = d["security_id"]
    return out


@router.post("/api/sync-plan/{plan_id}/approve")
async def approve_sync_plan(plan_id: str, request: Request,
                            x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    import order_execution as oe
    plan = mongo_conn.db.sync_plans.find_one({"plan_id": plan_id})
    if not plan:
        raise HTTPException(status_code=404, detail="plan not found")
    if plan.get("status") != "draft":
        raise HTTPException(status_code=400, detail=f"plan is '{plan.get('status')}', not draft")
    if plan.get("feasible") is False:
        raise HTTPException(status_code=400, detail="plan is NOT FUNDED — fix funding, regenerate, then approve")
    if oe.plan_age_hours(plan["generated_at"]) > oe.PLAN_MAX_AGE_HOURS:
        raise HTTPException(status_code=400, detail="plan too old — regenerate before approving")
    mongo_conn.db.sync_plans.update_one(
        {"plan_id": plan_id},
        {"$set": {"status": "approved", "approved_at": datetime.now().isoformat()}})
    return JSONResponse(content={"success": True, "plan_id": plan_id, "status": "approved"})


@router.get("/api/sync-plan/{plan_id}")
async def get_sync_plan(plan_id: str, request: Request,
                        x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    plan = mongo_conn.db.sync_plans.find_one({"plan_id": plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(status_code=404, detail="plan not found")
    return JSONResponse(content=sanitize_for_json({"success": True, "plan": plan}))


@router.post("/api/sync-plan/{plan_id}/execute")
async def execute_sync_plan(plan_id: str, request: Request, fraction: float = 0.2,
                            dry_run: bool = True,
                            x_api_key: str | None = Header(default=None)):
    """Punch one slice (default 1/5) of an APPROVED plan. dry_run defaults TRUE:
    real orders require dry_run=false AND MH_TRADING_ENABLED=true AND market
    hours AND caps. SELLs go before BUYs. Every attempt is audited."""
    _require_broker_auth(request, x_api_key)
    import order_execution as oe
    from brokers import BrokerError
    plan = mongo_conn.db.sync_plans.find_one({"plan_id": plan_id})
    if not plan:
        raise HTTPException(status_code=404, detail="plan not found")

    errors = oe.validate_execution(plan, fraction=fraction, dry_run=dry_run)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    symbols = [r["symbol"] for r in plan.get("actions", [])]
    sec_ids = await asyncio.to_thread(_dhan_security_ids, symbols)
    orders, batch_errors = oe.build_execution_batch(plan, fraction=fraction,
                                                    security_ids=sec_ids)
    if batch_errors:
        raise HTTPException(status_code=400, detail="; ".join(batch_errors))
    if not orders:
        return JSONResponse(content={"success": True, "plan_id": plan_id,
                                     "orders": [], "note": "nothing left to punch"})

    audit = {"plan_id": plan_id, "mapping_id": plan.get("mapping_id"),
             "broker": plan.get("broker"), "account": plan.get("account", "default"),
             "fraction": fraction, "dry_run": dry_run,
             "at": datetime.now().isoformat(), "orders": orders, "results": []}

    if dry_run:
        mongo_conn.db.execution_log.insert_one(dict(audit))
        return JSONResponse(content=sanitize_for_json({
            "success": True, "plan_id": plan_id, "dry_run": True,
            "orders": orders,
            "note": "DRY RUN — nothing sent. Repeat with dry_run=false to punch."}))

    # Concurrency lock: two simultaneous execute calls must not double-punch.
    # Atomic claim; stale locks (>10 min — crashed run) are reclaimable.
    now_iso = datetime.now().isoformat()
    stale_before = (datetime.now() - timedelta(minutes=10)).isoformat()
    claimed = mongo_conn.db.sync_plans.find_one_and_update(
        {"plan_id": plan_id,
         "$or": [{"executing_lock": {"$exists": False}},
                 {"executing_lock": None},
                 {"executing_lock": {"$lt": stale_before}}]},
        {"$set": {"executing_lock": now_iso}})
    if claimed is None:
        raise HTTPException(status_code=409,
                            detail="another execution of this plan is in progress")

    client = _broker(plan["broker"], plan.get("account"))
    results = []
    punched_by_seq: dict[int, int] = {}
    aborted_error: str | None = None
    # Double-punch guard: whatever happens inside the loop, the finally block
    # ALWAYS records the audit trail, persists punched quantities, and releases
    # the lock — otherwise a mid-loop crash loses track of live orders and the
    # next execute would re-punch them.
    try:
        for o in orders:
            try:
                res = await asyncio.to_thread(
                    lambda o=o: client.place_order(
                        security_id=o["security_id"], side=o["side"], quantity=o["qty"],
                        order_type=o["order_type"], price=o["price"], product=o["product"]))
                results.append({**o, "order_id": res.get("order_id"),
                                "broker_status": res.get("order_status"), "error": None})
                punched_by_seq[o["seq"]] = o["qty"]
            except BrokerError as e:
                results.append({**o, "order_id": None, "broker_status": "FAILED",
                                "error": str(e)})
            except Exception as e:
                # Unexpected (library/network) failure: stop punching further
                # orders — connectivity is suspect — but fall through to the
                # persistence block below with everything sent so far.
                logger.error(f"execute {plan_id}: unexpected error mid-loop: {e}",
                             exc_info=True)
                results.append({**o, "order_id": None, "broker_status": "FAILED",
                                "error": f"unexpected: {e}"})
                aborted_error = str(e)
                break
    finally:
        audit["results"] = results
        try:
            mongo_conn.db.execution_log.insert_one(dict(audit))
        except Exception as ae:
            logger.error(f"execution audit write failed for {plan_id}: {ae}")

        # persist punched quantities + order ids on the plan rows
        actions = plan["actions"]
        for row in actions:
            if row["seq"] in punched_by_seq:
                row["punched_qty"] = int(row.get("punched_qty") or 0) + punched_by_seq[row["seq"]]
                row["status"] = "punched" if row["punched_qty"] >= row["qty"] else "approved"
                ids = row.get("order_ids") or []
                ids += [r["order_id"] for r in results
                        if r["seq"] == row["seq"] and r.get("order_id")]
                row["order_ids"] = ids
        all_done = all(int(r.get("punched_qty") or 0) >= r["qty"] for r in actions)
        mongo_conn.db.sync_plans.update_one(
            {"plan_id": plan_id},
            {"$set": {"actions": actions,
                      "status": "punched" if all_done else "executing",
                      "last_executed_at": datetime.now().isoformat()},
             "$unset": {"executing_lock": ""}})

    ok = sum(1 for r in results if r.get("order_id"))
    if aborted_error:
        return JSONResponse(status_code=502, content=sanitize_for_json({
            "success": False, "plan_id": plan_id, "dry_run": False, "aborted": True,
            "error": f"execution aborted mid-run: {aborted_error}; "
                     "punched state persisted — review results before re-running",
            "punched": ok, "failed": len(results) - ok, "results": results}))
    return JSONResponse(content=sanitize_for_json({
        "success": True, "plan_id": plan_id, "dry_run": False,
        "punched": ok, "failed": len(results) - ok, "results": results}))


@router.post("/api/sync-plan/{plan_id}/refresh-status")
async def refresh_sync_plan_status(plan_id: str, request: Request,
                                   x_api_key: str | None = Header(default=None)):
    """Pull today's broker order book and update fill status on plan rows."""
    _require_broker_auth(request, x_api_key)
    from brokers import BrokerError
    plan = mongo_conn.db.sync_plans.find_one({"plan_id": plan_id})
    if not plan:
        raise HTTPException(status_code=404, detail="plan not found")
    client = _broker(plan["broker"], plan.get("account"))
    try:
        book = await asyncio.to_thread(client.orders_today)
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    by_id = {o["order_id"]: o for o in book if o.get("order_id")}
    actions = plan["actions"]
    for row in actions:
        fills = [by_id[i] for i in (row.get("order_ids") or []) if i in by_id]
        if fills:
            row["broker_statuses"] = [f["status"] for f in fills]
            row["filled_qty"] = sum(int(f.get("traded_qty") or 0) for f in fills)
            if row["filled_qty"] >= row["qty"]:
                row["status"] = "filled"
    mongo_conn.db.sync_plans.update_one({"plan_id": plan_id}, {"$set": {"actions": actions}})
    return JSONResponse(content=sanitize_for_json({
        "success": True, "plan_id": plan_id,
        "rows": [{"symbol": r["symbol"], "qty": r["qty"],
                  "punched_qty": r.get("punched_qty") or 0,
                  "filled_qty": r.get("filled_qty") or 0,
                  "status": r.get("status")} for r in actions]}))


@router.get("/api/prices/fresh")
async def fresh_prices_endpoint(symbols: str, request: Request,
                                x_api_key: str | None = Header(default=None)):
    """Live LTPs via TradingView for a comma-separated symbol list (<=100).
    Reusable by the portfolio and broker pages."""
    _require_broker_auth(request, x_api_key)
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:100]
    if not syms:
        raise HTTPException(status_code=400, detail="symbols required")
    from fresh_prices import get_ltp_map
    ltps = await asyncio.to_thread(get_ltp_map, syms)
    return JSONResponse(content=sanitize_for_json({
        "success": True, "prices": ltps,
        "fetched": len(ltps), "requested": len(syms)}))


@router.post("/api/broker/dhan/instruments/refresh")
async def refresh_instruments(request: Request,
                              x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    n = await asyncio.to_thread(_refresh_dhan_instruments)
    return JSONResponse(content={"success": True, "instruments": n})


@router.post("/api/notify/rebalance/run")
async def notify_rebalance_run(request: Request, dry_run: bool = True,
                               lead_days: int = 2,
                               x_api_key: str | None = Header(default=None)):
    """Manually trigger the Stage-1 notification sweep. dry_run defaults True —
    pass dry_run=false to actually send to Telegram."""
    _require_broker_auth(request, x_api_key)
    import notify_rebalance as nr
    out = await asyncio.to_thread(nr.run, lead_days, dry_run)
    return JSONResponse(content=sanitize_for_json({"success": True, **out}))


@router.get("/api/portfolio/{portfolio_id}/live-vs-sim")
async def portfolio_live_vs_sim(portfolio_id: str, request: Request, broker: str = "dhan",
                                account: str | None = None,
                                x_api_key: str | None = Header(default=None)):
    """Diff the broker's real holdings against the simulated book (newest cached
    run): matched names with qty/value drift, sim-only, broker-only."""
    _require_broker_auth(request, x_api_key)
    doc = mongo_conn.db.portfolio_runs.find_one(
        {"portfolio_id": portfolio_id}, {"result.current_book": 1, "created_at": 1},
        sort=[("created_at", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="no cached run — run the portfolio first")
    sim_book = {b["symbol"]: b for b in (doc.get("result") or {}).get("current_book", [])}
    from brokers import BrokerError
    try:
        live = await asyncio.to_thread(_broker(broker, account).holdings)
    except BrokerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    live_map = {h.symbol: h for h in live}
    matched, sim_only, broker_only = [], [], []
    for sym, b in sorted(sim_book.items()):
        if sym in live_map:
            h = live_map[sym]
            matched.append({"symbol": sym,
                            "sim_qty": b.get("quantity"), "live_qty": h.quantity,
                            "sim_value": b.get("market_value"), "live_value": h.market_value,
                            "value_drift": (round(h.market_value - b.get("market_value", 0), 2)
                                            if h.market_value is not None else None)})
        else:
            sim_only.append({"symbol": sym, "sim_qty": b.get("quantity"),
                             "sim_value": b.get("market_value")})
    for sym, h in sorted(live_map.items()):
        if sym not in sim_book:
            broker_only.append({"symbol": sym, "live_qty": h.quantity,
                                "live_value": h.market_value})
    return JSONResponse(content=sanitize_for_json({
        "success": True, "broker": broker, "sim_as_of": doc["created_at"],
        "matched": matched, "sim_only": sim_only, "broker_only": broker_only,
        "match_count": len(matched), "sim_only_count": len(sim_only),
        "broker_only_count": len(broker_only)}))

