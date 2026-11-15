"""Fund trading desk API (spec §8).

Access control (security review 2026-09-27):
  * localhost-only unless MH_API_TOKEN is set (then X-API-Key), like /api/broker;
  * every state-changing request (POST/PUT) must carry `X-Fund-Desk: 1`. A foreign web page cannot send a custom
    header without a CORS preflight, which the backend's allow-list refuses; and when the browser sends an Origin,
    it must be on the CORS allow-list. Together these stop cross-site request forgery from other websites;
  * inputs are type- and range-checked; sleeve universes and ETF symbols must exist in the database.

Model symbols, quantities and prices come only from the engine — no endpoint accepts them. Execution records accept
the manager's actual fill quantity and price for a line of a published ticket; a price outside the traded range
needs an explicit confirm. The other-holdings register accepts only symbols known to the market database.
"""
import csv
import hmac as _hmac
import io
import logging
import os
import threading
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

import settings
from app.db import mongo_conn
from app.serialization import sanitize_for_json
from fund import papers, service
from fund.config import validate
from fund.data import latest_closes, traded_range
from fund.store import FundStore

logger = logging.getLogger(__name__)


def _require_local(request: Request, x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("MH_API_TOKEN", "").strip()
    if expected:
        if not x_api_key or not _hmac.compare_digest(x_api_key, expected):
            raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    else:
        host = (request.client.host if request.client else "") or ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient-local"):
            raise HTTPException(status_code=403, detail="fund API is localhost-only until MH_API_TOKEN is set")


def _require_desk_write(request: Request):
    """CSRF guard for state-changing requests."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get("x-fund-desk") != "1":
        raise HTTPException(status_code=403, detail="state-changing requests must come from the desk (X-Fund-Desk header)")
    origin = request.headers.get("origin")
    if origin and origin not in settings.CORS_ORIGINS:
        raise HTTPException(status_code=403, detail=f"origin {origin} is not allowed")


router = APIRouter(prefix="/api/fund", dependencies=[Depends(_require_local), Depends(_require_desk_write)])
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _store() -> FundStore:
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    return FundStore(mongo_conn.db)


def _ok(payload):
    return JSONResponse(content=sanitize_for_json(payload))


def _refused(e: Exception):
    raise HTTPException(status_code=409, detail=str(e))


def _run_job(job_id: str, fn, *args):
    def progress(msg):
        with _jobs_lock:
            _jobs[job_id]["message"] = msg
    try:
        out = fn(*args, progress=progress)
        with _jobs_lock:
            _jobs[job_id].update(status="done", result=out, finished_at=datetime.now(timezone.utc).isoformat())
    except service.RunRefused as e:
        with _jobs_lock:
            _jobs[job_id].update(status="refused", error=str(e))
    except Exception as e:  # noqa: BLE001 — surfaced to the desk, logged with traceback
        logger.exception("fund job %s failed", job_id)
        with _jobs_lock:
            _jobs[job_id].update(status="error", error=f"{type(e).__name__}: {e}")


def _start(fn, *args) -> str:
    with _jobs_lock:
        if any(j["status"] == "running" for j in _jobs.values()):
            raise HTTPException(status_code=409, detail="a run is already being computed")
        job_id = uuid.uuid4().hex[:10]
        _jobs[job_id] = {"job_id": job_id, "status": "running", "message": "starting",
                         "started_at": datetime.now(timezone.utc).isoformat()}
    threading.Thread(target=_run_job, args=(job_id, fn, *args), daemon=True).start()
    return job_id


# ---------------------------------------------------------------------------------------------------- config
class ConfigIn(BaseModel):
    name: str | None = None
    capital: float | None = None
    model_start: str | None = None
    backfill_weekday: int | None = None
    slippage_bp: float | None = None
    reset_cost: float | None = None
    limit_band: float | None = None
    min_trim_value: float | None = None          # ticket only (not a new model version)
    rebalance_months: list[int] | None = None
    rebalance_band: float | None = None          # send null explicitly to switch the drift band off
    sleeves: list[dict] | None = None


@router.get("")
def get_config():
    return _ok(_store().get_config())


@router.put("")
def put_config(body: ConfigIn):
    st = _store()
    new = {**st.get_config(), **body.model_dump(exclude_unset=True)}
    universes = set(st.db.index_reconstitution.distinct("index_name"))
    wanted = {sl.get("symbol") for sl in (new.get("sleeves") or []) if isinstance(sl, dict)}
    symbols = {d["symbol"] for d in st.db.symbol_mappings.find(
        {"symbol": {"$in": [x for x in wanted if isinstance(x, str)]}}, {"symbol": 1})}
    errs = validate(new, date.today(), universes, symbols)
    if errs:
        raise HTTPException(status_code=400, detail="; ".join(errs))
    return _ok(st.save_config({k: v for k, v in new.items() if k not in ("_id", "version", "updated_at")}))


# ---------------------------------------------------------------------------------------------------- status
@router.get("/status")
def status():
    st = _store()
    cfg = st.get_config()
    runs = st.list_runs(20)
    published = [r for r in runs if r["status"] in ("PUBLISHED", "EXECUTING", "CLOSED") and r["version"] == cfg["version"]]
    latest = service.latest_bench_date(mongo_conn.db)
    with _jobs_lock:
        running = [j for j in _jobs.values() if j["status"] == "running"]
    return _ok({"config_version": cfg["version"], "fund_name": cfg["name"], "capital": cfg["capital"],
                "model_start": cfg["model_start"], "latest_prices": str(latest) if latest else None,
                "today": str(date.today()), "last_run": runs[0] if runs else None,
                "last_published": published[0] if published else None, "open_tickets":
                    [r["_id"] for r in published if r["status"] in ("PUBLISHED", "EXECUTING")],
                "job": running[0] if running else None})


# ---------------------------------------------------------------------------------------------------- runs
@router.post("/runs")
def create_run():
    job_id = _start(lambda progress: service.compute_run(mongo_conn.db, _store(), progress=progress)["_id"])
    return _ok({"job_id": job_id})


@router.get("/jobs/{job_id}")
def job(job_id: str):
    with _jobs_lock:
        j = _jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return _ok(j)


@router.get("/runs")
def list_runs():
    return _ok(_store().list_runs())


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    st = _store()
    r = st.get_run(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run not found")
    r.pop("checkpoint", None)
    r["executions"] = st.executions(run_id)
    return _ok(r)


@router.post("/runs/{run_id}/publish")
def publish(run_id: str):
    try:
        r = service.publish(mongo_conn.db, _store(), run_id)
    except service.RunRefused as e:
        _refused(e)
    r.pop("checkpoint", None)
    return _ok(r)


@router.post("/runs/{run_id}/discard")
def discard(run_id: str):
    st = _store()
    r = st.get_run(run_id)
    if not r or r["status"] != "DRAFT":
        raise HTTPException(status_code=409, detail="only a DRAFT can be discarded")
    st.set_status(run_id, "DISCARDED")
    st.journal("RUN_DISCARDED", {"run_id": run_id})
    return _ok({"run_id": run_id, "status": "DISCARDED"})


@router.get("/runs/{run_id}/ticket")
def ticket(run_id: str, format: str = "json"):
    r = _store().get_run(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run not found")
    if format == "csv":
        safe_id = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")
        return PlainTextResponse(ticket_csv(r["ticket"]), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename=ticket_{safe_id}.csv"})
    return _ok({"run_id": run_id, "status": r["status"], "fill_date": r["fill_date"], "ticket": r["ticket"],
                "residuals": r.get("residuals", [])})


def _cell(v):
    """Neutralise spreadsheet formula injection: text starting with = + - @ gets a leading apostrophe."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def ticket_csv(lines: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["line_id", "sleeve", "symbol", "side", "qty", "ref_px", "limit_px", "value", "full_exit", "reason"])
    for l in lines:
        w.writerow([_cell(l["line_id"]), _cell(l["sleeve"]), _cell(l["symbol"]), l["side"], l["qty"],
                    f"{l['ref_px']:.2f}", f"{l['limit_px']:.2f}", f"{l['value']:.0f}", l["full_exit"],
                    _cell(l["reason_text"])])
    return buf.getvalue()


class ExecutionIn(BaseModel):
    line_id: str
    status: str                        # done | partial | extra | skipped
    qty: int | None = None
    price: float | None = None
    note: str | None = None


@router.post("/runs/{run_id}/executions")
def executions(run_id: str, items: list[ExecutionIn], confirm: bool = False):
    """409 with {"warnings": [...]} when a fill price needs review; resend with ?confirm=true to save as entered."""
    st = _store()
    try:
        return _ok(service.record_executions(
            st, run_id, [{k: v for k, v in i.model_dump().items() if v is not None} for i in items],
            price_range=lambda syms, since: traded_range(st.db, syms, since), confirm=confirm))
    except service.PriceWarnings as e:
        raise HTTPException(status_code=409, detail={"message": "fill prices need review", "warnings": e.warnings})
    except service.RunRefused as e:
        _refused(e)


class CashFlowIn(BaseModel):
    amount: float
    note: str | None = None


@router.get("/cashflows")
def list_cashflows():
    st = _store()
    return _ok(st.cashflows(st.get_config()["version"]))


@router.post("/cashflows")
def add_cashflow(body: CashFlowIn):
    try:
        return _ok(service.add_cashflow(_store(), body.amount, body.note or ""))
    except service.RunRefused as e:
        _refused(e)


@router.post("/cashflows/{flow_id}/cancel")
def cancel_cashflow(flow_id: str):
    try:
        service.cancel_cashflow(_store(), flow_id)
    except service.RunRefused as e:
        _refused(e)
    return _ok({"id": flow_id, "status": "CANCELLED"})


class AckIn(BaseModel):
    run_id: str


@router.post("/drift/ack")
def ack_drift(body: AckIn):
    st = _store()
    r = st.get_run(body.run_id)
    if not r or not r.get("drift_hash"):
        raise HTTPException(status_code=409, detail="this run shows no drift to acknowledge")
    cfg = st.get_config()
    st.db.fund_config.update_one({"_id": "main"}, {"$set": {"drift_ack": {"hash": r["drift_hash"], "run_id": r["_id"]}}})
    st.journal("DRIFT_ACKNOWLEDGED", {"run_id": r["_id"], "hash": r["drift_hash"], "version": cfg["version"]})
    return _ok({"acknowledged": r["drift_hash"], "note": "compute a new run — drift now shows as a warning"})


# ---------------------------------------------------------------------------------------------------- books
@router.get("/books")
def books():
    st = _store()
    cfg = st.get_config()
    r = st.db.fund_runs.find_one({"version": cfg["version"], "status": {"$ne": "DISCARDED"}},
                                 sort=[("created_at", -1)])
    if not r:
        return _ok({"run_id": None, "books": []})
    return _ok({"run_id": r["_id"], "signal_date": r["signal_date"], "fund": r["fund"], "books": r["books"],
                "curve": r["curve"], "sleeves": r["sleeves"]})


@router.get("/performance")
def performance():
    """Model performance from model start: the whole fund and every book (replayed on request, cached ~15 min)."""
    try:
        return _ok(service.performance(mongo_conn.db, _store()))
    except service.RunRefused as e:
        _refused(e)


@router.get("/actual")
def actual():
    st = _store()
    return _ok(service.actual_book(st, price_of=lambda syms: latest_closes(st.db, syms)))


class OtherHoldingIn(BaseModel):
    qty: int
    cost_basis: float | None = None
    note: str | None = None


@router.get("/other-holdings")
def other_holdings():
    """Everything held outside the books — manual entries plus extra-fill surplus — for the desk and AI review."""
    st = _store()
    return _ok(service.other_holdings_view(st, price_of=lambda syms: latest_closes(st.db, syms)))


@router.put("/other-holdings")
def put_other_holdings(body: dict[str, OtherHoldingIn]):
    st = _store()
    known = lambda s: bool(st.db.symbol_mappings.find_one({"symbol": s}) or st.db.index_meta.find_one({"Symbol": s}))
    try:
        service.set_other_holdings(st, {k: v.model_dump() for k, v in body.items()}, known)
        return _ok(service.other_holdings_view(st, price_of=lambda syms: latest_closes(st.db, syms)))
    except service.RunRefused as e:
        _refused(e)


@router.get("/journal")
def journal(limit: int = 200):
    return _ok(_store().read_journal(min(max(limit, 1), 1000)))


# ---------------------------------------------------------------------------------------------------- streak watch
@router.get("/streak-watch")
def streak_watch():
    """Grid-10 sleeves: actual holdings with mean_long vs its 222-SMA (below = exit) and Grid-10's next picks."""
    from fund.watch import streak_watch as build
    st = _store()
    return _ok(build(st.db, st))


class ManualTradeIn(BaseModel):
    sleeve: str
    symbol: str
    side: str
    qty: int
    price: float
    note: str | None = None
    trade_date: str | None = None


@router.get("/manual-trades")
def manual_trades():
    st = _store()
    return _ok(st.manual_trades(st.get_config()["version"]))


@router.post("/manual-trades")
def add_manual_trade(body: ManualTradeIn):
    st = _store()
    known = lambda s: bool(st.db.symbol_mappings.find_one({"symbol": s}) or st.db.index_meta.find_one({"Symbol": s}))
    try:
        return _ok(service.record_manual_trade(st, body.model_dump(), known))
    except service.RunRefused as e:
        _refused(e)


@router.delete("/manual-trades/{trade_id}")
def delete_manual_trade(trade_id: str):
    try:
        service.delete_manual_trade(_store(), trade_id)
        return _ok({"deleted": trade_id})
    except service.RunRefused as e:
        _refused(e)


# ---------------------------------------------------------------------------------------------------- AI Broadcast
@router.get("/papers")
def list_papers():
    return _ok(papers.list_papers(_store()))


@router.get("/papers/{paper_id}")
def get_paper(paper_id: str):
    p = papers.get_paper(_store(), paper_id)
    if not p:
        raise HTTPException(status_code=404, detail="paper not found")
    return _ok(p)


@router.post("/papers")
def publish_paper(body: dict):
    """Structured paper (spec docs/specs/2026-10-04-ai-broadcast.md); validated strictly, stored as text."""
    try:
        return _ok(papers.publish(_store(), body))
    except papers.PaperRefused as e:
        _refused(e)


@router.post("/papers/{paper_id}/archive")
def archive_paper(paper_id: str):
    try:
        papers.archive(_store(), paper_id)
        return _ok({"paper_id": paper_id, "status": "ARCHIVED"})
    except papers.PaperRefused as e:
        _refused(e)
