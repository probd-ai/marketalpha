"""Materialize the combined N500 ∪ Microcap250 universe as a selectable simulator index.

Creates index_name 'NIFTY500MICRO750':
  - index_meta: current = union of current NIFTY500 + NIFTYMICROCAP250 members.
  - index_reconstitution: at each review date D (union of both indices' events), the TRUE delta
    of the combined membership: added = union(D) - union(D-1day), removed = union(D-1) - union(D).
    (A name moving N500<->Micro nets to no change; only entries/exits to the top-750 set count.)
Then validates members_as_of(combined, D) == union of the two at several 2022-2026 dates.
Run with --store to write; default validates a dry build.
"""
import sys
from datetime import date, datetime, timedelta
from pymongo import MongoClient

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
from membership import load_membership, members_as_of, build_membership_intervals, ChangeEvent  # noqa: E402

STORE = "--store" in sys.argv
NAME = "NIFTY500MICRO750"
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]

iv500 = load_membership(db, "NIFTY500", anchor=date.today())
ivmic = load_membership(db, "NIFTYMICROCAP250", anchor=date.today())


def union_on(d):
    return members_as_of(iv500, d) | members_as_of(ivmic, d)


# review dates = union of both indices' effective dates
dates = sorted({datetime.strptime(str(e["effective_date"])[:10], "%Y-%m-%d").date()
                for e in db.index_reconstitution.find(
                    {"index_name": {"$in": ["NIFTY500", "NIFTYMICROCAP250"]}}, {"effective_date": 1})})
events = []
for d in dates:
    after = union_on(d)
    before = union_on(d - timedelta(days=1))
    added = sorted(after - before)
    removed = sorted(before - after)
    if added or removed:
        events.append((d, added, removed))

current = union_on(date.today())
print(f"combined '{NAME}': current={len(current)}  review-events={len(events)} ({dates[0]}..{dates[-1]})")

# validate: replay our changelog and compare to the live union at test dates
changelog = [ChangeEvent(on=d, added=frozenset(a), removed=frozenset(r)) for d, a, r in events]
ivc = build_membership_intervals(current, changelog, anchor=date.today())
print("\nvalidation (replay vs live union):")
ok = True
for td in [date(2022, 6, 1), date(2023, 6, 1), date(2024, 6, 1), date(2025, 6, 1), date(2026, 3, 1)]:
    repl = members_as_of(ivc, td)
    live = union_on(td)
    diff = len(repl ^ live)
    ok &= (diff == 0)
    print(f"  {td}: replay={len(repl)} live={len(live)} mismatch={diff}")
print("REPLAY EXACT" if ok else "REPLAY HAS MISMATCHES (investigate before trusting)")

if STORE and ok:
    # current snapshot in index_meta
    db.index_meta.delete_many({"index_name": NAME})
    db.index_meta.insert_many([{"index_name": NAME, "Symbol": s} for s in sorted(current)])
    # reconstitution events
    db.index_reconstitution.delete_many({"index_name": NAME})
    for d, added, removed in events:
        db.index_reconstitution.insert_one({
            "_id": f"{NAME}_{d.isoformat()}", "index_name": NAME,
            "effective_date": datetime(d.year, d.month, d.day),
            "added": added, "removed": removed,
            "source": "derived_union_n500_microcap250", "verified": True,
            "loaded_at": datetime(2026, 6, 21)})
    print(f"\nSTORED index_meta ({len(current)} members) + {len(events)} reconstitution events for {NAME}.")
elif STORE:
    print("\nNOT STORED — replay mismatched; fix before storing.")
else:
    print("\n[dry-run — re-run with --store to materialize]")
