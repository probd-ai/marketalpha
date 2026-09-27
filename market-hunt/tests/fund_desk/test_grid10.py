import numpy as np
import pandas as pd
import pytest

from fund.engine.costs import Costs
from fund.engine.grid10 import Grid10
from fund.engine.replay import replay_sleeve
from fund.engine.types import Lot, Reason, SleeveState

from helpers import BENCH, frame, make_market

ZERO = Costs(0.0, 0.0, 0.0)
SYMS = [f"S{i:02d}" for i in range(12)]


def _market(grid_vals, roc_vals, days=5, **kw):
    idx = pd.bdate_range("2021-01-04", periods=days)
    closes = pd.DataFrame(100.0, index=idx, columns=SYMS)
    grid = pd.DataFrame([grid_vals] * days, index=idx, columns=SYMS, dtype=float)
    roc = pd.DataFrame([roc_vals] * days, index=idx, columns=SYMS, dtype=float)
    return make_market(SYMS, days, closes=closes, grid=grid, roc=roc, **kw), idx


def _step(m, idx, st, i=1):
    return Grid10(10).on_day(st, m, ZERO, idx, i, idx[i - 1], True)


def test_buys_the_lowest_22d_names_in_grid_up():
    grid = [1] * 12
    roc = [0.05 - 0.01 * i for i in range(12)]           # S11 lowest
    m, idx = _market(grid, roc)
    st = SleeveState(cash=1000.0)
    orders = _step(m, idx, st)
    assert sorted(o.symbol for o in orders) == SYMS[2:]
    assert all(o.reason == Reason.NEW_LOWEST_22D for o in orders)
    assert st.cash == pytest.approx(0.0, abs=1e-9)


def test_sell_needs_grid_down_and_positive_month():
    grid = [-1, -1] + [1] * 10
    roc = [0.04, -0.03] + [0.0] * 10
    m, idx = _market(grid, roc)
    st = SleeveState(cash=0.0, lots={"S00": [Lot("S00", 1.0, idx[0].date())], "S01": [Lot("S01", 1.0, idx[0].date())]})
    orders = _step(m, idx, st)
    sells = [o for o in orders if o.side == "SELL"]
    assert [o.symbol for o in sells] == ["S00"] and sells[0].reason == Reason.GRID_DOWN_POSITIVE_MONTH
    assert "S01" in st.lots                              # down-state but negative month: kept until it bounces


def test_winners_are_never_trimmed_and_surplus_spreads_equally():
    grid, roc = [1] * 12, [0.0] * 12
    m, idx = _market(grid, roc)
    lots = {s: [Lot(s, 1.0, idx[0].date())] for s in SYMS[:10]}
    lots["S00"] = [Lot("S00", 5.0, idx[0].date())]       # a winner worth 500 of 1400+cash
    st = SleeveState(cash=100.0, lots=lots)
    orders = _step(m, idx, st)
    assert st.lots["S00"][0].qty >= 5.0                  # never cut
    assert all(o.side == "BUY" for o in orders)
    # target = (100 + 1400)/10 = 150 ; gaps 50 each for 9 names = 450 > cash 100 -> pro-rata, winner gets nothing
    assert {o.symbol for o in orders} == set(SYMS[1:10])
    assert all(o.value == pytest.approx(100.0 / 9) for o in orders)
    assert all(o.reason == Reason.TOPUP_TO_TARGET for o in orders)


def test_remainder_spread_over_all_holdings_including_winners():
    # a remainder only exists when a held name cannot trade (it counts in equity but has no gap)
    grid, roc = [1] * 12, [0.0] * 12
    m, idx = _market(grid, roc)
    m.opens.loc[idx[1], "S09"] = np.nan                  # S09 halted, worth 50
    lots = {s: [Lot(s, 1.0, idx[0].date())] for s in SYMS[1:9]}
    lots["S00"] = [Lot("S00", 3.0, idx[0].date())]       # winner above target
    lots["S09"] = [Lot("S09", 0.5, idx[0].date())]
    st = SleeveState(cash=900.0, lots=lots)              # equity 2050, target 205, gaps 8 x 105 = 840 < 900
    orders = {o.symbol: o for o in _step(m, idx, st)}
    assert orders["S00"].reason == Reason.REMAINDER_SPREAD and orders["S00"].value == pytest.approx(60 / 9)
    assert orders["S01"].reason == Reason.TOPUP_TO_TARGET and orders["S01"].value == pytest.approx(105 + 60 / 9)
    assert "S09" not in orders and st.lots["S00"][0].qty > 3.0


def test_fewer_eligible_names_gives_a_smaller_book():
    grid = [1, 1, 1] + [-1] * 9
    m, idx = _market(grid, [0.0] * 12)
    st = SleeveState(cash=300.0)
    _step(m, idx, st)
    assert sorted(st.lots) == ["S00", "S01", "S02"]


def test_holding_without_open_is_kept_and_valued_at_last_close():
    grid, roc = [-1] + [1] * 11, [0.05] + [0.0] * 11
    m, idx = _market(grid, roc)
    m.opens.loc[idx[1], "S00"] = np.nan                  # halted today
    st = SleeveState(cash=0.0, lots={"S00": [Lot("S00", 1.0, idx[0].date())]})
    _step(m, idx, st)
    assert "S00" in st.lots


def test_index_exit_is_sold_next_open():
    idx = pd.bdate_range("2021-01-04", periods=5)
    mem = lambda d: set(SYMS) - ({"S00"} if d >= idx[2].date() else set())
    m, _ = _market([1] * 12, [0.0] * 12, members=mem)
    res = replay_sleeve(Grid10(10), m, ZERO, idx[1], idx[-1], {idx[1]}, 1000.0)
    exits = [o for d, os in res.orders.items() for o in os if o.reason == Reason.INDEX_EXIT]
    assert [(o.symbol) for o in exits] == ["S00"] and idx[2] in res.orders


def test_replay_is_deterministic_and_checkpoint_equivalent():
    days = 60
    idx = pd.bdate_range("2021-01-04", periods=days)
    rng = np.random.default_rng(7)
    grid = pd.DataFrame(rng.choice([-1.0, 1.0], (days, 12)), index=idx, columns=SYMS)
    roc = pd.DataFrame(rng.normal(0, 0.05, (days, 12)), index=idx, columns=SYMS)
    m = make_market(SYMS, days, seed=3, grid=grid, roc=roc)
    sched = set(idx[::5])
    costs = Costs(0.0012, 0.001, 0.0005)
    full = replay_sleeve(Grid10(10), m, costs, idx[1], idx[-1], sched, 1e6)
    again = replay_sleeve(Grid10(10), m, costs, idx[1], idx[-1], sched, 1e6)
    pd.testing.assert_series_equal(full.equity, again.equity)
    half = replay_sleeve(Grid10(10), m, costs, idx[1], idx[30], sched, 1e6)
    rest = replay_sleeve(Grid10(10), m, costs, idx[1], idx[-1], sched, 1e6, resume=(half.last_date, half.state))
    joined = pd.concat([half.equity, rest.equity])
    pd.testing.assert_series_equal(joined, full.equity)
    assert rest.state.to_dict() == full.state.to_dict()


def test_checkpoint_keeps_index_exit_across_membership_change():
    days = 30
    idx = pd.bdate_range("2021-01-04", periods=days)
    rng = np.random.default_rng(3)
    grid = pd.DataFrame(1.0, index=idx, columns=SYMS)
    roc = pd.DataFrame(rng.normal(0, 0.05, (days, 12)), index=idx, columns=SYMS)
    cut = idx[15]
    mem = lambda d: set(SYMS) - ({"S00", "S01", "S02"} if d >= cut.date() else set())
    m = make_market(SYMS, days, seed=2, grid=grid, roc=roc, members=mem)
    costs = Costs(0.0012, 0.001, 0.0005)
    full = replay_sleeve(Grid10(10), m, costs, idx[1], idx[-1], {idx[1]}, 1e6)
    half = replay_sleeve(Grid10(10), m, costs, idx[1], idx[14], {idx[1]}, 1e6)     # stop the day before the change
    rest = replay_sleeve(Grid10(10), m, costs, idx[1], idx[-1], {idx[1]}, 1e6, resume=(half.last_date, half.state))
    pd.testing.assert_series_equal(pd.concat([half.equity, rest.equity]), full.equity)
    assert not {"S00", "S01", "S02"} & set(full.state.lots)
