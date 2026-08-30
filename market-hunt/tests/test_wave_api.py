"""Integration tests for /api/simulation/run wave routing.

Requires a live server with loaded market_hunt DB.
Run with:
    MH_API_BASE=http://127.0.0.1:3001 python -m pytest tests/test_wave_api.py -v -m integration

These tests assert response shape (200 + key presence), NOT exact numbers.
Numeric baselining is a documented follow-up once the endpoint is in production.
"""
import os
import pytest
import requests

pytestmark = pytest.mark.integration

BASE = os.environ.get("MH_API_BASE", "http://127.0.0.1:3001")


def test_stateless_run_unchanged_when_no_wave():
    """Stateless path must be unaffected when no wave block is sent.

    Uses strategy_1781939795919 (the aligned-stack champion, 4 rules, N100).
    Asserts the response shape: simulation.summary carries total_return and sharpe_ratio.
    """
    body = {
        "strategy_id": "strategy_1781939795919",
        "universe": "NIFTY50",
        "start_date": "2022-01-01",
        "end_date": "2022-06-30",
        "point_in_time_membership": True,
    }
    r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=120)
    r.raise_for_status()
    j = r.json()
    assert j.get("success") is True
    s = j["simulation"]["summary"]
    # Stateless summary keys (stateless path does not produce "cagr"; it produces "total_return")
    assert "total_return" in s, f"expected total_return in summary, got keys: {list(s.keys())}"
    assert "sharpe_ratio" in s, f"expected sharpe_ratio in summary, got keys: {list(s.keys())}"
    # Wave key must NOT be present on a stateless run
    assert "wave_diagnostics" not in j["simulation"], "wave_diagnostics leaked into stateless response"


def test_wave_block_routes_to_wave_engine():
    """When a wave block is present, the request must route to the wave engine.

    Asserts that the response contains wave_diagnostics and summary with cagr + sharpe_ratio
    (wave engine summarize() produces cagr, not total_return).
    """
    body = {
        "universe": "NIFTY50",
        "start_date": "2022-01-01",
        "end_date": "2022-12-31",
        "point_in_time_membership": True,
        "wave": {
            "capital_N": 1000000,
            "tranches": 20,
            "buy_cadence": "daily",
            "exit_mode": "full",
            "entry": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
            "exit": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "down"}]],
        },
    }
    r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=180)
    r.raise_for_status()
    j = r.json()
    assert j.get("success") is True
    sim = j["simulation"]
    assert "wave_diagnostics" in sim, f"missing wave_diagnostics; keys: {list(sim.keys())}"
    assert "summary" in sim, f"missing summary; keys: {list(sim.keys())}"
    s = sim["summary"]
    assert "cagr" in s, f"expected cagr in wave summary, got: {list(s.keys())}"
    assert "sharpe_ratio" in s, f"expected sharpe_ratio in wave summary, got: {list(s.keys())}"
    assert "results" in sim, "missing results list in wave response"


def test_saved_wave_strategy_runs_as_wave_by_id():
    """A saved strategy carrying a wave block must execute as a WAVE when run by
    strategy_id alone (no wave block in the request) — the run handler must read
    the strategy's persisted wave, not require the caller to resend it."""
    wave = {
        "capital_N": 1000000, "tranches": 20, "reentry": True,
        "buy_cadence": "daily", "exit_mode": "full",
        "entry": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
        "accumulate": [],
        "exit": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "down"}]],
    }
    body = {"name": "saved-wave-run", "description": "t", "rules": [], "wave": wave}
    sid = requests.post(f"{BASE}/api/simulation/strategies", json=body, timeout=30).json()["id"]
    try:
        run = {
            "strategy_id": sid, "universe": "NIFTY50",
            "start_date": "2023-01-01", "end_date": "2023-06-30",
            "point_in_time_membership": True,
        }
        r = requests.post(f"{BASE}/api/simulation/run", json=run, timeout=180)
        r.raise_for_status()
        sim = r.json()["simulation"]
        assert "wave_diagnostics" in sim, (
            "saved wave strategy ran STATELESS when invoked by id; "
            f"sim keys: {list(sim.keys())}")
        assert "cagr" in sim["summary"]
    finally:
        requests.delete(f"{BASE}/api/simulation/strategies/{sid}", timeout=30)


def test_rich_filter_equivalent_to_legacy_rules():
    """A rich `filter` of compare-vs-number signals must produce an IDENTICAL summary
    to the legacy `rules` it mirrors — proves the filter path reproduces
    apply_strategy_rules (so legacy->filter conversion is safe)."""
    run = dict(universe="NIFTY50", start_date="2022-01-01", end_date="2022-12-31",
               point_in_time_membership=True, portfolio_base_value=100000, max_holdings=None,
               momentum_ranking="20_day_return")
    # legacy rules strategy
    rules = [{"id": "r1", "metric": "mean_long", "operator": ">", "threshold": 50, "name": "a"},
             {"id": "r2", "metric": "mean_long", "operator": "<", "threshold": 60, "name": "b"}]
    sid_rules = requests.post(f"{BASE}/api/simulation/strategies",
                              json={"name": "eq-rules", "description": "t", "rules": rules}, timeout=30).json()["id"]
    # equivalent filter strategy
    flt = [[{"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}, "negate": False},
            {"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 60}, "negate": False}]]
    sid_flt = requests.post(f"{BASE}/api/simulation/strategies",
                            json={"name": "eq-filter", "description": "t", "rules": [], "filter": flt}, timeout=30).json()["id"]
    try:
        a = requests.post(f"{BASE}/api/simulation/run", json={**run, "strategy_id": sid_rules}, timeout=180).json()["simulation"]["summary"]
        b = requests.post(f"{BASE}/api/simulation/run", json={**run, "strategy_id": sid_flt}, timeout=180).json()["simulation"]["summary"]
        assert round(a["total_return"], 4) == round(b["total_return"], 4), (a["total_return"], b["total_return"])
        assert round(a["max_drawdown"], 4) == round(b["max_drawdown"], 4)
    finally:
        requests.delete(f"{BASE}/api/simulation/strategies/{sid_rules}", timeout=30)
        requests.delete(f"{BASE}/api/simulation/strategies/{sid_flt}", timeout=30)


def test_filter_metric_vs_metric_and_last_cross_dir_run():
    """New filter capabilities run end-to-end: metric-vs-metric compare and last_cross_dir."""
    run = dict(universe="NIFTY50", start_date="2022-01-01", end_date="2022-12-31",
               point_in_time_membership=True, portfolio_base_value=100000, max_holdings=None,
               momentum_ranking="20_day_return")
    flt_mvm = [[{"kind": "compare", "lhs_metric": "truevx_score", "op": "<", "rhs": {"metric": "mean_long"}, "negate": False}]]
    flt_lcd = [[{"kind": "last_cross_dir", "metric": "mean_long", "grid": [50, 60, 70], "direction": "up", "negate": False}]]
    sids = []
    try:
        for name, f in (("mvm", flt_mvm), ("lcd", flt_lcd)):
            sid = requests.post(f"{BASE}/api/simulation/strategies",
                                json={"name": name, "description": "t", "rules": [], "filter": f}, timeout=30).json()["id"]
            sids.append(sid)
            r = requests.post(f"{BASE}/api/simulation/run", json={**run, "strategy_id": sid}, timeout=180)
            r.raise_for_status()
            sim = r.json()["simulation"]
            assert "summary" in sim and "total_return" in sim["summary"]
            assert "wave_diagnostics" not in sim, "filter strategy must run stateless, not as a wave"
    finally:
        for sid in sids:
            requests.delete(f"{BASE}/api/simulation/strategies/{sid}", timeout=30)


def test_strategy_crud_roundtrips_wave_block():
    """POST a strategy with a wave block, GET it back, assert wave.tranches == 20, then DELETE."""
    wave = {
        "capital_N": 1000000,
        "tranches": 20,
        "buy_cadence": "daily",
        "exit_mode": "full",
        "entry": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
        "exit": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "down"}]],
    }
    body = {"name": "wave-roundtrip", "description": "t", "rules": [], "wave": wave}
    r = requests.post(f"{BASE}/api/simulation/strategies", json=body, timeout=30)
    r.raise_for_status()
    sid = r.json()["id"]
    got = next(s for s in requests.get(f"{BASE}/api/simulation/strategies", timeout=30).json()["strategies"] if s["id"] == sid)
    assert got["wave"]["tranches"] == 20
    requests.delete(f"{BASE}/api/simulation/strategies/{sid}", timeout=30)
