"""Production data loader equals the research loader; the full five-sleeve fund runs end to end (Task 11)."""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.integration
HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "research_grid"
START, END = date(2020, 10, 1), date(2026, 9, 25)


@pytest.fixture(scope="module")
def db():
    from pymongo import MongoClient
    return MongoClient("mongodb://localhost:27017/")["market_hunt"]


def test_loader_equals_research_loader(db):
    sys.path.insert(0, str(HARNESS))
    from grid_meanrev import load_data
    from membership import load_membership, union_over

    from fund.data import build_market

    m = build_market(db, "NIFTY50", "grid10", START, date.today())
    iv = load_membership(db, "NIFTY50", date.today())
    opens, closes, ml = load_data(db, sorted(union_over(iv, START, date.today())), field="mean_long")
    pd.testing.assert_frame_equal(m.closes, closes)
    pd.testing.assert_frame_equal(m.opens, opens)


def test_full_fund_runs(db):
    from fund.data import build_asset_market, build_market, charge_rates
    from fund.engine.asset import Asset
    from fund.engine.calendar import period_start_shifted, trading_days, week_session
    from fund.engine.costs import Costs
    from fund.engine.fund import blend
    from fund.engine.grid10 import Grid10
    from fund.engine.replay import replay_sleeve
    from fund.engine.tranche import Tranche

    costs = Costs(*charge_rates(), 0.0005)
    weights = {"Value core": 0.28, "Gold": 0.08, "Nasdaq-100": 0.04, "Growth": 0.35, "Satellite": 0.25}
    curves, orders = {}, {}
    specs = {"Value core": ("NIFTY50", "grid10", Grid10(10)),
             "Growth": ("NIFTY200", "tranche", Tranche()),
             "Satellite": ("NIFTY500MICRO750", "tranche", Tranche())}
    for name, (uni, kind, book) in specs.items():
        m = build_market(db, uni, kind, START, date.today())
        cal = trading_days(m.closes[m.bench], START, END)
        sched = period_start_shifted(cal, "weekly", 0) if kind == "grid10" else week_session(cal, 0)
        r = replay_sleeve(book, m, costs, START, END, sched, 1.0)
        curves[name], orders[name] = r.equity, r.orders
    for name, sym in (("Gold", "GOLDBEES"), ("Nasdaq-100", "MON100")):
        m = build_asset_market(db, sym, START)
        cal = trading_days(m.closes[m.bench], START, END)
        r = replay_sleeve(Asset(sym), m, costs, START, END, set(), 1.0)
        curves[name], orders[name] = r.equity, r.orders
    idx = curves["Value core"].index
    px = pd.DataFrame({k: v.reindex(idx).ffill() for k, v in curves.items()})
    res = blend(px, weights)
    assert np.isfinite(res.equity).all()
    for d in res.reset_days:
        row = res.sleeve_values.loc[d]
        for k, w in weights.items():
            assert row[k] / row.sum() == pytest.approx(w, abs=1e-9)
    assert all(len(o) >= 1 for o in orders.values())
    yrs = (idx[-1] - idx[0]).days / 365.25
    print(f"\nFULL FUND (ETFs, canonical order): {((res.equity.iloc[-1]) ** (1 / yrs) - 1) * 100:.1f}%/yr, "
          f"worst fall {((res.equity / res.equity.cummax()) - 1).min() * 100:.1f}%")
    for k, v in curves.items():
        v = v.dropna()
        print(f"  {k:11s} {((v.iloc[-1] / v.iloc[0]) ** (1 / yrs) - 1) * 100:5.1f}%/yr")
