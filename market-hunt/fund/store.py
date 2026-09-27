"""Persistence for the trading desk (Mongo). The only module of the fund package that writes.

Collections: fund_config (one doc), fund_runs (snapshots; immutable once PUBLISHED except execution status),
fund_executions (one doc per run line), fund_journal (append-only audit log).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import numpy as np

import uuid

from .config import default_config, next_version


def plain(obj):
    """Mongo/JSON-safe copy: numpy scalars -> Python, NaN/inf -> None, dates -> ISO strings."""
    if isinstance(obj, dict):
        return {str(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [plain(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FundStore:
    def __init__(self, db):
        self.db = db
        # one published run per model version and ISO week, enforced by the database (atomic across processes)
        self.db.fund_runs.create_index("publish_key", unique=True, sparse=True)

    # -- config ------------------------------------------------------------------------------------------------
    def get_config(self) -> dict:
        doc = self.db.fund_config.find_one({"_id": "main"})
        if doc is not None and "rebalance_months" not in doc:        # saved before the setting existed
            doc = {**doc, "rebalance_months": list(range(1, 13)), "rebalance_band": None}
        if doc is None:
            doc = default_config()
            doc["updated_at"] = _now()
            self.db.fund_config.insert_one(doc)
            self.journal("CONFIG_CREATED", {"version": doc["version"]})
        return doc

    def save_config(self, new: dict) -> dict:
        old = self.get_config()
        new = {**old, **new, "_id": "main"}
        new["version"] = next_version(old, new)
        new["updated_at"] = _now()
        self.db.fund_config.replace_one({"_id": "main"}, new, upsert=True)
        self.journal("CONFIG_SAVED", {"version": new["version"], "new_model": new["version"] != old["version"]})
        return new

    # -- runs --------------------------------------------------------------------------------------------------
    def insert_run(self, snap: dict) -> None:
        snap["created_at"] = _now()
        self.db.fund_runs.insert_one(plain(snap))
        self.journal("RUN_COMPUTED", {"run_id": snap["_id"], "signal_date": snap["signal_date"],
                                      "verdict": snap["verdict"], "lines": len(snap["ticket"])})

    def get_run(self, run_id: str) -> dict | None:
        return self.db.fund_runs.find_one({"_id": run_id})

    def list_runs(self, limit: int = 50) -> list[dict]:
        proj = {"curve": 0, "books": 0, "ticket": 0, "checkpoint": 0, "residuals": 0, "orders": 0}
        return list(self.db.fund_runs.find({}, proj).sort("created_at", -1).limit(limit))

    def published_runs(self, version: int) -> list[dict]:
        return list(self.db.fund_runs.find({"version": version, "status": {"$in": ["PUBLISHED", "EXECUTING", "CLOSED"]}},
                                           {"curve": 0, "books": 0}).sort("fill_date", 1))

    def mark_published(self, run_id: str, version: int, iso_week: str, at: str) -> bool:
        """DRAFT -> PUBLISHED atomically; False if the run is no longer a draft or the week is already published."""
        from pymongo.errors import DuplicateKeyError
        try:
            r = self.db.fund_runs.update_one({"_id": run_id, "status": "DRAFT"}, {"$set": {
                "status": "PUBLISHED", "published_at": at, "publish_key": f"{version}|{iso_week}"}})
        except DuplicateKeyError:
            return False
        return r.modified_count == 1

    def set_status(self, run_id: str, status: str, **extra) -> None:
        self.db.fund_runs.update_one({"_id": run_id}, {"$set": {"status": status, **extra}})

    # -- executions --------------------------------------------------------------------------------------------
    def record_execution(self, run_id: str, rec: dict) -> None:
        key = {"run_id": run_id, "line_id": rec["line_id"]}
        self.db.fund_executions.replace_one(key, plain({**key, **rec, "recorded_at": _now()}), upsert=True)

    def executions(self, run_id: str | None = None) -> list[dict]:
        q = {"run_id": run_id} if run_id else {}
        return list(self.db.fund_executions.find(q, {"_id": 0}))

    # -- cash flows --------------------------------------------------------------------------------------------
    def add_cashflow(self, version: int, amount: float, note: str) -> dict:
        doc = {"_id": f"CF{uuid.uuid4().hex[:8]}", "version": version, "amount": amount, "note": note,
               "status": "PENDING", "created_at": _now()}
        self.db.fund_cashflows.insert_one(doc)
        return doc

    def cashflows(self, version: int | None = None) -> list[dict]:
        q = {"version": version} if version is not None else {}
        return list(self.db.fund_cashflows.find(q).sort("created_at", 1))

    def cancel_cashflow(self, flow_id: str) -> bool:
        r = self.db.fund_cashflows.update_one({"_id": flow_id, "status": "PENDING"},
                                              {"$set": {"status": "CANCELLED", "cancelled_at": _now()}})
        return r.modified_count == 1

    def apply_cashflows(self, ids: list[str], run_id: str, nav: float) -> None:
        for fid in ids:
            f = self.db.fund_cashflows.find_one({"_id": fid})
            self.db.fund_cashflows.update_one({"_id": fid, "status": "PENDING"}, {"$set": {
                "status": "APPLIED", "applied_run_id": run_id, "nav": nav, "units": f["amount"] / nav,
                "applied_at": _now()}})

    # -- journal -----------------------------------------------------------------------------------------------
    def journal(self, event: str, detail: dict) -> None:
        self.db.fund_journal.insert_one({"at": _now(), "event": event, "detail": plain(detail)})

    def read_journal(self, limit: int = 200) -> list[dict]:
        return list(self.db.fund_journal.find({}, {"_id": 0}).sort("at", -1).limit(limit))
