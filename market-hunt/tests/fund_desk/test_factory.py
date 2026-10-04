"""One engine factory + schedule + param validator shared by the fund desk and the simulator's book strategies."""
import pandas as pd
import pytest

from fund.config import validate_book
from fund.engine.asset import Asset
from fund.engine.calendar import period_start_shifted, week_session
from fund.engine.factory import base_schedule, make_book
from fund.engine.grid10 import Grid10
from fund.engine.tranche import Tranche


def test_make_book_defaults():
    g = make_book("grid10", {})
    assert isinstance(g, Grid10) and g.n == 10
    t = make_book("tranche", {})
    assert isinstance(t, Tranche)
    assert (t.top, t.weeks, t.slot_div, t.max_w, t.deploy_cash) == (5, 8, 40, 0.10, True)


def test_make_book_params_and_asset():
    t = make_book("tranche", {"top": 3, "weeks": 4, "slot_div": 20, "max_w": 0.2, "deploy_cash": False})
    assert (t.top, t.weeks, t.slot_div, t.max_w, t.deploy_cash) == (3, 4, 20, 0.2, False)
    assert make_book("grid10", {"n": 7}).n == 7
    a = make_book("asset", {}, symbol="GOLDBEES")
    assert isinstance(a, Asset)


def test_make_book_unknown_kind():
    with pytest.raises(ValueError, match="unknown"):
        make_book("wave", {})


def test_base_schedule_matches_calendar_rules():
    cal = pd.bdate_range("2024-01-01", periods=60)
    for k in range(5):
        assert base_schedule("grid10", cal, k) == period_start_shifted(cal, "weekly", k)
        assert base_schedule("tranche", cal, k) == week_session(cal, k)
    assert base_schedule("asset", cal, 0) == set()


@pytest.mark.parametrize("kind,params", [("grid10", {}), ("grid10", {"n": 12}),
                                         ("tranche", {"top": 5, "weeks": 8, "slot_div": 40, "max_w": 0.1,
                                                      "deploy_cash": True})])
def test_validate_book_ok(kind, params):
    assert validate_book(kind, params) == []


@pytest.mark.parametrize("kind,params,msg", [
    ("wave", {}, "unknown kind"),
    ("asset", {}, "unknown kind"),
    ("grid10", {"n": 0}, "n must be between"),
    ("grid10", {"n": 2.5}, "n must be between"),
    ("grid10", {"top": 5}, "unknown parameter"),
    ("tranche", {"deploy_cash": 1}, "true/false"),
    ("tranche", {"max_w": 2}, "max_w must be between"),
    ("grid10", [], "params must be an object"),
])
def test_validate_book_rejects(kind, params, msg):
    errs = validate_book(kind, params)
    assert errs and any(msg in e for e in errs), errs
