from datetime import date
from app.data.universe.membership import MembershipInterval
from app.data.universe.provider import IndexMembershipProvider, load_provider_from_seed


def test_members_as_of_respects_intervals():
    intervals = [
        MembershipInterval("AAA", None, None),                       # always
        MembershipInterval("OLD", None, date(2022, 3, 31)),          # left at 2022-03-31
        MembershipInterval("NEW", date(2022, 3, 31), None),          # joined 2022-03-31
    ]
    p = IndexMembershipProvider({"NIFTY50": intervals})
    assert p.members_as_of("NIFTY50", date(2021, 1, 1)) == {"AAA", "OLD"}
    assert p.members_as_of("NIFTY50", date(2023, 1, 1)) == {"AAA", "NEW"}


def test_union_over_window_includes_dropped_names():
    intervals = [
        MembershipInterval("AAA", None, None),
        MembershipInterval("OLD", None, date(2022, 3, 31)),
        MembershipInterval("NEW", date(2022, 3, 31), None),
    ]
    p = IndexMembershipProvider({"NIFTY50": intervals})
    assert p.union_over("NIFTY50", date(2021, 1, 1), date(2023, 1, 1)) == {"AAA", "OLD", "NEW"}


def test_load_from_seed(tmp_path):
    (tmp_path / "nifty50_current.csv").write_text("symbol\nAAA\nNEW\n")
    (tmp_path / "nifty50_changelog.csv").write_text(
        "effective_date,added,removed,source_url\n"
        "2022-03-31,NEW,OLD,https://example.com\n"
    )
    p = load_provider_from_seed(tmp_path, anchor=date(2026, 1, 1))
    assert p.members_as_of("NIFTY50", date(2021, 1, 1)) == {"AAA", "OLD"}
    assert "NEW" in p.members_as_of("NIFTY50", date(2023, 1, 1))


def test_load_from_seed_ignores_blank_symbols(tmp_path):
    (tmp_path / "nifty50_current.csv").write_text("symbol\nAAA\n\n")
    p = load_provider_from_seed(tmp_path, anchor=date(2026, 1, 1))
    assert p.members_as_of("NIFTY50", date(2026, 1, 1)) == {"AAA"}
