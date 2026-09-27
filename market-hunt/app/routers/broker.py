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
    if "accounts" in payload:
        # A mapping may span several accounts of one broker. Refuse any that has no
        # stored credentials, rather than leaving a mapping that fails on every read.
        accts = [str(a).strip().lower() for a in (payload["accounts"] or []) if str(a).strip()]
        if not accts:
            raise HTTPException(status_code=400, detail="accounts cannot be empty")
        if m["broker"] == "dhan":
            from brokers.dhan import list_accounts
            avail = list_accounts()
            missing = [a for a in accts if a not in avail]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"no stored credentials for: {', '.join(missing)} — "
                           f"available: {', '.join(avail) or 'none'}")
        elif len(accts) > 1:
            raise HTTPException(status_code=400,
                                detail="only Dhan supports multiple accounts")
        upd["accounts"] = accts
        upd["account"] = accts[0]        # keep the legacy single-account field valid
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

    # A mapping may span SEVERAL accounts. Cash does not move between them, so each
    # is read separately and planned as its own self-funding book.
    accounts = m.get("accounts") or [m.get("account") or "default"]
    by_account: dict[str, dict] = {}
    try:
        for acct in accounts:
            c = _broker(m["broker"], acct)
            by_account[acct] = {
                "holdings": await asyncio.to_thread(
                    lambda c=c: [h.to_dict() for h in c.holdings()]),
                "cash": await asyncio.to_thread(
                    lambda c=c: c.funds().available_cash),
            }
    except BrokerError as e:
        # A PARTIAL read would plan against a book missing real positions, and
        # every one of them would look like a BUY already made.
        raise HTTPException(status_code=502, detail=str(e))
    live = [h for a in accounts for h in by_account[a]["holdings"]]
    funds = {"available_cash": sum(by_account[a]["cash"] for a in accounts)}

    candidates = sorted({b["symbol"] for b in sim_book} | {h["symbol"] for h in live})
    adv = await asyncio.to_thread(_adv_values, candidates)

    # Live LTPs from TradingView (fail-soft: missing symbols fall back to
    # broker LTP / last close) so LIMIT prices anchor to the current market.
    from fresh_prices import get_ltp_map
    fresh = await asyncio.to_thread(get_ltp_map, candidates)
    logger.info(f"🔄 fresh quotes: {len(fresh)}/{len(candidates)} symbols")

    # Quantities the user holds privately. These are a NEVER-SELL FLOOR for the
    # planner, not just a reconciliation display: without this the holdings page
    # could show "20 of these are yours" while the plan queued all 40 for sale.
    pf = mongo_conn.db.portfolios.find_one({"id": m["portfolio_id"]},
                                           {"_id": 0, "discretionary": 1}) or {}
    plan_body = broker_sync.build_sync_plan(
        sim_book=sim_book,
        live_holdings=None if len(accounts) > 1 else live,
        available_cash=None if len(accounts) > 1 else (funds.get("available_cash") or 0.0),
        settings=m.get("settings"), adv_values=adv, fresh_prices=fresh,
        discretionary=pf.get("discretionary") or {},
        by_account=by_account if len(accounts) > 1 else None)
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
    c_isin = find("ISIN")          # optional — absent in some master revisions
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
        isin = (row.get(c_isin) or "").strip().upper() if c_isin else ""
        if sym and sec:
            ops.append({"symbol": sym, "security_id": sec, "series": series,
                        # ISIN is the cross-broker identity; it survives renames.
                        "isin": isin,
                        "dhan_security_id": sec,
                        # Kite orders by exchange tradingsymbol, which on NSE is
                        # the same string as the scrip master's trading symbol.
                        "kite_tradingsymbol": sym})
    if not ops:
        raise RuntimeError("Dhan scrip master parsed to zero NSE equity rows")
    coll = mongo_conn.db.instruments
    coll.delete_many({})
    coll.insert_many(ops)
    coll.create_index("symbol")
    coll.create_index("isin")
    return len(ops)


def _resolve_instruments(rows: list[dict], broker: str) -> tuple[dict[str, str], list[str]]:
    """Plan rows -> {symbol: broker identifier}, matched on ISIN first so a ticker
    rename does not silently drop the row. Unresolved rows are REPORTED, never
    guessed — the caller refuses them."""
    import instrument_master as im
    coll = mongo_conn.db.instruments
    if coll.estimated_document_count() == 0:
        _refresh_dhan_instruments()
    symbols = [(r.get("symbol") or "").strip() for r in rows]
    isins = [(r.get("isin") or "").strip().upper() for r in rows if r.get("isin")]
    docs = list(coll.find({"$or": [{"symbol": {"$in": symbols}},
                                   {"isin": {"$in": isins}}]},
                          {"_id": 0, "symbol": 1, "isin": 1, "series": 1,
                           "dhan_security_id": 1, "kite_tradingsymbol": 1}))
    return im.resolve(rows, im.build_index(docs), broker)


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
    # Approval is the commit point, so this is where the chain is laid out:
    # single-sided groups, SELLs first, advanced one step per /execute call.
    set_fields = {"status": "approved", "approved_at": datetime.now().isoformat()}
    group_size = int((plan.get("settings") or {}).get("group_size") or 0)
    if group_size > 0:
        accts = plan.get("accounts") or []
        if accts:
            # One chain per account: proceeds are per-account, so budgets are too.
            per = {}
            for a in accts:
                rows = [r for r in plan.get("actions", []) if r.get("account") == a]
                per[a] = {"groups": oe.build_groups(rows, group_size=group_size),
                          "cursor": 0, "realized_cash": 0.0, "already_spent": 0.0,
                          "starting_cash": float(((plan.get("totals") or {})
                                                  .get("by_account") or {})
                                                 .get(a, {}).get("available_cash") or 0)}
            set_fields.update({"by_account": per})
        else:
            set_fields.update({
                "groups": oe.build_groups(plan.get("actions", []), group_size=group_size),
                "cursor": 0, "realized_cash": 0.0, "already_spent": 0.0})
    mongo_conn.db.sync_plans.update_one({"plan_id": plan_id}, {"$set": set_fields})
    return JSONResponse(content={"success": True, "plan_id": plan_id,
                                 "status": "approved",
                                 "groups": len(set_fields.get("groups") or [])})


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
                            dry_run: bool = True, account: str | None = None,
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

    # ---- group-chained mode (opt-in) ---------------------------------------
    # A plan carrying `groups` advances exactly ONE step per call: punch a group,
    # wait for its fills, settle it, then size the next BUY group from the cash
    # those sells actually realized. Plans without `groups` keep the original
    # whole-plan slicing behaviour unchanged.
    # On a routed plan, one /execute call advances ONE account's chain. `account`
    # selects it; omitted, the next account with pending work is taken.
    acct_sel = None
    if plan.get("accounts"):
        acct_sel = account or oe.next_account_with_work(plan)
        if acct_sel is None:
            return JSONResponse(content={"success": True, "plan_id": plan_id,
                                         "step": "COMPLETE",
                                         "info": {"reason": "all accounts settled"}})
        if acct_sel not in plan["accounts"]:
            raise HTTPException(status_code=400,
                                detail=f"account '{acct_sel}' is not on this plan")

    group_idx = None
    chain = oe._chain(plan, acct_sel)
    if chain.get("groups"):
        fills: dict = {}
        if any(g.get("state") == "punched" for g in chain["groups"]):
            try:
                book = await asyncio.to_thread(
                    _broker(plan["broker"], acct_sel or plan.get("account")).orders_today)
                fills = {o["symbol"]: o for o in book if o.get("symbol")}
            except BrokerError as e:
                raise HTTPException(status_code=502, detail=str(e))
        step, info = oe.advance_plan(plan, fills=fills, account=acct_sel)
        if step != "PUNCH":
            # WAIT / SETTLE / STALLED / REPRICE / COMPLETE — persist and report.
            persist = {"last_executed_at": datetime.now().isoformat()}
            if acct_sel:
                persist["by_account"] = plan["by_account"]
            else:
                persist.update({"groups": plan["groups"],
                                "cursor": plan.get("cursor", 0),
                                "realized_cash": plan.get("realized_cash", 0.0),
                                "already_spent": plan.get("already_spent", 0.0)})
            mongo_conn.db.sync_plans.update_one({"plan_id": plan_id}, {"$set": persist})
            return JSONResponse(content=sanitize_for_json({
                "success": True, "plan_id": plan_id, "account": acct_sel,
                "step": step, "info": info}))
        group_idx = int(chain.get("cursor") or 0)

    if group_idx is not None:
        g = chain["groups"][group_idx]
        # Take the CURRENT action rows by seq (they carry punched_qty), applying
        # any budget trim the buy sizing decided. Using the group's own stored
        # copies would read a stale punched_qty after a Mongo round-trip.
        sized_by_seq = {r["seq"]: r["qty"] for r in (g.get("sized_rows") or [])}
        seqs = {r["seq"] for r in (g.get("sized_rows") or g["rows"])}
        rows = []
        for a in plan.get("actions", []):
            if a["seq"] in seqs:
                rows.append({**a, "qty": sized_by_seq.get(a["seq"], a["qty"])})
    else:
        rows = plan.get("actions", [])

    sec_ids, unresolved = await asyncio.to_thread(
        _resolve_instruments, rows, plan["broker"])
    if unresolved:
        # Refuse rather than guess: an unresolved identity would otherwise be
        # silently absent from the rebalance.
        raise HTTPException(status_code=400, detail="; ".join(unresolved))
    orders, batch_errors, batch_warnings = oe.build_execution_batch(
        {**plan, "actions": rows}, fraction=fraction, security_ids=sec_ids)
    if batch_errors:
        raise HTTPException(status_code=400, detail="; ".join(batch_errors))
    if not orders:
        return JSONResponse(content={"success": True, "plan_id": plan_id,
                                     "orders": [], "warnings": batch_warnings,
                                     "note": "nothing left to punch"})

    audit = {"plan_id": plan_id, "mapping_id": plan.get("mapping_id"),
             "broker": plan.get("broker"), "account": plan.get("account", "default"),
             "fraction": fraction, "dry_run": dry_run,
             "at": datetime.now().isoformat(), "orders": orders,
             "warnings": batch_warnings, "results": []}

    if dry_run:
        mongo_conn.db.execution_log.insert_one(dict(audit))
        return JSONResponse(content=sanitize_for_json({
            "success": True, "plan_id": plan_id, "dry_run": True,
            "orders": orders, "warnings": batch_warnings,
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

    # Orders execute in the account the row was routed to — never a plan-level
    # default, which would place them in the wrong book.
    client = _broker(plan["broker"], acct_sel or plan.get("account"))
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
        update = {"actions": actions,
                  "status": "punched" if all_done else "executing",
                  "last_executed_at": datetime.now().isoformat()}
        if group_idx is not None:
            # Mark the group in flight so the NEXT call checks its fills rather
            # than punching it again.
            chain["groups"][group_idx]["state"] = "punched"
            chain["groups"][group_idx]["punched_at"] = datetime.now().isoformat()
            if acct_sel:
                update["by_account"] = plan["by_account"]
            else:
                update["groups"] = chain["groups"]
                update["cursor"] = chain.get("cursor", 0)
        mongo_conn.db.sync_plans.update_one(
            {"plan_id": plan_id},
            {"$set": update, "$unset": {"executing_lock": ""}})

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
    # Orders execute in the account the row was routed to — never a plan-level
    # default, which would place them in the wrong book.
    client = _broker(plan["broker"], acct_sel or plan.get("account"))
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

