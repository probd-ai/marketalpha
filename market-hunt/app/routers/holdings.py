"""Holdings reconciliation tools — the callable surface for the AI bookkeeper.

The model resolves company names, interprets intent and writes the journal narrative.
Everything here is deterministic: parsing, quantity arithmetic, state transitions and
persistence. That split is deliberate — these numbers become orders, and a reasoning
slip in the arithmetic would be indistinguishable from a genuine gap.

The USER locks. The model may prepare and recommend a lock, never record one on its
own: the lock is the human gate that will eventually authorise execution.

SECURITY: mirrors the broker router — localhost-only unless MH_API_TOKEN is set.
Broker exports contain PII (holder name, UCC, mobile, email); none of it is persisted,
and the account is tracked only by a one-way fingerprint.
"""
import datetime as dt
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

import broker_holdings as bh
import holdings_store as hs
from app.db import mongo_conn
from app.routers.broker import _require_broker_auth

logger = logging.getLogger(__name__)
router = APIRouter()


def _db():
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    return mongo_conn.db


def _portfolio(db, portfolio_id: str) -> dict:
    p = db.portfolios.find_one({"id": portfolio_id})
    if not p:
        raise HTTPException(status_code=404, detail=f"unknown portfolio {portfolio_id}")
    return p


def _target_book(db, portfolio_id: str) -> tuple[dict, str | None, dict]:
    """{symbol: whole shares} the system says to hold, from the newest cached run.

    Sorted by `created_at` to match `/api/portfolio/{id}/last-run` exactly. Sorting by
    `computed_at` is WRONG: that field is null on some run documents, so a stale run
    wins and every gap is then computed against the wrong book — silently, and looking
    entirely healthy.

    Quantities are ROUNDED. The simulated book holds fractional shares (37.0813)
    because it divides capital by price; a real book holds 37. Comparing the raw
    fraction would report a permanent 0.08-share gap on every position.
    """
    run = db.portfolio_runs.find_one({"portfolio_id": portfolio_id},
                                     sort=[("created_at", -1)])
    if not run:
        return {}, None, {"has_run": False}
    result = run.get("result") or {}
    target = {}
    for b in result.get("current_book") or []:
        sym = b.get("symbol")
        if sym:
            target[sym] = target.get(sym, 0) + round(float(b.get("quantity") or 0))
    hist = result.get("rebalance_history") or []
    last_rebalance = max((h.get("date") for h in hist if h.get("date")), default=None)

    # A run computed against older prices cannot know about rebalances that happened
    # since, so its book is not the current target — gaps against it are meaningless.
    meta = {"has_run": True, "computed_at": run.get("created_at"),
            "data_version": run.get("data_version"),
            "stale": bool(str(run.get("end_key", "")).startswith("latest:")
                          and run.get("data_version") != _latest_data_date_safe(db))}
    return target, last_rebalance, meta


ROC_WINDOWS = {"m1": 22, "m3": 66, "y1": 252}          # sessions
# 1-year weighted heaviest, 1-month lightest — the same 0.5/1.0/1.5 shape TrueVX uses
# for its short/mid/long legs, so this reads on the familiar scale.
ROC_WEIGHTS = {"m1": 0.5, "m3": 1.0, "y1": 1.5}


def _roc_table(db, symbols: list[str]) -> dict:
    """{symbol: {m1, m3, y1, strength}} percent returns over 22/66/252 sessions.

    `strength` is the weighted mean of the three. It is deliberately dominated by the
    1-year leg, because the thing worth spotting is a name that has already run a long
    way — a 724% yearly gain SHOULD swamp a 20% month. Read it as "how extended", not
    as a forecast.
    """
    if not symbols:
        return {}
    need = max(ROC_WINDOWS.values()) + 10
    since = dt.datetime.now() - dt.timedelta(days=int(need * 1.7))
    series: dict[str, list] = {}
    for coll in [c for c in db.list_collection_names() if c.startswith("prices_")]:
        for d in db[coll].find({"symbol": {"$in": symbols}, "date": {"$gte": since}},
                               {"_id": 0, "symbol": 1, "date": 1, "close_price": 1}):
            if d.get("close_price"):
                series.setdefault(d["symbol"], []).append((d["date"], float(d["close_price"])))
    out = {}
    for sym, rows in series.items():
        rows.sort(key=lambda r: r[0])
        closes = [c for _d, c in rows]
        if len(closes) < 30:
            continue
        last = closes[-1]
        vals, wsum, acc = {}, 0.0, 0.0
        for key, n in ROC_WINDOWS.items():
            if len(closes) > n and closes[-1 - n] > 0:
                pct = (last / closes[-1 - n] - 1) * 100
                vals[key] = round(pct, 1)
                acc += pct * ROC_WEIGHTS[key]
                wsum += ROC_WEIGHTS[key]
            else:
                vals[key] = None            # not enough history — never fabricate a 0
        vals["strength"] = round(acc / wsum, 1) if wsum else None
        out[sym] = vals
    return out


def _latest_data_date_safe(db) -> str | None:
    """Newest indicator date, as the portfolio router computes data_version."""
    try:
        d = db.indicators.find_one({"indicator_type": "truevx"}, sort=[("date", -1)])
        return d["date"].strftime("%Y-%m-%d") if d else None
    except Exception:                                              # noqa: BLE001
        return None


@router.get("/api/holdings/{portfolio_id}/status")
async def holdings_status(portfolio_id: str, request: Request,
                          x_api_key: str | None = Header(default=None)):
    """Read the configured export, resolve, reconcile, and report the sync state.

    Safe to call on every page load: unchanged content is deduped by hash, so no
    snapshot is written unless the file genuinely changed.
    """
    _require_broker_auth(request, x_api_key)
    db = _db()
    p = _portfolio(db, portfolio_id)
    folder = (p.get("holdings_path") or "").strip()
    discretionary = p.get("discretionary") or {}
    target, last_rebalance, run_meta = _target_book(db, portfolio_id)

    if not folder:
        return JSONResponse(content={
            "success": True, "portfolio_id": portfolio_id,
            "sync": {"state": "NO_PATH",
                     "message": "No holdings folder configured for this portfolio."},
            "target_count": len(target)})

    # A portfolio may be funded by SEVERAL broker accounts, so the whole folder is
    # aggregated. Reading only the newest file would reconcile against part of the
    # book and report the rest as missing — confidently wrong.
    files = hs.read_folder(folder, hs.load_aliases(db))
    if not files:
        return JSONResponse(content={
            "success": True, "portfolio_id": portfolio_id,
            "sync": hs.sync_state(snapshot=None, lock=None, last_rebalance=None,
                                  gaps=None, unresolved=None,
                                  expected_fingerprint=None),
            "folder": folder, "target_count": len(target)})

    chosen, superseded, ambiguous = hs.select_current(files)
    if ambiguous:
        return JSONResponse(content={
            "success": True, "portfolio_id": portfolio_id, "folder": folder,
            "sync": {"state": "AMBIGUOUS",
                     "message": "Two or more exports carry no account identity, so "
                                "they cannot be told apart — they might be two "
                                "accounts or one account exported twice. Summing "
                                "them would double a position. Remove the stale one.",
                     "files": [os.path.basename(f.path) for f in files]},
            "target_count": len(target)})

    agg = hs.aggregate(chosen)
    doc, is_new = hs.record_aggregate(db, portfolio_id, agg)
    lock = hs.active_lock(db, portfolio_id)

    observed = agg["quantities"]
    unresolved = agg["unresolved"]
    rec = bh.reconcile(target, observed, discretionary) if not unresolved else \
        {"gaps": [], "unclassified": [], "in_sync": False}

    expected = p.get("account_fingerprints") or (
        [p["account_fingerprint"]] if p.get("account_fingerprint") else [])
    unexpected = [fp for fp in agg["fingerprints"] if expected and fp not in expected]

    sync = hs.sync_state(snapshot={**doc, "account_fingerprint": None},
                         lock=lock, last_rebalance=last_rebalance,
                         gaps=rec["gaps"], unresolved=unresolved,
                         expected_fingerprint=None)
    if run_meta.get("stale"):
        sync = {"state": "RUN_STALE",
                "message": "The portfolio's cached run was computed against older "
                           f"data ({run_meta.get('data_version')}) so its book predates "
                           "the latest rebalance. Re-run the portfolio before trusting "
                           "any gap.", **{k: v for k, v in run_meta.items() if k != "stale"}}
    if unexpected:
        sync = {"state": "WRONG_ACCOUNT",
                "message": f"{len(unexpected)} export(s) belong to a broker account "
                           "not pinned to this portfolio. Refusing to reconcile.",
                "unexpected_accounts": unexpected}

    return JSONResponse(content={
        "success": True, "portfolio_id": portfolio_id, "folder": folder,
        "sync": sync, "snapshot_id": doc["id"], "newly_ingested": is_new,
        "sources": agg["sources"], "accounts": agg["fingerprints"],
        "superseded_files": superseded,
        "file_as_of": agg["as_of"], "as_of_source": agg["as_of_source"],
        "last_rebalance": last_rebalance, "run": run_meta,
        "target": target, "observed": observed, "discretionary": discretionary,
        "roc": _roc_table(db, sorted(set(target) | set(observed) | set(discretionary))),
        "unresolved": unresolved, **rec,
        "note": "Quantities only, summed across accounts. Dated by the OLDEST export "
                "— the book is only as current as its stalest part.",
    })


@router.post("/api/holdings/automap")
async def automap(payload: dict, request: Request,
                  x_api_key: str | None = Header(default=None)):
    """Map portfolios to sub-folders of `root` by matching folder name to portfolio name.

    Folders are named for the portfolio ("Alpha-Market-probdx0.1"), so the mapping is
    derivable rather than hand-entered. Only exact name matches are wired; anything
    else is reported for a human, never guessed.
    """
    _require_broker_auth(request, x_api_key)
    db = _db()
    root = ((payload or {}).get("root") or "").strip()
    if not root or not os.path.isdir(root):
        raise HTTPException(status_code=400, detail=f"not a directory: {root}")
    by_name = {p["name"]: p["id"] for p in db.portfolios.find({}, {"id": 1, "name": 1})}
    mapped, unmatched = [], []
    for entry in sorted(os.listdir(root)):
        full = os.path.join(root, entry)
        if not os.path.isdir(full):
            continue
        pid = by_name.get(entry)
        if not pid:
            unmatched.append(entry)
            continue
        db.portfolios.update_one({"id": pid}, {"$set": {
            "holdings_path": full, "last_modified": dt.datetime.now().isoformat()}})
        mapped.append({"folder": entry, "portfolio_id": pid, "path": full})
    return JSONResponse(content={"success": True, "mapped": mapped,
                                 "unmatched_folders": unmatched,
                                 "portfolios_without_folder": sorted(
                                     n for n in by_name if n not in os.listdir(root))})


@router.get("/api/holdings/aliases")
async def list_aliases(request: Request, x_api_key: str | None = Header(default=None)):
    _require_broker_auth(request, x_api_key)
    return JSONResponse(content={"success": True, "aliases": hs.load_aliases(_db())})


@router.post("/api/holdings/aliases")
async def create_alias(payload: dict, request: Request,
                       x_api_key: str | None = Header(default=None)):
    """Confirm one broker display name -> NSE symbol.

    The model proposes ("Great Eastern Shipping Company" is GESHIP); a human confirms
    once; it is a table lookup forever after. Nothing is fuzzy-matched, because a
    wrong symbol here becomes a wrong order.
    """
    _require_broker_auth(request, x_api_key)
    db = _db()
    raw, sym = (payload or {}).get("raw_name"), (payload or {}).get("symbol")
    if not raw or not sym:
        raise HTTPException(status_code=400, detail="raw_name and symbol are required")
    sym = sym.strip().upper()
    if not db.symbol_mappings.find_one({"symbol": sym}) and \
            not db.index_meta.find_one({"Symbol": sym}):
        raise HTTPException(status_code=400,
                            detail=f"{sym} is not a known symbol — refusing to store "
                                   "an alias that would silently mis-map a holding")
    return JSONResponse(content={"success": True,
                                 "alias": hs.save_alias(db, raw, sym,
                                                        (payload or {}).get("note"))})


@router.put("/api/holdings/{portfolio_id}/config")
async def set_config(portfolio_id: str, payload: dict, request: Request,
                     x_api_key: str | None = Header(default=None)):
    """Set the holdings folder, and optionally pin the expected account.

    Pinning matters: two Dhan exports differing only by Windows' " (1)" suffix
    belonged to different account holders. Reconciling one against the other's
    portfolio would be silently wrong, so identity is proved from inside the file.
    """
    _require_broker_auth(request, x_api_key)
    db = _db()
    p_existing = _portfolio(db, portfolio_id)
    update = {}
    if "holdings_path" in (payload or {}):
        folder = (payload["holdings_path"] or "").strip()
        if folder and not os.path.isdir(folder):
            raise HTTPException(status_code=400, detail=f"not a directory: {folder}")
        update["holdings_path"] = folder
    if payload.get("pin_account"):
        folder = update.get("holdings_path") or p_existing.get("holdings_path", "")
        files = hs.read_folder(folder, hs.load_aliases(db))
        if not files:
            raise HTTPException(status_code=400, detail="no export found to pin against")
        # A portfolio can be funded by several accounts, so pin the SET present in the
        # folder. A later export from an account outside this set trips WRONG_ACCOUNT.
        update["account_fingerprints"] = sorted({f.fingerprint for f in files if f.fingerprint})
        update["account_fingerprint"] = None      # supersedes the single-account field
    if not update:
        raise HTTPException(status_code=400, detail="nothing to update")
    update["last_modified"] = dt.datetime.now().isoformat()
    db.portfolios.update_one({"id": portfolio_id}, {"$set": update})
    return JSONResponse(content={"success": True, "updated": update})


@router.put("/api/holdings/{portfolio_id}/discretionary")
async def set_discretionary(portfolio_id: str, payload: dict, request: Request,
                            x_api_key: str | None = Header(default=None)):
    """Mark quantities as the user's own, not the strategy's.

    A QUANTITY per symbol, not a symbol exclusion — so if the strategy later picks a
    name the user already holds privately, the arithmetic still works: 50 held
    privately with a target of 30 means buy 30.
    """
    _require_broker_auth(request, x_api_key)
    db = _db()
    _portfolio(db, portfolio_id)
    disc = {str(k).upper(): float(v) for k, v in ((payload or {}).get("discretionary") or {}).items()}
    db.portfolios.update_one({"id": portfolio_id},
                             {"$set": {"discretionary": disc,
                                       "last_modified": dt.datetime.now().isoformat()}})
    return JSONResponse(content={"success": True, "discretionary": disc})


@router.post("/api/holdings/{portfolio_id}/lock")
async def lock_baseline(portfolio_id: str, payload: dict, request: Request,
                        x_api_key: str | None = Header(default=None)):
    """Pin the agreed baseline after a rebalance. THE HUMAN GATE.

    Stores three vectors — what the system targeted, what the broker actually showed,
    and what the user agreed is true — plus a disposition per discrepancy
    (accepted / pending / partial / intentional). That triple is what makes the
    journal useful later: target vs observed avg cost is measured execution cost,
    and locked vs subsequent files shows what moved between rebalances.
    """
    _require_broker_auth(request, x_api_key)
    db = _db()
    p = _portfolio(db, portfolio_id)
    snapshot_id = (payload or {}).get("snapshot_id")
    snap_doc = db[hs.SNAPSHOTS].find_one({"id": snapshot_id, "portfolio_id": portfolio_id},
                                         {"_id": 0}) if snapshot_id else None
    if not snap_doc:
        raise HTTPException(status_code=400,
                            detail="snapshot_id is required and must belong to this "
                                   "portfolio — lock what was actually reviewed")
    if snap_doc.get("unresolved"):
        raise HTTPException(status_code=409,
                            detail="cannot lock while holdings are unresolved: "
                                   f"{snap_doc['unresolved']}")

    target, last_rebalance, run_meta = _target_book(db, portfolio_id)
    observed = {h["symbol"]: h["quantity"] for h in snap_doc["holdings"] if h.get("symbol")}
    locked = {str(k).upper(): float(v) for k, v in ((payload or {}).get("locked") or observed).items()}
    doc = hs.save_lock(db, portfolio_id, snap_doc["id"], target, observed, locked,
                       (payload or {}).get("dispositions") or [],
                       (payload or {}).get("rebalance_date") or last_rebalance,
                       (payload or {}).get("note"))
    return JSONResponse(content={"success": True, "lock": doc})


@router.get("/api/holdings/{portfolio_id}/journal")
async def get_journal(portfolio_id: str, request: Request, limit: int = 50,
                      x_api_key: str | None = Header(default=None)):
    """Lock history — the record of what was agreed, when, and why it differed."""
    _require_broker_auth(request, x_api_key)
    db = _db()
    _portfolio(db, portfolio_id)
    return JSONResponse(content={"success": True,
                                 "journal": hs.journal(db, portfolio_id, limit)})
