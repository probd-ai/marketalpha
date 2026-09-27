"""Golden master: the engine's Grid-10 sleeve equals the frozen research harness on identical inputs (Task 7)."""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.integration

HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "research_grid"
START, END, CAP, SLIP = date(2020, 10, 1), date(2026, 9, 25), 1_260_000, 0.0005


@pytest.fixture(scope="module")
def setup():
    sys.path.insert(0, str(HARNESS))
    from grid_meanrev import BENCH, charge_rates, load_data
    from membership import load_membership, members_as_of, union_over
    from pymongo import MongoClient
    from value_grid_filter import grid_state as h_grid_state

    from fund.engine.market import Market
    from fund.engine.signals import grid_state, roc

    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, START, date.today()))
    opens, closes, ml = load_data(db, ever, field="mean_long")
    cols = [c for c in closes.columns if c != BENCH]
    h_state = h_grid_state(ml.reindex(index=closes.index, columns=cols))
    h_roc = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    m = Market(opens, closes, BENCH, lambda d: members_as_of(iv, d), roc(closes[cols]),
               grid=grid_state(ml.reindex(index=closes.index, columns=cols)))
    return dict(opens=opens, closes=closes, iv=iv, h_state=h_state, h_roc=h_roc, m=m, rates=charge_rates())


def test_signals_equal_harness(setup):
    pd.testing.assert_frame_equal(setup["m"].roc, setup["h_roc"])
    pd.testing.assert_frame_equal(setup["m"].grid, setup["h_state"])


@pytest.mark.parametrize("k", range(5))
def test_grid10_weekly_matches_harness(setup, k):
    import rotation_roc
    from oracle import canonicalize
    canonicalize(rotation_roc)
    run = rotation_roc.run

    from fund.engine.calendar import period_start_shifted, trading_days
    from fund.engine.costs import Costs
    from fund.engine.grid10 import Grid10
    from fund.engine.replay import replay_sleeve

    st, roc22 = setup["h_state"], setup["h_roc"]

    def pick(prev, pool, held):
        sells = {s for s in held if s in pool and st.at[prev, s] == -1
                 and not pd.isna(roc22.at[prev, s]) and roc22.at[prev, s] > 0}
        keep = [s for s in held if s not in sells]
        filtered = [s for s in pool if st.at[prev, s] == 1 and not pd.isna(roc22.at[prev, s]) and s not in keep]
        return sorted(keep + list(roc22.loc[prev, filtered].sort_values().index)[: max(0, 10 - len(keep))])

    br, sr = setup["rates"]
    log = []
    h_eq, _ = run(setup["opens"], setup["closes"], roc22, setup["iv"], START, END, 10, "low", f"weekly+{k}", CAP, SLIP,
                  br, sr, pick_fn=pick, log=log)
    m = setup["m"]
    cal = trading_days(m.closes[m.bench], START, END)
    res = replay_sleeve(Grid10(10), m, Costs(br, sr, SLIP), START, END, period_start_shifted(cal, "weekly", k), CAP)
    np.testing.assert_allclose(res.equity.to_numpy(), h_eq["equity"].to_numpy(), rtol=1e-9)
    eng = [(d, o.symbol, o.side) for d, os in res.orders.items() for o in os]
    assert sorted(map(str, eng)) == sorted(str((pd.Timestamp(x["date"]), x["symbol"], x["side"])) for x in log)
    ref = pd.read_csv(HARNESS / "grid10_weekly.csv", index_col=0, parse_dates=True)[str(k)]
    assert res.equity.iloc[-1] / res.equity.iloc[0] == pytest.approx(ref.iloc[-1], rel=1e-9)
