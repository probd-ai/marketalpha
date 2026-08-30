import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.compute_indicators import resolve_universe, filter_from_date, should_compute, DEFAULTS


def test_resolve_universe_drops_benchmark_and_dedups_sorts():
    out = resolve_universe(["TCS", "RELIANCE", "Nifty 50", "TCS"], benchmark="Nifty 50")
    assert out == ["RELIANCE", "TCS"]


def test_filter_from_date_keeps_on_or_after_floor():
    rows = [{"date": "2017-12-29"}, {"date": "2018-01-01"}, {"date": "2018-06-10"}]
    assert filter_from_date(rows, "2018-01-01") == [{"date": "2018-01-01"}, {"date": "2018-06-10"}]


def test_should_compute_respects_force_and_existing():
    assert should_compute(0, False) is True
    assert should_compute(5, False) is False
    assert should_compute(5, True) is True


def test_defaults_match_locked_params():
    assert DEFAULTS == {"s1": 22, "m2": 66, "l3": 222, "strength": 2,
                        "w_short": 0.5, "w_mid": 1.0, "w_long": 1.5,
                        "deadband_frac": 0.02, "min_deadband": 0.001}
