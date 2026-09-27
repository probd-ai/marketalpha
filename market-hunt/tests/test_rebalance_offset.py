# Execution-lag offset on rebalance dates. Pure — no data, no Mongo.
#
# The backtest ranks on a date's close and fills at that same close, which cannot
# happen live: the refresh, the run and the order all come after the market shut.
# Shifting rebalance dates forward N SESSIONS models trading on the next session.
from engine.stateless import get_rebalance_dates

# Three months of weekday sessions, deliberately with gaps (holidays).
DATES = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07",
         "2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06",
         "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"]


def test_offset_zero_is_byte_identical():
    """The gate on every other assertion: lag 0 must not change one date."""
    for dt in ("first", "mid", "last"):
        assert (get_rebalance_dates(DATES, "monthly", dt) ==
                get_rebalance_dates(DATES, "monthly", dt, 0))


def test_offset_shifts_by_sessions_not_calendar_days():
    """Jan 2 + 1 session is Jan 5 (Mon), not Jan 3 (Sat). Calendar arithmetic here
    would silently land on a day with no prices."""
    base = get_rebalance_dates(DATES, "monthly", "first")
    assert "2026-01-01" in base
    one = get_rebalance_dates(DATES, "monthly", "first", 1)
    assert "2026-01-02" in one
    two = get_rebalance_dates(DATES, "monthly", "first", 2)
    assert "2026-01-05" in two          # skips the weekend


def test_same_number_of_rebalances_when_the_shift_fits():
    base = get_rebalance_dates(DATES, "monthly", "first")
    assert len(get_rebalance_dates(DATES, "monthly", "first", 2)) == len(base)


def test_shift_past_the_end_of_data_drops_that_rebalance():
    """Trading on a day we have no prices for would be a different fiction. Better
    to lose the last rebalance than to invent it."""
    short = ["2026-01-01", "2026-01-02"]
    assert get_rebalance_dates(short, "monthly", "first", 1) == {"2026-01-02"}
    assert get_rebalance_dates(short, "monthly", "first", 5) == set()


def test_offset_applies_to_every_period_not_just_the_first():
    got = sorted(get_rebalance_dates(DATES, "monthly", "first", 1))
    assert got == ["2026-01-02", "2026-02-03", "2026-03-03"]


def test_offset_never_collides_two_rebalances_onto_one_day():
    got = get_rebalance_dates(DATES, "monthly", "first", 1)
    assert len(got) == len(set(got))
