# Target-to-account allocation. Pure — no broker, no Mongo.
#
# Each target position goes to exactly ONE account. Splitting every position
# proportionally was rejected: it doubles the order count, halves each order (worse
# against a 0.1% limit band and the ADV cap), doubles brokerage, and fragments every
# future sell — buying only tidier per-account weights that nobody reads.
import random

import account_allocator as aa


ACCTS = {"default": 1_132_753.0, "second": 800_044.0}


def test_every_target_lands_in_exactly_one_account():
    targets = {f"S{i}": 50_000.0 for i in range(20)}
    assign, _ = aa.allocate(targets, ACCTS, held={})
    assert set(assign) == set(targets)
    assert all(a in ACCTS for a in assign.values())


def test_split_tracks_capital_share():
    targets = {f"S{i}": 50_000.0 for i in range(40)}      # 2,000,000 total
    assign, report = aa.allocate(targets, ACCTS, held={})
    got = report["allocated_value"]
    share = ACCTS["default"] / sum(ACCTS.values())
    # Granularity is one whole position, so exactness is impossible; within one
    # position's value is the right tolerance.
    assert abs(got["default"] / sum(got.values()) - share) < 0.05


def test_allocation_is_stable_across_runs():
    """An unstable allocator would re-home positions every rebalance, paying full
    spread and brokerage to move stock that was already correct."""
    targets = {f"S{i}": (i + 1) * 1000.0 for i in range(30)}
    first, _ = aa.allocate(targets, ACCTS, held={})
    for _ in range(5):
        again, _ = aa.allocate(dict(targets), ACCTS, held={})
        assert again == first


def test_equal_values_tie_break_by_account_name_not_dict_order():
    targets = {"A": 1000.0, "B": 1000.0}
    a1, _ = aa.allocate(targets, {"zzz": 5000.0, "aaa": 5000.0}, held={})
    a2, _ = aa.allocate(targets, {"aaa": 5000.0, "zzz": 5000.0}, held={})
    assert a1 == a2


def test_held_position_stays_where_it_is():
    """Stickiness beats optimality: re-homing a correctly-held name costs a sell in
    one account and a buy in the other for no benefit."""
    targets = {"AAA": 100_000.0, "BBB": 100_000.0}
    assign, _ = aa.allocate(targets, ACCTS, held={"second": {"AAA": 10}})
    assert assign["AAA"] == "second"


def test_stickiness_applies_before_greedy_capacity():
    """Even when the sticky account is the smaller one."""
    targets = {"AAA": 400_000.0}
    assign, _ = aa.allocate(targets, ACCTS, held={"second": {"AAA": 5}})
    assert assign["AAA"] == "second"


def test_target_larger_than_any_budget_is_still_placed_and_reported():
    """Dropping it would silently shrink the book."""
    targets = {"HUGE": 5_000_000.0}
    assign, report = aa.allocate(targets, ACCTS, held={})
    assert assign["HUGE"] in ACCTS
    assert "HUGE" in report["over_budget"]


def test_no_accounts_raises_rather_than_returning_empty():
    try:
        aa.allocate({"AAA": 1.0}, {}, held={})
    except ValueError:
        return
    raise AssertionError("must raise, not silently allocate nothing")


def test_zero_capital_accounts_are_skipped_not_crashed():
    assign, _ = aa.allocate({"AAA": 1000.0}, {"a": 0.0, "b": 5000.0}, held={})
    assert assign["AAA"] == "b"


def test_property_disjoint_and_total_conserved():
    rnd = random.Random(7)
    for _ in range(100):
        targets = {f"S{i}": rnd.uniform(1_000, 200_000) for i in range(rnd.randint(1, 40))}
        assign, report = aa.allocate(targets, ACCTS, held={})
        assert set(assign) == set(targets)                     # nothing dropped
        buckets = {a: [s for s, x in assign.items() if x == a] for a in ACCTS}
        flat = [s for v in buckets.values() for s in v]
        assert len(flat) == len(set(flat)) == len(targets)     # disjoint
        assert abs(sum(report["allocated_value"].values())
                   - sum(targets.values())) < 1e-6             # value conserved
