"""Point-in-time index membership reconstruction for market-hunt.

Given a current constituent list plus the dated add/remove events stored in
`market_hunt.index_reconstitution`, reconstruct which symbols were in an index
on any past date. This is what makes a simulation survivorship-bias free.

Ported from the proven tvagent implementation
(backend/app/data/universe/membership.py) and extended with a Mongo loader.
"""
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class ChangeEvent:
    on: date
    added: frozenset[str]
    removed: frozenset[str]


@dataclass(frozen=True)
class MembershipInterval:
    symbol: str
    effective_from: date | None
    effective_to: date | None


def build_membership_intervals(
    current_members: set[str],
    changelog: list[ChangeEvent],
    anchor: date,
) -> list[MembershipInterval]:
    """Replay the changelog backward from `anchor` to build membership intervals.

    At `anchor`, members == current_members (each open-ended). Walking events
    newest->oldest: a stock 'added on D' had its open interval begin at D (absent
    before D); a stock 'removed on D' had an interval ending at D (present before).
    """
    current = set(current_members)
    open_to: dict[str, date | None] = {s: None for s in current}
    intervals: list[MembershipInterval] = []

    for ev in sorted(changelog, key=lambda e: e.on, reverse=True):
        for s in ev.added:  # present after D, absent before D
            if s in current:
                intervals.append(MembershipInterval(s, ev.on, open_to.get(s)))
                current.discard(s)
                open_to.pop(s, None)
        for s in ev.removed:  # absent after D, present before D
            open_to[s] = ev.on
            current.add(s)

    for s in current:  # members extending back to the start of history
        intervals.append(MembershipInterval(s, None, open_to.get(s)))

    return intervals


def _covers(iv: MembershipInterval, d: date) -> bool:
    if iv.effective_from is not None and iv.effective_from > d:
        return False
    if iv.effective_to is not None and d >= iv.effective_to:
        return False
    return True


def _overlaps(iv: MembershipInterval, start: date, end: date) -> bool:
    if iv.effective_to is not None and iv.effective_to <= start:
        return False
    if iv.effective_from is not None and iv.effective_from > end:
        return False
    return True


def members_as_of(intervals: list[MembershipInterval], on: date) -> set[str]:
    """The set of symbols that were index members on date `on`."""
    return {iv.symbol for iv in intervals if _covers(iv, on)}


def union_over(intervals: list[MembershipInterval], start: date, end: date) -> set[str]:
    """Every symbol that was a member at any point in [start, end] (ever-members)."""
    return {iv.symbol for iv in intervals if _overlaps(iv, start, end)}


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def load_membership(db, index_name: str, anchor: date) -> list[MembershipInterval]:
    """Build point-in-time intervals for `index_name` from Mongo.

    Current members come from `index_meta` (the current snapshot); dated add/remove
    events come from `index_reconstitution`. Raises ValueError if no reconstitution
    events exist for the index — callers must not silently fall back to the biased
    (current-list) universe.
    """
    current = {
        doc["Symbol"]
        for doc in db.index_meta.find({"index_name": index_name}, {"Symbol": 1})
        if doc.get("Symbol")
    }
    if not current:
        raise ValueError(f"no current constituents in index_meta for {index_name!r}")

    events = list(
        db.index_reconstitution.find({"index_name": index_name}).sort("effective_date", 1)
    )
    if not events:
        raise ValueError(
            f"no reconstitution events in index_reconstitution for {index_name!r}; "
            "load them via capture_index_rebalance before running point-in-time."
        )

    changelog = [
        ChangeEvent(
            on=_as_date(ev["effective_date"]),
            added=frozenset(ev.get("added") or []),
            removed=frozenset(ev.get("removed") or []),
        )
        for ev in events
    ]
    return build_membership_intervals(current, changelog, anchor)
