"""Golden master: the engine's weekly-tranche sleeve equals the frozen research harness (Task 9). Slow: loads N750."""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.integration

HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "research_grid"
START, SLIP = "2020-10-01", 0.0005
BOOKS = {"NIFTY200": (1_700_000, "n200_8wcap10_deploy.csv"),
         "NIFTY500MICRO750": (1_600_000, "n750_cap_weekly8w_cap10deploy.csv")}


@pytest.fixture(scope="module", params=list(BOOKS))
def setup(request):
    sys.path.insert(0, str(HARNESS))
    from alpha_test import RULES, load_means
    from grid_meanrev import BENCH, charge_rates, load_data
    from membership import load_membership, members_as_of, union_over
    from pymongo import MongoClient

    from fund.engine.market import Market
    from fund.engine.signals import alpha_eligible, roc

    index = request.param
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, index, date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, score = load_data(db, ever)
    fields = {"truevx_score": score, **load_means(db, ever)}
    cols = [c for c in closes.columns if c != BENCH]
    h_elig = pd.DataFrame(True, index=closes.index, columns=cols)
    for f, thr in RULES.items():
        h_elig &= (fields[f].reindex(index=closes.index, columns=cols) > thr).fillna(False)
    h_roc = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    m = Market(opens, closes, BENCH, lambda d: members_as_of(iv, d), roc(closes[cols]),
               elig=alpha_eligible(fields, closes.index, cols))
    return dict(index=index, opens=opens, closes=closes, iv=iv, h_elig=h_elig, h_roc=h_roc, m=m, rates=charge_rates())


def test_alpha_eligibility_equals_harness(setup):
    pd.testing.assert_frame_equal(setup["m"].elig, setup["h_elig"])


@pytest.mark.parametrize("k", range(5))
def test_tranche_matches_harness(setup, k):
    import weekly_tranche
    from oracle import canonicalize
    canonicalize(weekly_tranche)
    run_weekly = weekly_tranche.run_weekly

    from fund.engine.calendar import trading_days, week_session
    from fund.engine.costs import Costs
    from fund.engine.replay import replay_sleeve
    from fund.engine.tranche import Tranche

    cap, csv = BOOKS[setup["index"]]
    br, sr = setup["rates"]
    h_eq, _ = run_weekly(setup["opens"], setup["closes"], setup["h_roc"], setup["iv"], setup["h_elig"], START, None,
                         br, sr, k=k, capital=cap, weeks=8, slot_div=40, max_w=0.10, deploy_cash=True)
    m = setup["m"]
    cal = trading_days(m.closes[m.bench], START, None)
    res = replay_sleeve(Tranche(5, 8, 40, 0.10, True), m, Costs(br, sr, SLIP), START, None, week_session(cal, k), cap)
    np.testing.assert_allclose(res.equity.to_numpy(), h_eq["equity"].to_numpy(), rtol=1e-9)
    # The saved research CSV is a snapshot of older data; report drift instead of failing on it.
    ref = pd.read_csv(HARNESS / csv, index_col=0, parse_dates=True)[str(k)]
    print(f"{setup['index']} k={k}: engine {res.equity.iloc[-1] / res.equity.iloc[0]:.4f}x, saved CSV {ref.iloc[-1]:.4f}x")
