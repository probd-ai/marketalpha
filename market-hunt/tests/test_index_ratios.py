"""Tests for the index band-ratio analytics.

The arithmetic is trivial; the things worth pinning are the edge cases that would
silently produce a wrong chart: a zero denominator, a score of exactly 100, and the
epoch split that makes the query point-in-time correct.
"""
from datetime import date

import pytest

from analytics_ratios import (
    BANDS,
    RATIO_PAIRS,
    Epoch,
    assemble,
    band_labels,
    band_switch,
    compute_ratios,
    membership_epochs,
    ratio_keys,
    resolve_window,
)


class _Iv:
    """Stand-in for membership.MembershipInterval."""

    def __init__(self, symbol, eff_from, eff_to):
        self.symbol, self.effective_from, self.effective_to = symbol, eff_from, eff_to


def _members_as_of(intervals, on):
    return {iv.symbol for iv in intervals
            if (iv.effective_from is None or iv.effective_from <= on)
            and (iv.effective_to is None or on < iv.effective_to)}


# --------------------------------------------------------------------------- bands


def test_band_labels_are_the_five_fixed_bands():
    assert band_labels() == ["0-20", "20-40", "40-60", "60-80", "80-100"]


def test_ratio_keys_are_the_four_requested_ratios():
    assert ratio_keys() == ["80-100/60-80", "60-80/40-60", "40-60/20-40", "80-100/20-40"]


def test_band_switch_has_a_branch_per_band_except_the_last():
    sw = band_switch("$data.truevx_score")["$switch"]
    assert len(sw["branches"]) == len(BANDS) - 1
    assert sw["default"] == "80-100"
    # the last band is the default, which is what makes a score of exactly 100 land in
    # 80-100 rather than falling through the histogram entirely
    assert sw["branches"][0] == {"case": {"$lt": ["$data.truevx_score", 20]}, "then": "0-20"}


# -------------------------------------------------------------------------- ratios


def test_compute_ratios_divides_the_named_bands():
    counts = {"0-20": 10, "20-40": 20, "40-60": 40, "60-80": 60, "80-100": 120}
    r = compute_ratios(counts)
    assert r["80-100/60-80"] == 2.0
    assert r["60-80/40-60"] == 1.5
    assert r["40-60/20-40"] == 2.0
    assert r["80-100/20-40"] == 6.0


def test_zero_denominator_is_none_not_zero_and_not_infinity():
    counts = {"0-20": 0, "20-40": 0, "40-60": 5, "60-80": 0, "80-100": 7}
    r = compute_ratios(counts)
    assert r["80-100/60-80"] is None       # 7 / 0
    assert r["40-60/20-40"] is None        # 5 / 0
    assert r["80-100/20-40"] is None       # 7 / 0
    assert r["60-80/40-60"] == 0.0         # 0 / 5 is a real, meaningful zero


def test_zero_numerator_over_nonzero_denominator_is_a_real_zero():
    assert compute_ratios({"60-80": 0, "40-60": 3})["60-80/40-60"] == 0.0


def test_missing_band_counts_as_zero():
    assert compute_ratios({"40-60": 4})["60-80/40-60"] == 0.0


def test_ratios_are_rounded_but_not_truncated():
    assert compute_ratios({"80-100": 1, "60-80": 3})["80-100/60-80"] == 0.3333


def test_every_pair_appears_in_the_output():
    r = compute_ratios({lbl: 1 for lbl in band_labels()})
    assert set(r) == {f"{n}/{d}" for n, d in RATIO_PAIRS}


# -------------------------------------------------------------------------- window


def test_lookback_defaults_to_five_years():
    lo, hi = resolve_window(None, None, None, date(2026, 9, 5))
    assert hi == date(2026, 9, 5)
    assert lo == date(2021, 9, 6)


def test_lookback_one_year():
    lo, _ = resolve_window("1Y", None, None, date(2026, 9, 5))
    assert lo == date(2025, 9, 5)


def test_max_lookback_reaches_back_before_any_data():
    lo, _ = resolve_window("MAX", None, None, date(2026, 9, 5))
    assert lo == date(2000, 1, 1)


def test_explicit_dates_win_over_lookback():
    lo, hi = resolve_window("5Y", "2024-01-01", "2024-06-30", date(2026, 9, 5))
    assert (lo, hi) == (date(2024, 1, 1), date(2024, 6, 30))


def test_unknown_lookback_falls_back_to_five_years():
    lo, _ = resolve_window("banana", None, None, date(2026, 9, 5))
    assert lo == date(2021, 9, 6)


# -------------------------------------------------------------------------- epochs


def test_a_static_index_is_one_epoch():
    ivs = [_Iv("A", None, None), _Iv("B", None, None)]
    eps = membership_epochs(ivs, date(2024, 1, 1), date(2024, 12, 31), _members_as_of)
    assert eps == [Epoch(date(2024, 1, 1), date(2024, 12, 31), ("A", "B"))]


def test_a_change_inside_the_window_splits_it():
    # C joins on 2024-07-01; B leaves the same day
    ivs = [_Iv("A", None, None), _Iv("B", None, date(2024, 7, 1)), _Iv("C", date(2024, 7, 1), None)]
    eps = membership_epochs(ivs, date(2024, 1, 1), date(2024, 12, 31), _members_as_of)
    assert [e.members for e in eps] == [("A", "B"), ("A", "C")]
    assert eps[0].end == eps[1].start == date(2024, 7, 1)


def test_changes_outside_the_window_do_not_split_it():
    ivs = [_Iv("A", None, None), _Iv("B", date(2020, 1, 1), date(2030, 1, 1))]
    eps = membership_epochs(ivs, date(2024, 1, 1), date(2024, 12, 31), _members_as_of)
    assert len(eps) == 1


def test_epochs_tile_the_window_without_gaps_or_overlap():
    ivs = [_Iv("A", None, None),
           _Iv("B", None, date(2024, 4, 1)),
           _Iv("C", date(2024, 4, 1), date(2024, 9, 1)),
           _Iv("D", date(2024, 9, 1), None)]
    eps = membership_epochs(ivs, date(2024, 1, 1), date(2024, 12, 31), _members_as_of)
    assert eps[0].start == date(2024, 1, 1)
    assert eps[-1].end == date(2024, 12, 31)
    for a, b in zip(eps, eps[1:]):
        assert a.end == b.start


def test_an_epoch_with_no_members_is_dropped():
    ivs = [_Iv("A", date(2024, 6, 1), None)]
    eps = membership_epochs(ivs, date(2024, 1, 1), date(2024, 12, 31), _members_as_of)
    assert [e.members for e in eps] == [("A",)]


# ------------------------------------------------------------------------ assemble


def test_assemble_fills_missing_bands_and_sorts_by_date():
    rows = assemble({"2024-01-02": {"80-100": 3, "60-80": 6},
                     "2024-01-01": {"40-60": 2, "20-40": 1}})
    assert [r["date"] for r in rows] == ["2024-01-01", "2024-01-02"]
    assert rows[0]["counts"] == {"0-20": 0, "20-40": 1, "40-60": 2, "60-80": 0, "80-100": 0}
    assert rows[0]["total"] == 3
    assert rows[1]["ratios"]["80-100/60-80"] == 0.5


def test_assemble_attaches_price_only_where_it_exists():
    rows = assemble({"2024-01-01": {"40-60": 1}, "2024-01-02": {"40-60": 1}},
                    prices={"2024-01-02": {"close": 100.0}})
    assert "price" not in rows[0]
    assert rows[1]["price"] == {"close": 100.0}


@pytest.mark.parametrize("counts,expected", [
    ({"80-100": 0, "60-80": 0}, None),
    ({"80-100": 5, "60-80": 5}, 1.0),
])
def test_ratio_edge_cases(counts, expected):
    assert compute_ratios(counts)["80-100/60-80"] == expected
