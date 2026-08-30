from datetime import date
from walkforward.folds import Fold
from walkforward.sim_runner import RunResult
from walkforward.walk_forward import FoldResult
from walkforward.report import verdict, stability_flag


def test_verdict_robust_optimized():
    assert verdict({"sharpe": 1.2, "cagr": 0.25}, {"sharpe": 1.0, "cagr": 0.22},
                   {"sharpe": 0.6, "cagr": 0.12}) == "ROBUST_OPTIMIZED"


def test_verdict_robust_fixed_when_adaptive_loses_to_baseline():
    assert verdict({"sharpe": 0.9, "cagr": 0.20}, {"sharpe": 1.1, "cagr": 0.23},
                   {"sharpe": 0.6, "cagr": 0.12}) == "ROBUST_FIXED"


def test_verdict_no_edge_when_baseline_trails_benchmark():
    assert verdict({"sharpe": 1.2, "cagr": 0.10}, {"sharpe": 1.0, "cagr": 0.11},
                   {"sharpe": 0.9, "cagr": 0.13}) == "NO_EDGE"


def _fr(idx, cfg):
    f = Fold(date(2018 + idx, 1, 1), date(2020 + idx, 12, 31),
             date(2021 + idx, 1, 1), date(2021 + idx, 12, 31))
    rr = RunResult(0.2, 1.0, -10.0, 5.0, 20.0, [], 0)
    return FoldResult(f, cfg, rr, rr, rr)


def test_stability_flag():
    same = [_fr(i, (20, "price_roc_66d")) for i in range(4)]
    assert stability_flag(same) == "STABLE"
    mixed = [_fr(i, (n, "price_roc_66d")) for i, n in enumerate([10, 20, 30, 25])]
    assert stability_flag(mixed) == "THRASHING"
