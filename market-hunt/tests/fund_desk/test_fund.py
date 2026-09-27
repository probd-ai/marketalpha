from datetime import date

import numpy as np
import pandas as pd
import pytest

from fund.engine.asset import Asset
from fund.engine.costs import Costs
from fund.engine.fund import blend, validate_model_start
from fund.engine.replay import replay_sleeve

from helpers import make_market


def _curves():
    idx = pd.bdate_range("2021-01-25", "2021-03-10")
    a = pd.Series(np.linspace(1.0, 1.3, len(idx)), index=idx)
    b = pd.Series(np.linspace(1.0, 0.9, len(idx)), index=idx)
    return pd.DataFrame({"A": a, "B": b})


def test_weights_restored_on_month_change_and_cost_charged():
    c = _curves()
    res = blend(c, {"A": 0.6, "B": 0.4}, reset_cost=0.0015)
    for d in res.reset_days:
        row = res.sleeve_values.loc[d]
        assert row["A"] / row.sum() == pytest.approx(0.6, abs=1e-12)
    assert [d.month for d in res.reset_days] == [2, 3]
    # cost of the first reset = 0.15% of half the absolute drift
    i = c.index.get_loc(res.reset_days[0])
    v = [0.6 * c["A"].iloc[i] / c["A"].iloc[0], 0.4 * c["B"].iloc[i] / c["B"].iloc[0]]
    moved = (abs(v[0] - 0.6 * sum(v)) + abs(v[1] - 0.4 * sum(v))) / 2
    assert res.equity.iloc[i] == pytest.approx(sum(v) - moved * 0.0015, rel=1e-12)


def test_scale_is_constant_between_resets():
    res = blend(_curves(), {"A": 0.6, "B": 0.4})
    s = res.scale["A"]
    feb = s.loc["2021-02-02":"2021-02-26"]
    assert feb.max() - feb.min() < 1e-12


def test_model_start_range():
    validate_model_start(date(2020, 10, 1), date(2026, 9, 27))
    with pytest.raises(ValueError, match="2020-10-01"):
        validate_model_start(date(2020, 9, 30), date(2026, 9, 27))
    with pytest.raises(ValueError):
        validate_model_start(date(2026, 9, 28), date(2026, 9, 27))


def test_asset_sleeve_buys_once_and_holds():
    m = make_market(["GOLDBEES"], days=10, seed=1)
    idx = m.closes.index
    res = replay_sleeve(Asset("GOLDBEES"), m, Costs(0.001, 0.001, 0.0005), idx[1], idx[-1], set(idx), 1000.0)
    assert len([o for os in res.orders.values() for o in os]) == 1
    q = res.state.qty("GOLDBEES")
    assert res.equity.iloc[-1] == pytest.approx(q * m.closes["GOLDBEES"].iloc[-1])


def test_band_rebalances_only_when_drift_exceeds_it():
    idx = pd.bdate_range("2021-01-04", "2021-06-30")
    a = pd.Series(np.linspace(1.0, 2.0, len(idx)), index=idx)       # sleeve A doubles, B flat -> A drifts up
    c = pd.DataFrame({"A": a, "B": pd.Series(1.0, index=idx)})
    firsts = {idx[idx.to_period("M") == p][0] for p in idx.to_period("M").unique()}
    wide = blend(c, {"A": 0.5, "B": 0.5}, reset_days=set(), band_days=firsts, band=0.50)   # never breached
    tight = blend(c, {"A": 0.5, "B": 0.5}, reset_days=set(), band_days=firsts, band=0.03)
    assert wide.reset_days == []
    assert len(tight.reset_days) >= 3
    for d in tight.reset_days:
        assert d in firsts


def test_scheduled_months_always_rebalance():
    c = _curves()
    res = blend(c, {"A": 0.6, "B": 0.4}, reset_days={pd.Timestamp("2021-03-01")}, band_days=set(), band=0.05)
    assert res.reset_days == [pd.Timestamp("2021-03-01")]
