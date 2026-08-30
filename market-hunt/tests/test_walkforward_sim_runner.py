from walkforward.sim_runner import build_run_result, RunResult


def _sample():
    # ~1 year, doubling: 100000 -> 200000
    return {
        "summary": {"total_return": 100.0, "sharpe_ratio": 1.5,
                    "max_drawdown": -20.0, "alpha": 60.0},
        "results": [
            {"date": "2021-01-01", "portfolio_value": 100000.0},
            {"date": "2021-07-01", "portfolio_value": 150000.0},
            {"date": "2022-01-01", "portfolio_value": 200000.0},
        ],
    }


def test_build_run_result_maps_summary_and_equity():
    r = build_run_result(_sample())
    assert isinstance(r, RunResult)
    assert r.sharpe == 1.5
    assert r.alpha == 60.0
    assert r.total_return == 100.0
    assert r.n_points == 3
    assert r.equity[0] == ("2021-01-01", 100000.0)
    assert r.equity[-1] == ("2022-01-01", 200000.0)


def test_build_run_result_cagr_is_annualized_from_dates():
    r = build_run_result(_sample())
    # doubled over ~1 year -> CAGR ~ 100%
    assert 0.95 < r.cagr < 1.05
