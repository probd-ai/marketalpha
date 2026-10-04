"""AI Broadcast: papers the AI publishes to the desk (spec docs/specs/2026-10-04-ai-broadcast.md).

A paper is structured text — title, summary, sections with a body, a note and/or a table. Nothing is interpreted as
HTML or Markdown; the desk renders it with its own components. Validation is strict because the content is stored
and shown later. Collection `fund_papers`.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

KINDS = ("trade-plan", "research", "note")
TONES = ("info", "warn")
ALIGNS = ("left", "right")
PAPER_KEYS = {"title", "kind", "summary", "sections", "run_id"}
SECTION_KEYS = {"heading", "body", "note", "table"}
TABLE_KEYS = {"columns", "rows", "align", "total"}
LIST_FIELDS = {"sections": 0}


class PaperRefused(Exception):
    pass


def _text(v, lo: int, hi: int) -> bool:
    return isinstance(v, str) and lo <= len(v.strip() if lo else v) and len(v) <= hi


def _cell(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return math.isfinite(v)
    return v is None or (isinstance(v, str) and len(v) <= 200)


def _table_errors(tag: str, t) -> list[str]:
    if not isinstance(t, dict):
        return [f"{tag}: table must be an object"]
    errs = [f"{tag}: table has unknown field(s) {sorted(set(t) - TABLE_KEYS)}"] if set(t) - TABLE_KEYS else []
    cols = t.get("columns")
    if not isinstance(cols, list) or not 1 <= len(cols) <= 12 or not all(_text(c, 0, 60) for c in cols):
        return errs + [f"{tag}: table columns must be 1-12 short labels"]
    rows = t.get("rows")
    if not isinstance(rows, list) or len(rows) > 300:
        return errs + [f"{tag}: table rows must be a list of at most 300"]
    for i, r in enumerate(rows):
        if not isinstance(r, list) or len(r) != len(cols):
            errs.append(f"{tag}: row {i + 1} must have {len(cols)} cells")
        elif not all(_cell(c) for c in r):
            errs.append(f"{tag}: row {i + 1} has a cell that is not short text or a number")
    if "align" in t and (not isinstance(t["align"], list) or len(t["align"]) != len(cols)
                         or not all(a in ALIGNS for a in t["align"])):
        errs.append(f"{tag}: align must give left/right for each column")
    if "total" in t and (not isinstance(t["total"], list) or len(t["total"]) != len(cols)
                         or not all(_cell(c) for c in t["total"])):
        errs.append(f"{tag}: total must be one row the width of the columns")
    return errs


def validate(p) -> list[str]:
    if not isinstance(p, dict):
        return ["paper must be an object"]
    errs = [f"unknown field(s) {sorted(set(p) - PAPER_KEYS)}"] if set(p) - PAPER_KEYS else []
    if not _text(p.get("title"), 1, 120):
        errs.append("title must be 1-120 characters")
    if p.get("kind") not in KINDS:
        errs.append(f"kind must be one of {list(KINDS)}")
    if "summary" in p and not _text(p["summary"], 0, 1000):
        errs.append("summary must be at most 1000 characters")
    if "run_id" in p and not _text(p["run_id"], 1, 40):
        errs.append("run_id must be a run id")
    secs = p.get("sections")
    if not isinstance(secs, list) or not 1 <= len(secs) <= 30:
        return errs + ["sections must be a list of 1-30 sections"]
    for i, s in enumerate(secs, 1):
        tag = f"section {i}"
        if not isinstance(s, dict):
            errs.append(f"{tag}: must be an object")
            continue
        if set(s) - SECTION_KEYS:
            errs.append(f"{tag}: unknown field(s) {sorted(set(s) - SECTION_KEYS)}")
        if not _text(s.get("heading"), 1, 120):
            errs.append(f"{tag}: heading must be 1-120 characters")
        if "body" in s and not _text(s["body"], 0, 8000):
            errs.append(f"{tag}: body must be at most 8000 characters")
        if "note" in s:
            n = s["note"]
            if not isinstance(n, dict) or n.get("tone") not in TONES or not _text(n.get("text"), 1, 1000) \
                    or set(n) - {"tone", "text"}:
                errs.append(f"{tag}: note needs a tone (info/warn) and 1-1000 characters of text")
        if "table" in s:
            errs += _table_errors(tag, s["table"])
    return errs


def publish(store, p: dict) -> dict:
    errs = validate(p)
    if errs:
        raise PaperRefused("; ".join(errs[:5]))
    if p.get("run_id") and not store.get_run(p["run_id"]):
        raise PaperRefused(f"run {p['run_id']} not found")
    now = datetime.now(timezone.utc)
    doc = {"_id": f"P{now:%Y%m%d}-{uuid.uuid4().hex[:6]}", **p, "author": "Claude", "status": "PUBLISHED",
           "created_at": now.isoformat(timespec="seconds")}
    store.db.fund_papers.insert_one(doc)
    store.journal("PAPER_PUBLISHED", {"paper_id": doc["_id"], "title": doc["title"], "kind": doc["kind"]})
    return doc


def list_papers(store) -> list[dict]:
    """Newest first, without sections."""
    return [{k: v for k, v in d.items() if k != "sections"}
            for d in store.db.fund_papers.find({}, LIST_FIELDS).sort("created_at", -1)]


def get_paper(store, paper_id: str) -> dict | None:
    return store.db.fund_papers.find_one({"_id": paper_id})


def archive(store, paper_id: str) -> None:
    r = store.db.fund_papers.update_one({"_id": paper_id, "status": "PUBLISHED"}, {"$set": {
        "status": "ARCHIVED", "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}})
    if r.modified_count != 1:
        raise PaperRefused("paper not found or already archived")
    store.journal("PAPER_ARCHIVED", {"paper_id": paper_id})
