"""Persistence for the AI bookkeeper: holdings snapshots, aliases, locks, journal.

Division of labour (deliberate):
  * the MODEL resolves company names, interprets intent and writes the narrative
  * this module does arithmetic-free storage and exact state transitions
  * the USER presses lock -- it is the human gate that will eventually authorise orders

A lock records three vectors, which is what makes the journal worth keeping:
  target   -- what the system said to hold at that rebalance
  observed -- what the broker file actually showed
  locked   -- what the user agreed is true going forward
`target` vs `observed` avg cost is measured execution cost; `locked` vs later files
shows what moved between rebalances.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import io
import os
from dataclasses import dataclass

import broker_holdings as bh

ALIASES = "broker_symbol_aliases"
SNAPSHOTS = "holdings_snapshots"
LOCKS = "holdings_locks"

# Beyond this a Kite file (which states no download time) is treated as unverifiable
# rather than trusted, since only its mtime is available.
MTIME_TRUST_HOURS = 36


# ------------------------------------------------------------------ file discovery
@dataclass(frozen=True)
class FileSnap:
    """One parsed export, reduced to what selection and aggregation need."""
    path: str
    mtime: dt.datetime
    fingerprint: str | None
    quantities: dict[str, float]
    file_as_of: dt.datetime | None
    content_hash: str
    broker: str
    unresolved: list[str]


def newest_export(folder: str) -> tuple[str, dt.datetime] | None:
    """Newest *.csv in `folder`, with its mtime. Used for account pinning only.

    Reconciliation aggregates the WHOLE folder (see `read_folder`) — one portfolio
    can be funded by several broker accounts, and picking one file would silently
    reconcile against part of the book.
    """
    if not folder or not os.path.isdir(folder):
        return None
    files = [p for p in glob.glob(os.path.join(folder, "*.csv")) if os.path.isfile(p)]
    if not files:
        return None
    newest = max(files, key=os.path.getmtime)
    return newest, dt.datetime.fromtimestamp(os.path.getmtime(newest))


def read_folder(folder: str, aliases: dict[str, str]) -> list[FileSnap]:
    """Parse every *.csv in `folder`. Unreadable files are skipped, not fatal."""
    out: list[FileSnap] = []
    if not folder or not os.path.isdir(folder):
        return out
    for path in sorted(glob.glob(os.path.join(folder, "*.csv"))):
        if not os.path.isfile(path):
            continue
        try:
            snap, _ = read_export(path)
        except ValueError:
            continue
        snap = bh.resolve(snap, aliases)
        out.append(FileSnap(
            path=path, mtime=dt.datetime.fromtimestamp(os.path.getmtime(path)),
            fingerprint=snap.account_fingerprint, quantities=snap.quantities(),
            file_as_of=snap.file_as_of, content_hash=snap.content_hash,
            broker=snap.broker, unresolved=snap.unresolved()))
    return out


def select_current(snaps: list[FileSnap]) -> tuple[list[FileSnap], list[str], bool]:
    """One current export per account. Returns (chosen, dropped_filenames, ambiguous).

    Same account twice (a new export dropped in beside the old) collapses to the
    newest — summing them would double the position. Files with no account identity
    are the hard case: Kite states none, so two different ones might be two accounts
    or one account exported twice, and guessing wrong doubles a quantity. Identical
    content dedups safely; anything else is `ambiguous` and must block.
    """
    by_account: dict[str, list[FileSnap]] = {}
    unknown: list[FileSnap] = []
    for s in snaps:
        if s.fingerprint:
            by_account.setdefault(s.fingerprint, []).append(s)
        else:
            unknown.append(s)

    chosen, dropped = [], []
    for _fp, group in by_account.items():
        group = sorted(group, key=lambda s: s.mtime, reverse=True)
        chosen.append(group[0])
        dropped += [os.path.basename(s.path) for s in group[1:]]

    distinct_unknown = {s.content_hash: s for s in unknown}
    if len(distinct_unknown) > 1:
        return [], dropped, True                       # cannot be told apart — block
    if distinct_unknown:
        keep = max(distinct_unknown.values(), key=lambda s: s.mtime)
        chosen.append(keep)
        dropped += [os.path.basename(s.path) for s in unknown if s is not keep]
    return chosen, dropped, False


def aggregate(chosen: list[FileSnap]) -> dict:
    """Sum quantities across accounts; date the book by its STALEST file."""
    quantities: dict[str, float] = {}
    for s in chosen:
        for sym, qty in s.quantities.items():
            quantities[sym] = quantities.get(sym, 0.0) + qty

    stamps = [(s.file_as_of or s.mtime) for s in chosen]
    all_broker = bool(chosen) and all(s.file_as_of for s in chosen)
    return {
        "quantities": quantities,
        "fingerprints": sorted({s.fingerprint for s in chosen if s.fingerprint}),
        "as_of": min(stamps).isoformat() if stamps else None,
        "as_of_source": "broker" if all_broker else "file_mtime",
        "unresolved": sorted({n for s in chosen for n in s.unresolved}),
        # The hash must cover RESOLUTION, not just the file bytes. Confirming an alias
        # changes what the same file means (unresolved -> a real symbol) without
        # touching its content; hashing bytes alone left a stale snapshot claiming 0
        # holdings and 27 unresolved names, which then blocked the lock.
        "content_hash": hashlib.sha256(
            ("|".join(sorted(s.content_hash for s in chosen)) + "::" +
             "|".join(f"{k}={v}" for k, v in sorted(quantities.items())) + "::" +
             "|".join(sorted({n for s in chosen for n in s.unresolved}))
             ).encode()).hexdigest()[:16],
        "sources": [{"file": os.path.basename(s.path), "broker": s.broker,
                     "account": s.fingerprint, "positions": len(s.quantities),
                     "as_of": (s.file_as_of or s.mtime).isoformat()} for s in chosen],
    }


def read_export(path: str) -> tuple[bh.BrokerSnapshot, str]:
    text = io.open(path, encoding="utf-8-sig", errors="replace").read()
    return bh.parse(text), text


# ------------------------------------------------------------------ aliases
def load_aliases(db) -> dict[str, str]:
    """{raw_name: symbol} for every confirmed alias."""
    return {d["raw_name"]: d["symbol"] for d in db[ALIASES].find({}, {"_id": 0})}


def save_alias(db, raw_name: str, symbol: str, note: str | None = None) -> dict:
    """Confirm one broker display name -> NSE symbol. Idempotent by normalised name."""
    doc = {
        "_id": bh.normalize_name(raw_name),
        "raw_name": raw_name,
        "symbol": symbol.strip().upper(),
        "note": note,
        "confirmed_at": dt.datetime.now().isoformat(),
    }
    db[ALIASES].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    return {k: v for k, v in doc.items() if k != "_id"}


# ------------------------------------------------------------------ snapshots
def record_aggregate(db, portfolio_id: str, agg: dict) -> tuple[dict, bool]:
    """Persist an aggregated folder read, or reuse the last one if nothing changed.

    Returns (snapshot, is_new). Hash dedup over the combined content is what lets the
    portfolio page re-read on every load while the log records only real changes.
    """
    prev = latest_snapshot(db, portfolio_id)
    if prev and prev.get("content_hash") == agg["content_hash"]:
        return prev, False
    doc = {
        "id": f"snap_{int(dt.datetime.now().timestamp() * 1000)}",
        "portfolio_id": portfolio_id,
        "content_hash": agg["content_hash"],
        "file_as_of": agg["as_of"],
        "as_of_source": agg["as_of_source"],
        "accounts": agg["fingerprints"],
        "sources": agg["sources"],
        "holdings": [{"symbol": s, "quantity": q} for s, q in agg["quantities"].items()],
        "unresolved": agg["unresolved"],
        "ingested_at": dt.datetime.now().isoformat(),
    }
    db[SNAPSHOTS].insert_one(dict(doc))
    return doc, True


def latest_snapshot(db, portfolio_id: str) -> dict | None:
    return db[SNAPSHOTS].find_one({"portfolio_id": portfolio_id},
                                  {"_id": 0}, sort=[("ingested_at", -1)])


def record_snapshot(db, portfolio_id: str, snap: bh.BrokerSnapshot,
                    source_file: str, file_mtime: dt.datetime) -> tuple[dict, bool]:
    """Persist a snapshot, or reuse the last one when the content is unchanged.

    Returns (snapshot, is_new). Content-hash dedup is what lets the portfolio page
    re-read the file on every load while the log still records only real changes.
    """
    prev = latest_snapshot(db, portfolio_id)
    if prev and prev.get("content_hash") == snap.content_hash:
        return prev, False
    doc = {
        "id": f"snap_{int(dt.datetime.now().timestamp() * 1000)}",
        "portfolio_id": portfolio_id,
        "broker": snap.broker,
        "content_hash": snap.content_hash,
        "file_as_of": snap.file_as_of.isoformat() if snap.file_as_of else None,
        "file_mtime": file_mtime.isoformat(),
        "as_of_source": "broker" if snap.file_as_of else "file_mtime",
        "account_fingerprint": snap.account_fingerprint,
        "source_file": os.path.basename(source_file),
        "holdings": [{"raw_name": h.raw_name, "symbol": h.symbol,
                      "quantity": h.quantity, "avg_cost": h.avg_cost, "ltp": h.ltp}
                     for h in snap.holdings],
        "unresolved": snap.unresolved(),
        "ingested_at": dt.datetime.now().isoformat(),
    }
    db[SNAPSHOTS].insert_one(dict(doc))
    return doc, True


# ------------------------------------------------------------------ locks / journal
def active_lock(db, portfolio_id: str) -> dict | None:
    return db[LOCKS].find_one({"portfolio_id": portfolio_id}, {"_id": 0},
                              sort=[("locked_at", -1)])


def save_lock(db, portfolio_id: str, snapshot_id: str, target: dict,
              observed: dict, locked: dict, dispositions: list[dict],
              rebalance_date: str | None, note: str | None) -> dict:
    """Pin the agreed post-rebalance baseline.

    `dispositions` carries the user's decision per discrepancy -- accepted /
    pending / partial / intentional -- which is what turns this from a snapshot
    into a journal of execution discipline.
    """
    doc = {
        "id": f"lock_{int(dt.datetime.now().timestamp() * 1000)}",
        "portfolio_id": portfolio_id,
        "snapshot_id": snapshot_id,
        "rebalance_date": rebalance_date,
        "target": target,
        "observed": observed,
        "locked": locked,
        "dispositions": dispositions or [],
        "note": note,
        "locked_at": dt.datetime.now().isoformat(),
    }
    db[LOCKS].insert_one(dict(doc))
    return doc


def journal(db, portfolio_id: str, limit: int = 50) -> list[dict]:
    return list(db[LOCKS].find({"portfolio_id": portfolio_id}, {"_id": 0})
                .sort("locked_at", -1).limit(limit))


# ------------------------------------------------------------------ state
def sync_state(*, snapshot: dict | None, lock: dict | None,
               last_rebalance: str | None, gaps: list | None,
               unresolved: list | None, expected_fingerprint: str | None) -> dict:
    """The single source of truth for what the UI shows.

    Ordered by severity, because a later state must never mask an earlier one -- the
    dangerous failure is reporting "in sync" from a file that predates the rebalance,
    where the comparison is meaningless rather than reassuring.
    """
    if snapshot is None:
        return {"state": "NO_FILE",
                "message": "No holdings export found in the configured folder."}
    if expected_fingerprint and snapshot.get("account_fingerprint") \
            and snapshot["account_fingerprint"] != expected_fingerprint:
        return {"state": "WRONG_ACCOUNT",
                "message": "This file belongs to a different broker account than the "
                           "one mapped to this portfolio. Refusing to reconcile."}
    if unresolved:
        return {"state": "UNRESOLVED",
                "message": f"{len(unresolved)} holding name(s) could not be resolved "
                           "to a symbol. Confirm them before reconciling.",
                "unresolved": unresolved}

    as_of = snapshot.get("file_as_of") or snapshot.get("file_mtime")
    if last_rebalance and as_of and as_of[:10] < last_rebalance[:10]:
        return {"state": "REBALANCE_DUE",
                "message": f"Holdings are from {as_of[:10]}, before the "
                           f"{last_rebalance[:10]} rebalance. Download a fresh export "
                           "— gaps cannot be judged from a stale file.",
                "file_as_of": as_of, "last_rebalance": last_rebalance}
    if lock is None:
        return {"state": "UNLOCKED",
                "message": "No baseline locked yet. Review the comparison and lock it "
                           "to record what this book should be."}
    if gaps:
        return {"state": "LOCKED_DRIFT",
                "message": f"{len(gaps)} position(s) differ from the locked baseline.",
                "locked_at": lock.get("locked_at")}
    return {"state": "LOCKED_CLEAN",
            "message": "Live holdings match the locked baseline.",
            "locked_at": lock.get("locked_at")}
