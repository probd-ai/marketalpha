import asyncio
from datetime import date
from walkforward.folds import Fold
from walkforward.sim_runner import RunResult
from walkforward.walk_forward import select_best, curve_metrics, stitch, run_walk_forward


def _rr(sharpe, equity=None, **kw):
    return RunResult(cagr=kw.get("cagr", 0.0), sharpe=sharpe,
                     max_drawdown=kw.get("mdd", -10.0), alpha=kw.get("alpha", 0.0),
                     total_return=kw.get("tr", 0.0), equity=equity or [], n_points=len(equity or []))


def test_select_best_picks_max_objective_and_skips_none():
    results = {(10, "20_day_return"): _rr(0.8), (20, "price_roc_66d"): _rr(1.3),
               (30, "price_roc_222d"): None}
    assert select_best(results, "sharpe") == (20, "price_roc_66d")


def test_curve_metrics_on_known_doubling_curve():
    eq = [("2021-01-01", 100000.0), ("2022-01-01", 200000.0)]
    m = curve_metrics(eq)
    assert abs(m["total_return"] - 100.0) < 1e-6
    assert 0.95 < m["cagr"] < 1.05


def test_stitch_compounds_segments():
    seg1 = [("2021-01-01", 100000.0), ("2021-12-31", 120000.0)]  # +20%
    seg2 = [("2022-01-01", 100000.0), ("2022-12-31", 110000.0)]  # +10%
    out = stitch([seg1, seg2])
    assert out[0] == ("2021-01-01", 100000.0)
    # final level = 100000 * 1.2 * 1.1 = 132000
    assert abs(out[-1][1] - 132000.0) < 1e-6


def test_run_walk_forward_selects_on_train_then_runs_oos():
    fold = Fold(date(2018, 1, 1), date(2020, 12, 31), date(2021, 1, 1), date(2021, 12, 31))

    class StubRunner:
        def __init__(self):
            self.calls = []
        async def run(self, start, end, n, window):
            self.calls.append((start, end, n, window))
            # train: favor n=20; oos/baseline: return a tagged sharpe
            if end == date(2020, 12, 31):
                return _rr(1.0 if n == 20 else 0.5)
            return _rr(2.0, equity=[("2021-01-01", 100000.0), ("2021-12-31", 130000.0)])

    runner = StubRunner()
    res = asyncio.run(run_walk_forward(runner, [fold], sizes=(10, 20), windows=("price_roc_66d",),
                                       baseline=(20, "price_roc_66d"), objective="sharpe"))
    assert len(res) == 1
    assert res[0].best_config == (20, "price_roc_66d")
    assert res[0].oos.sharpe == 2.0
    assert res[0].baseline_oos.sharpe == 2.0
