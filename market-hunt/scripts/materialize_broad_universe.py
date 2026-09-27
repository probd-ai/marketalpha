"""Materialize DERIVED union universes as selectable simulator indices.

NSE publishes no index for some combinations we want to trade, so they are derived
from indices it does publish:

    NIFTY500MICRO750     = NIFTY500        u NIFTYMICROCAP250   (total market, ~750)
    NIFTYSMALLMICRO500   = NIFTYSMALLCAP250 u NIFTYMICROCAP250  (small+micro only, ~500)
    NIFTYLARGEMIDCAP250  = NIFTY100        u NIFTYMIDCAP150     (large+mid, 250)

NSE does publish LargeMidcap 250, but only as a current snapshot -- we hold no
reconstitution history for it, so it could not run point-in-time. Deriving it from two
parents that DO have history makes it PIT-correct. Verified identical to the published
membership: 250 names, zero difference.

For each, two things are written:
  - index_meta:            current members = union of the parents' current members
  - index_reconstitution:  at every review date D (union of both parents' event dates),
    the TRUE delta of the combined set: added = union(D) - union(D-1),
    removed = union(D-1) - union(D). A name moving between the two parents nets to no
    change, so only genuine entries and exits to the combined set are recorded.

Each build is VALIDATED before it is stored: the derived changelog is replayed and
compared against the live union at several dates. A mismatch blocks the write, because
a wrong membership timeline silently corrupts every point-in-time backtest that uses it.

This runs automatically inside `refresh_all` (recon stage), so the derived universes
track their parents with no manual step. Run standalone to inspect:

    python scripts/materialize_broad_universe.py            # dry run, validate only
    python scripts/materialize_broad_universe.py --store    # write
    python scripts/materialize_broad_universe.py --only NIFTYSMALLMICRO500 --store
"""
import os
import sys
from datetime import date, datetime, timedelta

from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import (  # noqa: E402
    ChangeEvent, build_membership_intervals, load_membership, members_as_of,
)

# name -> parent indices whose union defines it
COMPOSITES = {
    "NIFTY500MICRO750": ("NIFTY500", "NIFTYMICROCAP250"),
    "NIFTYSMALLMICRO500": ("NIFTYSMALLCAP250", "NIFTYMICROCAP250"),
    "NIFTYLARGEMIDCAP250": ("NIFTY100", "NIFTYMIDCAP150"),
}
VALIDATE_AT = [date(2022, 6, 1), date(2023, 6, 1), date(2024, 6, 1),
               date(2025, 6, 1), date(2026, 3, 1)]

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")


def build(db, name: str, parents: tuple[str, ...], store: bool, log=print) -> bool:
    """Derive, validate and optionally store one composite. Returns True if usable."""
    try:
        ivs = [load_membership(db, p, anchor=date.today()) for p in parents]
    except ValueError as e:
        log(f"  {name}: SKIP — {e}")
        return False

    def union_on(d):
        out = set()
        for iv in ivs:
            out |= members_as_of(iv, d)
        return out

    dates = sorted({datetime.strptime(str(e["effective_date"])[:10], "%Y-%m-%d").date()
                    for e in db.index_reconstitution.find(
                        {"index_name": {"$in": list(parents)}}, {"effective_date": 1})})
    if not dates:
        log(f"  {name}: SKIP — parents have no reconstitution events")
        return False

    events = []
    for d in dates:
        after, before = union_on(d), union_on(d - timedelta(days=1))
        added, removed = sorted(after - before), sorted(before - after)
        if added or removed:
            events.append((d, added, removed))

    current = union_on(date.today())
    # Replay our derived changelog and compare against the live union. A mismatch means
    # the timeline is wrong, and a wrong timeline corrupts every PIT backtest silently.
    ivc = build_membership_intervals(
        current, [ChangeEvent(on=d, added=frozenset(a), removed=frozenset(r))
                  for d, a, r in events], anchor=date.today())
    mismatches = []
    for td in VALIDATE_AT:
        if td < dates[0]:
            continue                       # before the first event: nothing to check
        diff = len(members_as_of(ivc, td) ^ union_on(td))
        if diff:
            mismatches.append((td, diff))

    log(f"  {name}: {len(current)} members, {len(events)} events "
        f"({dates[0]}..{dates[-1]}) — "
        + ("replay EXACT" if not mismatches else f"MISMATCH {mismatches}"))
    if mismatches:
        log(f"  {name}: NOT STORED — derived timeline disagrees with the live union")
        return False

    if store:
        db.index_meta.delete_many({"index_name": name})
        db.index_meta.insert_many([{"index_name": name, "Symbol": s} for s in sorted(current)])
        db.index_reconstitution.delete_many({"index_name": name})
        for d, added, removed in events:
            db.index_reconstitution.insert_one({
                "_id": f"{name}_{d.isoformat()}", "index_name": name,
                "effective_date": datetime(d.year, d.month, d.day),
                "added": added, "removed": removed,
                "source": f"derived_union_{'_'.join(p.lower() for p in parents)}",
                "verified": True, "loaded_at": datetime.now()})
        log(f"  {name}: stored")
    return True


def materialize_all(db=None, store: bool = False, only: str | None = None, log=print) -> dict:
    """Rebuild every composite. Called by refresh_all so they track parents unattended."""
    # pymongo Database has no __bool__ — must compare to None explicitly.
    db = db if db is not None else MongoClient(MONGO_URI)["market_hunt"]
    out = {}
    for name, parents in COMPOSITES.items():
        if only and name != only:
            continue
        out[name] = build(db, name, parents, store, log)
    return out


if __name__ == "__main__":
    store = "--store" in sys.argv
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    print(f"derived universes ({'STORE' if store else 'dry run'}):")
    res = materialize_all(store=store, only=only)
    if not store:
        print("\n[dry run — re-run with --store to write]")
    raise SystemExit(0 if all(res.values()) else 1)
