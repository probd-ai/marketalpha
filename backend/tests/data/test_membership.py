from datetime import date
from app.data.universe.membership import (
    ChangeEvent, MembershipInterval, build_membership_intervals,
)


def _interval(intervals, symbol):
    return [i for i in intervals if i.symbol == symbol]


def test_survivor_has_open_interval():
    anchor = date(2026, 1, 1)
    out = build_membership_intervals({"AAA", "BBB"}, [], anchor)
    aaa = _interval(out, "AAA")
    assert len(aaa) == 1
    assert aaa[0].effective_from is None and aaa[0].effective_to is None


def test_added_stock_starts_at_event_date():
    anchor = date(2026, 1, 1)
    log = [ChangeEvent(date(2022, 3, 31), frozenset({"AAA"}), frozenset({"OLD"}))]
    out = build_membership_intervals({"AAA", "BBB"}, log, anchor)
    aaa = _interval(out, "AAA")[0]
    assert aaa.effective_from == date(2022, 3, 31) and aaa.effective_to is None


def test_removed_stock_ends_at_event_date():
    anchor = date(2026, 1, 1)
    log = [ChangeEvent(date(2022, 3, 31), frozenset({"AAA"}), frozenset({"OLD"}))]
    out = build_membership_intervals({"AAA", "BBB"}, log, anchor)
    old = _interval(out, "OLD")[0]
    assert old.effective_from is None and old.effective_to == date(2022, 3, 31)
