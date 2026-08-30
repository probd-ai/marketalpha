"""Tests for point-in-time index membership reconstruction (market-hunt)."""
from datetime import date

from membership import (
    ChangeEvent,
    build_membership_intervals,
    members_as_of,
    union_over,
)


def _intervals():
    # current (as of anchor 2026-01-01) = {A, B, C}
    # events: D added 2022-01-01 then removed 2024-01-01; C added 2023-01-01
    current = {"A", "B", "C"}
    changelog = [
        ChangeEvent(on=date(2022, 1, 1), added=frozenset({"D"}), removed=frozenset()),
        ChangeEvent(on=date(2023, 1, 1), added=frozenset({"C"}), removed=frozenset()),
        ChangeEvent(on=date(2024, 1, 1), added=frozenset(), removed=frozenset({"D"})),
    ]
    return build_membership_intervals(current, changelog, anchor=date(2026, 1, 1))


def test_members_as_of_reconstructs_past_membership():
    iv = _intervals()
    # 2021: A, B present; C not yet added (2023); D not yet added (2022)
    assert members_as_of(iv, date(2021, 6, 1)) == {"A", "B"}
    # 2022-06: D added (2022-01), C still absent
    assert members_as_of(iv, date(2022, 6, 1)) == {"A", "B", "D"}
    # 2023-06: C added, D still in
    assert members_as_of(iv, date(2023, 6, 1)) == {"A", "B", "C", "D"}
    # 2025: D removed (2024-01), back to A, B, C
    assert members_as_of(iv, date(2025, 6, 1)) == {"A", "B", "C"}


def test_added_stock_absent_before_present_after_effective_date():
    iv = _intervals()
    assert "C" not in members_as_of(iv, date(2022, 12, 31))
    assert "C" in members_as_of(iv, date(2023, 1, 1))


def test_union_over_includes_ever_members():
    iv = _intervals()
    # over the full window, D (since-removed) must be in the union for data loading
    assert union_over(iv, date(2020, 1, 1), date(2026, 1, 1)) == {"A", "B", "C", "D"}
    # a window entirely before C/D existed
    assert union_over(iv, date(2020, 1, 1), date(2021, 12, 31)) == {"A", "B"}
