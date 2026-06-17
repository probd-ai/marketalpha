from dataclasses import dataclass
from datetime import date


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
    """Replay changelog backward from anchor to build membership intervals.

    At anchor, members == current_members (each with an open-ended interval).
    Walking events newest->oldest: an 'added on D' stock had its open interval
    begin at D (and was absent before D); a 'removed on D' stock had a new
    interval ending at D (open backward from D).
    """
    current = set(current_members)
    open_to: dict[str, date | None] = {s: None for s in current}

    intervals: list[MembershipInterval] = []

    for ev in sorted(changelog, key=lambda e: e.on, reverse=True):
        for s in ev.added:                    # present after D, absent before D
            if s in current:
                intervals.append(MembershipInterval(s, ev.on, open_to.get(s)))
                current.discard(s)
                open_to.pop(s, None)
        for s in ev.removed:                  # absent after D, present before D
            open_to[s] = ev.on
            current.add(s)

    for s in current:                         # members extending to start of history
        intervals.append(MembershipInterval(s, None, open_to.get(s)))

    return intervals
