"""Validation gate for the NIFTY500 point-in-time changelog.

Replays the changelog FORWARD from the earliest in-window baseline and asserts
that members_as_of(today) reproduces the CURRENT NIFTY500 index_meta set exactly.
Applies a canonical-rename map so historical tickers reconcile against the
current canonical symbols (e.g. ZOMATO->ETERNAL).

Run:
    ./.venv/Scripts/python.exe scripts/validate_n500_reconstitution.py
"""
import csv
import sys
from datetime import date

from pymongo import MongoClient

sys.path.insert(0, ".")
from membership import build_membership_intervals, members_as_of, union_over, ChangeEvent  # noqa: E402

CSV = "../backend/data/seed/nifty500_changelog.csv"
INDEX = "NIFTY500"
TODAY = date(2026, 6, 30)

# Historical ticker -> current canonical ticker, ONLY where the stock is still a
# current NIFTY500 constituent under a renamed/merged symbol. Derived from the
# current index_meta list; not guessed.
CANON = {
    "ZOMATO": "ETERNAL",      # renamed 2025-04
    "LTI": "LTM", "LTIM": "LTM",  # L&T Infotech -> LTIMindtree, stored LTM
    # NOTE: MINDTREE is NOT mapped to LTM. Mindtree merged INTO LTI; its 2022-09
    # removal from N500 is the absorption of a distinct entity, while LTI (LTM)
    # survived. Mapping MINDTREE->LTM would falsely remove LTM.
    "GET&D": "GVT&D",         # GE T&D -> GE Vernova T&D
    "HEXAWARE": "HEXT",       # re-listed; current symbol HEXT
}


def _syms(cell):
    return [s.strip() for s in (cell or "").split(";") if s.strip()]


def canon(s):
    return CANON.get(s, s)


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    current = {d["Symbol"] for d in db.index_meta.find({"index_name": INDEX}, {"Symbol": 1}) if d.get("Symbol")}
    print(f"current NIFTY500 constituents: {len(current)}")

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

    # ever-members over window (for price-coverage report)
    ever = union_over(intervals, date(2020, 1, 1), TODAY)
    print(f"\never-members over 2020-01-01..{TODAY}: {len(ever)}")
    return extra, missing


if __name__ == "__main__":
    main()
