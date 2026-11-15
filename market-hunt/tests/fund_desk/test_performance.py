"""Performance analytics (fund.performance): statistics on hand-checkable curves, plus the accounting invariants that
make the page trustworthy over ANY period — per-symbol P&L sums to the book's change in value, and sleeve
contributions plus the rebalance cost sum to the fund's return."""
import math

import numpy as np
import pandas as pd
import pytest

from fund import performance as P
from fund.engine.costs import Costs
from fund.engine.fund import blend
from fund.engine.grid10 import Grid10
from fund.engine.replay import replay_sleeve
from fund.engine.types import Order, Reason

from helpers import make_market

SYMS = [f"S{i:02d}" for i in range(12)]


def _curve(vals, start="2026-01-05"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)), dtype=float)


# ------------------------------------------------------------------------------------------------- series stats
def test_series_stats_on_a_hand_checked_curve():
    c = _curve([100, 110, 99, 121])
    s = P.series_stats(c)
    assert s["total_return"] == pytest.approx(21.0)
    assert s["cagr"] is None                                   # under a year: never annualise a short period
    assert s["max_dd"] == pytest.approx(-10.0)                 # 110 -> 99
    assert (s["max_dd_peak"], s["max_dd_trough"], s["max_dd_recovered"]) == ("2026-01-06", "2026-01-07", "2026-01-08")
    assert s["dd_now"] == pytest.approx(0.0)
    assert s["best_day"] == {"date": "2026-01-08", "r": pytest.approx(100 * (121 / 99 - 1))}
    assert s["worst_day"]["r"] == pytest.approx(-10.0)
    r = c.pct_change().dropna()
    assert s["vol"] == pytest.approx(100 * r.std(ddof=1) * math.sqrt(252))
    assert s["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(252))


def test_unrecovered_drawdown_and_annualised_cagr_after_a_year():
    idx = pd.bdate_range("2024-01-01", "2025-06-30")
    c = pd.Series(np.linspace(100, 150, len(idx)), index=idx)
    c.iloc[-5:] = 120.0                                        # falls at the end and stays down
    s = P.series_stats(c)
    years = (idx[-1] - idx[0]).days / 365.25
    assert s["cagr"] == pytest.approx(100 * ((120 / 100) ** (1 / years) - 1))
    assert s["max_dd_recovered"] is None and s["dd_now"] == pytest.approx(s["max_dd"])


def test_monthly_returns_chain_to_the_total():
    idx = pd.bdate_range("2026-01-01", "2026-04-15")
    c = pd.Series(100 * np.exp(np.random.default_rng(1).normal(0, 0.01, len(idx)).cumsum()), index=idx)
    m = P.monthly_returns(c)
    assert [x["m"] for x in m] == ["2026-01", "2026-02", "2026-03", "2026-04"]
    chained = np.prod([1 + x["r"] / 100 for x in m]) - 1
    assert 100 * chained == pytest.approx(100 * (c.iloc[-1] / c.iloc[0] - 1))
    assert m[1]["r"] == pytest.approx(100 * (c.loc["2026-02"].iloc[-1] / c.loc["2026-01"].iloc[-1] - 1))


def test_anchor_session_before_model_start_is_not_a_month():
    idx = pd.DatetimeIndex(["2025-12-31", "2026-01-01", "2026-01-30", "2026-02-27"])
    c = pd.Series([100.0, 99.0, 105.0, 110.25], index=idx)
    m = P.monthly_returns(c)
    assert [x["m"] for x in m] == ["2026-01", "2026-02"]
    assert m[0]["r"] == pytest.approx(5.0)                     # from the anchor close (100), not day 1 (99)


def test_relative_to_itself_and_to_a_leveraged_copy():
    c = _curve([100, 102, 101, 105, 103, 108])
    same = P.relative(c, c, "self", "index")
    assert same["excess"] == pytest.approx(0.0) and same["beta"] == pytest.approx(1.0) and same["corr"] == pytest.approx(1.0)
    r = c.pct_change().fillna(0)
    double = 100 * (1 + 2 * r).cumprod()
    rel = P.relative(double, c, "base", "index")
    assert rel["beta"] == pytest.approx(2.0) and rel["corr"] == pytest.approx(1.0)


def test_relative_forward_fills_a_benchmark_holiday():
    c = _curve([100, 101, 102, 103])
    b = c.drop(c.index[2])                                     # benchmark missing one session
    rel = P.relative(c, b, "b", "index")
    assert rel["total_return"] == pytest.approx(3.0)


# ------------------------------------------------------------------------------------------------- fund level
def _blended():
    idx = pd.bdate_range("2026-01-01", "2026-05-29")
    rng = np.random.default_rng(4)
    curves = pd.DataFrame({k: np.exp(rng.normal(0.0005, 0.012, len(idx)).cumsum()) for k in "ABC"}, index=idx)
    fr = blend(curves, {"A": 0.5, "B": 0.3, "C": 0.2}, reset_cost=0.0015, band_days=set(idx[::10]), band=0.02)
    return curves, fr


def test_contributions_plus_reset_cost_equal_the_fund_return():
    curves, fr = _blended()
    assert fr.reset_cost > 0
    pts, cost_pts = P.contribution(curves, fr.sleeve_values)
    total = 100 * (fr.equity.iloc[-1] / fr.equity.iloc[0] - 1)
    assert sum(pts.values()) + cost_pts == pytest.approx(total, abs=1e-9)
    assert cost_pts == pytest.approx(-100 * fr.reset_cost / fr.equity.iloc[0], abs=1e-9)   # derived cost = blend's


def test_contributions_reconcile_over_any_period():
    curves, fr = _blended()
    cut = curves.index[45]                                      # e.g. "this year": from a later anchor session
    pts, cost_pts = P.contribution(curves.loc[cut:], fr.sleeve_values.loc[cut:])
    e = fr.equity.loc[cut:]
    assert sum(pts.values()) + cost_pts == pytest.approx(100 * (e.iloc[-1] / e.iloc[0] - 1), abs=1e-9)


def test_ew_universe_respects_point_in_time_membership():
    idx = pd.bdate_range("2026-01-05", periods=4)
    closes = pd.DataFrame({"A": [100, 110, 121, 121], "B": [100, 100, 50, 50]}, index=idx, dtype=float)
    later = idx[2].date()
    m = make_market(["A", "B"], 4, closes=closes, members=lambda d: {"A"} if d < later else {"A", "B"})
    ew = P.ew_universe(m, idx)
    # day 1: only A (+10%); day 2: A +10%, B -50% -> mean -20%; day 3: flat
    assert list(ew.round(6)) == [1.0, 1.1, round(1.1 * 0.8, 6), round(1.1 * 0.8, 6)]


# ------------------------------------------------------------------------------------------------- trades
def _o(sym, side, qty, value):
    return Order(sym, side, qty, value / qty, value, Reason.TOPUP_TO_TARGET)


D4 = pd.bdate_range("2026-01-05", periods=4)
HAND = {D4[0]: [_o("A", "BUY", 10, 101.0), _o("B", "BUY", 5, 50.5)],
        D4[1]: [_o("A", "SELL", 4, 47.0)],
        D4[3]: [_o("A", "SELL", 6, 66.0)]}


def test_positions_and_summary_on_hand_built_fills():
    out = P.trade_summary(P.positions(HAND, {"B": 12.0}), base=1000.0)
    assert out["closed_count"] == 1 and out["open_count"] == 1
    a = out["closed"][0]
    assert a["symbol"] == "A" and a["days"] == 3 and a["ret"] == pytest.approx(100 * (113 / 101 - 1))
    assert a["pnl_pts"] == pytest.approx(1.2) and out["hit_rate"] == 100.0
    assert out["open"][0] == {"symbol": "B", "opened": "2026-01-05", "qty": 5, "value": 60.0,
                              "ret": pytest.approx(100 * (60 / 50.5 - 1)), "pnl_pts": pytest.approx(0.95)}


def test_summary_can_keep_only_positions_closed_in_the_period():
    pos = P.positions(HAND, {"B": 12.0})
    assert P.trade_summary(pos, 1000.0, closed_after="2026-01-08")["closed_count"] == 0
    assert P.trade_summary(pos, 1000.0, closed_after="2026-01-07")["closed_count"] == 1


def test_symbol_pnl_full_period_is_the_cash_identity():
    by = {x["symbol"]: x["pts"] for x in P.symbol_pnl(HAND, D4[0] - pd.Timedelta(days=1), {}, {"A": 11.0, "B": 12.0},
                                                      base=1000.0)}
    assert by == {"A": pytest.approx(1.2), "B": pytest.approx(0.95)}   # 113 − 101 and 60 − 50.5, over 1000


def test_symbol_pnl_from_a_later_start_carries_holdings_in_at_that_close():
    # from the close of D4[1]: A 6 sh @ 10.5 then sold for 66 (gain 3); B 5 sh 55 -> 60 (gain 5)
    out = P.symbol_pnl(HAND, D4[1], {"A": 10.5, "B": 11.0}, {"A": 11.0, "B": 12.0}, base=100.0)
    assert out == [{"symbol": "B", "pts": pytest.approx(5.0)}, {"symbol": "A", "pts": pytest.approx(3.0)}]


def test_reopened_symbol_is_a_new_position():
    d = pd.bdate_range("2026-01-05", periods=4)
    orders = {d[0]: [_o("A", "BUY", 1, 100.0)], d[1]: [_o("A", "SELL", 1, 90.0)],
              d[2]: [_o("A", "BUY", 1, 90.0)], d[3]: [_o("A", "SELL", 1, 99.0)]}
    out = P.trade_summary(P.positions(orders, {}), base=100.0)
    assert out["closed_count"] == 2 and out["hit_rate"] == 50.0
    assert out["avg_loss"] == pytest.approx(-10.0) and out["avg_win"] == pytest.approx(10.0)


def test_dust_remnant_stays_with_its_trade():
    """Real case (Growth, JINDALSTEL 2026): an expiry leaves 6.9e-8 of 9.1e-5 shares, later sessions sell the rest
    and 1e-20-share dust. One closed trade, no error, its P&L includes the remnant's sale."""
    d = pd.bdate_range("2026-01-05", periods=5)
    orders = {d[0]: [_o("A", "BUY", 9.1e-5, 9.1e-2)],
              d[1]: [_o("A", "SELL", 9.1e-5 - 6.9e-8, 0.1)],          # closes: remnant 0.076% < 0.1% of peak
              d[2]: [_o("A", "SELL", 6.9e-8, 7.5e-5)],                # remnant sold later -> same trade
              d[3]: [_o("A", "SELL", 1e-20, 1e-17)]}                  # dust beyond the remnant (bought as dust)
    out = P.trade_summary(P.positions(orders, {}), base=1.0)
    assert out["closed_count"] == 1 and out["open_count"] == 0
    assert out["closed"][0]["closed"] == "2026-01-06"
    assert out["closed"][0]["pnl_pts"] == pytest.approx(100 * (0.1 + 7.5e-5 + 1e-17 - 9.1e-2))


def test_unsold_remnant_is_valued_and_carried_into_a_reopen():
    d = pd.bdate_range("2026-01-05", periods=4)
    orders = {d[0]: [_o("A", "BUY", 1.0, 100.0)],
              d[1]: [_o("A", "SELL", 1.0 - 1e-6, 110.0)],             # remnant 1e-6 shares stays
              d[2]: [_o("A", "BUY", 2.0, 200.0)]}                     # reopen absorbs the remnant
    out = P.trade_summary(P.positions(orders, {"A": 120.0}), base=1000.0)
    assert out["closed_count"] == 1 and out["open_count"] == 1
    total = sum(t["pnl_pts"] for t in out["closed"] + out["open"])
    assert total == pytest.approx(100 * (-100 + 110 - 200 + (2.0 + 1e-6) * 120) / 1000)


def test_a_real_sell_after_close_is_still_refused():
    d = pd.bdate_range("2026-01-05", periods=3)
    orders = {d[0]: [_o("A", "BUY", 10, 100.0)], d[1]: [_o("A", "SELL", 10, 100.0)], d[2]: [_o("A", "SELL", 1, 10.0)]}
    with pytest.raises(ValueError, match="no open position"):
        P.positions(orders, {})


def test_sell_without_position_is_refused():
    d = pd.bdate_range("2026-01-05", periods=1)
    with pytest.raises(ValueError, match="no open position"):
        P.positions({d[0]: [_o("A", "SELL", 1, 10.0)]}, {})


def test_symbol_pnl_reconciles_to_a_real_engine_replay_in_every_period():
    days = 80
    idx = pd.bdate_range("2021-01-04", periods=days)
    rng = np.random.default_rng(11)
    grid = pd.DataFrame(rng.choice([-1.0, 1.0], (days, 12)), index=idx, columns=SYMS)
    roc = pd.DataFrame(rng.normal(0, 0.05, (days, 12)), index=idx, columns=SYMS)
    m = make_market(SYMS, days, seed=5, grid=grid, roc=roc)
    cap = 1e6
    res = replay_sleeve(Grid10(10), m, Costs(0.0012, 0.001, 0.0005), idx[1], idx[-1], set(idx[::5]), cap)
    close = lambda d: {s: float(m.cclose.at[d, s]) for s in SYMS}
    eq = res.equity
    for start, v0 in [(idx[0], cap), (idx[40], float(eq.loc[idx[40]]))]:      # whole history, and a later period
        by = P.symbol_pnl(res.orders, start, close(start), close(idx[-1]), base=v0)
        assert sum(x["pts"] for x in by) == pytest.approx(100 * (eq.iloc[-1] / v0 - 1), abs=1e-7)
    s = P.trade_summary(P.positions(res.orders, close(idx[-1])), cap)
    assert s["closed_count"] > 0, "fixture should close some positions"
    assert {p["symbol"] for p in s["open"]} == set(res.state.lots)
    full, later = P.turnover(res.orders, eq), P.turnover(res.orders, eq.loc[idx[40]:])
    assert full > 0 and later > 0
