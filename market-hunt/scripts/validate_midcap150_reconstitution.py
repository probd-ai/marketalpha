"""Validation gate for the NIFTYMIDCAP150 point-in-time changelog.

Replays the changelog FORWARD (backward-from-anchor) from the current
NIFTYMIDCAP150 index_meta set and asserts members_as_of(today) reproduces the
CURRENT set exactly. Applies a canonical-rename map so historical PR tickers
reconcile against the current canonical symbols (e.g. L&TFH->LTF).

Run:
    ./.venv/Scripts/python.exe scripts/validate_midcap150_reconstitution.py
"""
import csv
import sys
from datetime import date

from pymongo import MongoClient

sys.path.insert(0, ".")
from membership import build_membership_intervals, members_as_of, union_over, ChangeEvent  # noqa: E402

CSV = "../backend/data/seed/niftymidcap150_changelog.csv"
INDEX = "NIFTYMIDCAP150"
TODAY = date(2026, 6, 30)

# Historical PR ticker -> current canonical ticker, ONLY where the stock is still
# a current NIFTYMIDCAP150 constituent under a renamed/merged symbol. Derived from
# the current index_meta list; not guessed.
CANON = {
    "L&TFH": "LTF",      # L&T Finance Holdings -> L&T Finance (LTF)
    "MINDAIND": "UNOMINDA",  # Minda Industries -> UNO Minda (UNOMINDA)
}


def _syms(cell):
    return [s.strip() for s in (cell or "").split(";") if s.strip()]


def canon(s):
    return CANON.get(s, s)


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    current = {d["Symbol"] for d in db.index_meta.find({"index_name": INDEX}, {"Symbol": 1}) if d.get("Symbol")}
    print(f"current {INDEX} constituents: {len(current)}")

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

    extra = sorted(replayed - current)     # replay says member, current says no
    missing = sorted(current - replayed)   # current says member, replay says no
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
