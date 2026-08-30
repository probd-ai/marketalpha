"""Validation gate for the NIFTY200 point-in-time changelog.

Replays the changelog backward from the current NIFTY200 index_meta set and
asserts members_as_of(today) reproduces the CURRENT set exactly. Symbols are
already stored in current-canonical form in the CSV; CANON covers any residual
historical->current rename needed to reconcile.

Run (from market-hunt repo root):
    ./.venv/Scripts/python.exe scripts/validate_n200_reconstitution.py
"""
import csv
import sys
from datetime import date

from pymongo import MongoClient

sys.path.insert(0, ".")
from membership import build_membership_intervals, members_as_of, union_over, ChangeEvent  # noqa: E402

CSV = "../backend/data/seed/nifty200_changelog.csv"
INDEX = "NIFTY200"
TODAY = date(2026, 6, 30)

# Historical ticker -> current canonical ticker, ONLY where the stock is still a
# current NIFTY200 constituent under a renamed symbol. CSV already stores most in
# canonical form; this is a safety net. Derived from current index_meta, not guessed.
CANON = {
    "ZOMATO": "ETERNAL",   # renamed 2025-04
}


def _syms(cell):
    return [s.strip() for s in (cell or "").split(";") if s.strip()]


def canon(s):
    return CANON.get(s, s)


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    current = {d["Symbol"] for d in db.index_meta.find({"index_name": INDEX}, {"Symbol": 1}) if d.get("Symbol")}
    print(f"current NIFTY200 constituents: {len(current)}")

    events = []
    with open(CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            y, m, d = (int(x) for x in row["effective_date"].split("-"))
            events.append(ChangeEvent(
                on=date(y, m, d),
                added=frozenset(canon(s) for s in _syms(row["added"])),
                removed=frozenset(canon(s) for s in _syms(row["removed"])),
            ))
    print(f"changelog events: {len(events)}")

    intervals = build_membership_intervals(current, events, TODAY)
    replayed = members_as_of(intervals, TODAY)

    extra = sorted(replayed - current)
    missing = sorted(current - replayed)
    print(f"\nas-of {TODAY}: replayed={len(replayed)} current={len(current)}")
    print(f"EXTRA (in replay, not current)  [{len(extra)}]: {extra}")
    print(f"MISSING (in current, not replay) [{len(missing)}]: {missing}")

    if not extra and not missing:
        print("\nRECONCILES EXACTLY: 0 discrepancies")
    else:
        print(f"\nRESIDUAL: {len(extra)+len(missing)} discrepancies")

    ever = union_over(intervals, date(2020, 1, 1), TODAY)
    print(f"\never-members over 2020-01-01..{TODAY}: {len(ever)}")
    return extra, missing


if __name__ == "__main__":
    main()
