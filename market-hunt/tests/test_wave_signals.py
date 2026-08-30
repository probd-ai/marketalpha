from wave_models import Signal
from wave_signals import (crossed_up, crossed_down, eval_signal,
                          group_fires, count_firing_groups, any_group_fires, update_last_dir, CrossState)

def test_crossed_up_through_level():
    assert crossed_up(48, 52, 50) is True       # passed up through 50
    assert crossed_up(50, 52, 50) is False      # prev == level, not "through"
    assert crossed_up(52, 55, 50) is False      # already above
    assert crossed_up(None, 52, 50) is False    # first bar, no prev

def test_crossed_down_through_level():
    assert crossed_down(52, 48, 50) is True
    assert crossed_down(48, 47, 50) is False
    assert crossed_down(None, 47, 50) is False

def test_eval_cross_signal_with_negate():
    sig = Signal(kind="cross", metric="mean_long", level=70, direction="up", negate=True)
    today = {"mean_long": 62}; prev = {"mean_long": 38}     # crossed 40/50/60 but not 70
    assert eval_signal(sig, today, prev, {}) is True        # negate(NOT crossed 70) = True
    today2 = {"mean_long": 72}                              # this move DID cross 70
    assert eval_signal(sig, today2, prev, {}) is False

def test_group_fires_is_and():
    g = [Signal(kind="cross", metric="mean_long", level=50, direction="up"),
         Signal(kind="cross", metric="mean_long", level=70, direction="up", negate=True)]
    today = {"mean_long": 55}; prev = {"mean_long": 48}
    assert group_fires(g, today, prev, {}) is True

def test_count_firing_groups_counts_levels():
    # one big up-move 38 -> 62 crosses 40,50,60 => 3 single-level groups fire
    groups = [[Signal(kind="cross", metric="mean_long", level=L, direction="up")] for L in (40, 50, 60)]
    today = {"mean_long": 62}; prev = {"mean_long": 38}
    assert count_firing_groups(groups, today, prev, {}) == 3
    assert any_group_fires(groups, today, prev, {}) is True

def test_eval_compare_metric_vs_number_and_missing_defaults_zero():
    s = Signal(kind="compare", lhs_metric="truevx_score", op=">", rhs={"number": 50})
    assert eval_signal(s, {"truevx_score": 95}, {}, {}) is True
    assert eval_signal(s, {"truevx_score": None}, {}, {}) is False   # missing -> 0.0, not > 50

def test_eval_last_cross_dir_reads_state():
    s = Signal(kind="last_cross_dir", metric="mean_long", grid=[40, 50, 60], direction="up")
    assert eval_signal(s, {}, {}, {("mean_long", (40, 50, 60)): "up"}) is True
    assert eval_signal(s, {}, {}, {("mean_long", (40, 50, 60)): "down"}) is False

def test_compare_metric_vs_metric_and_number():
    s_met = Signal(kind="compare", lhs_metric="truevx_score", op="<=", rhs={"metric": "mean_long"})
    s_num = Signal(kind="compare", lhs_metric="mean_long", op=">", rhs={"number": 50})
    today = {"truevx_score": 40, "mean_long": 55}
    assert eval_signal(s_met, today, {}, {}) is True     # 40 <= 55
    assert eval_signal(s_num, today, {}, {}) is True     # 55 > 50

def test_update_last_dir_tracks_most_recent_cross():
    grid = [50, 60, 70, 80, 90]
    d = update_last_dir(48, 62, grid, None)      # up through 50,60
    assert d == "up"
    d = update_last_dir(62, 58, grid, d)         # down through 60
    assert d == "down"
    d = update_last_dir(58, 59, grid, d)         # no grid cross -> unchanged
    assert d == "down"

def test_last_cross_dir_signal_reads_state():
    sig = Signal(kind="last_cross_dir", metric="mean_long", grid=[50, 60, 70, 80, 90], direction="down")
    last_dirs = {("mean_long", (50, 60, 70, 80, 90)): "down"}
    assert eval_signal(sig, {"mean_long": 55}, {}, last_dirs) is True

def test_cross_state_exposes_prev_and_updates_last_dir():
    grid = (50, 60, 70, 80, 90)
    cs = CrossState(grids=[("mean_long", grid)])
    cs.update({"mean_long": 48})
    assert cs.prev_snapshot() == {"mean_long": 48}        # after first day, prev = day1
    assert cs.last_dirs[("mean_long", grid)] is None      # no cross yet (first bar)
    cs.update({"mean_long": 62})                          # up through 50,60
    assert cs.last_dirs[("mean_long", grid)] == "up"
    assert cs.prev_snapshot() == {"mean_long": 62}
