import os
import time
import pytest
import requests

BASE = os.environ.get("MH_API_BASE", "http://localhost:3001")
pytestmark = pytest.mark.integration


def _a_strategy_id():
    r = requests.get(f"{BASE}/api/simulation/strategies", timeout=10)
    strategies = r.json().get("strategies", [])
    assert strategies, "seed at least one strategy first"
    return strategies[0]["id"]


def _poll_run(pid: str, params: dict, max_tries: int = 150, interval: float = 2.0) -> dict:
    """POST /run, then poll run-status until no longer 'running'. Returns final status payload."""
    r = requests.post(f"{BASE}/api/portfolio/{pid}/run", params=params, timeout=30)
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    job_id = r.json()["job_id"]
    for _ in range(max_tries):
        time.sleep(interval)
        sr = requests.get(f"{BASE}/api/portfolio/run-status/{job_id}", timeout=10)
        assert sr.status_code == 200, sr.text
        data = sr.json()
        if data["status"] != "running":
            return data
    raise AssertionError(f"Job {job_id} still running after {max_tries * interval}s")


def test_portfolio_crud_roundtrip():
    sid = _a_strategy_id()
    body = {
        "name": "Test Book", "inception_date": "2024-01-01", "total_capital": 1000000,
        "sleeves": [{"id": "s1", "label": "All", "strategy_id": sid, "weight": 100,
                     "universe": "NIFTY500"}],
    }
    created = requests.post(f"{BASE}/api/portfolio", json=body, timeout=15).json()
    assert created["success"]
    pid = created["portfolio_id"]
    try:
        got = requests.get(f"{BASE}/api/portfolio/{pid}", timeout=10).json()
        assert got["portfolio"]["name"] == "Test Book"
        assert any(p["id"] == pid for p in
                   requests.get(f"{BASE}/api/portfolio", timeout=10).json()["portfolios"])
    finally:
        assert requests.delete(f"{BASE}/api/portfolio/{pid}", timeout=10).json()["success"]


def test_portfolio_rejects_bad_weights():
    sid = _a_strategy_id()
    body = {"name": "Bad", "inception_date": "2024-01-01",
            "sleeves": [{"id": "s1", "label": "A", "strategy_id": sid, "weight": 80,
                         "universe": "NIFTY500"}]}
    resp = requests.post(f"{BASE}/api/portfolio", json=body, timeout=10)
    assert resp.status_code == 400 and "sum to 100" in resp.text


def test_portfolio_run_composes_curve_and_book():
    sid = _a_strategy_id()
    body = {"name": "Run Book", "inception_date": "2024-01-01", "total_capital": 1000000,
            "point_in_time_membership": True,
            "sleeves": [{"id": "s1", "label": "All", "strategy_id": sid, "weight": 100,
                         "universe": "NIFTY500"}]}
    pid = requests.post(f"{BASE}/api/portfolio", json=body, timeout=15).json()["portfolio_id"]
    try:
        job = _poll_run(pid, {"end_date": "2024-06-30"})
        assert job["status"] == "completed", f"Job failed: {job['error']}"
        data = job["result"]
        assert data["success"]
        assert len(data["curve"]) > 0
        assert {"portfolio_value", "benchmark_value", "date"} <= data["curve"][0].keys()
        assert {"total_return", "alpha", "sharpe_ratio"} <= data["summary"].keys()
        assert data["sleeves"][0]["weight"] == 100
    finally:
        requests.delete(f"{BASE}/api/portfolio/{pid}", timeout=10)


def test_portfolio_run_start_date_override():
    sid = _a_strategy_id()
    body = {"name": "Window Book", "inception_date": "2024-01-01", "total_capital": 1000000,
            "point_in_time_membership": True,
            "sleeves": [{"id": "s1", "label": "All", "strategy_id": sid, "weight": 100,
                         "universe": "NIFTY500"}]}
    pid = requests.post(f"{BASE}/api/portfolio", json=body, timeout=15).json()["portfolio_id"]
    try:
        # start_date override later than stored inception → curve starts on/after the override
        job = _poll_run(pid, {"start_date": "2024-03-01", "end_date": "2024-06-30"})
        assert job["status"] == "completed", f"Job failed: {job['error']}"
        data = job["result"]
        assert data["curve"][0]["date"] >= "2024-03-01"
        assert data["portfolio"]["inception_date"] >= "2024-03-01"
        # invalid: start before 2022 floor → 400 synchronously (pre-job validation)
        bad = requests.post(f"{BASE}/api/portfolio/{pid}/run",
                            params={"start_date": "2021-01-01"}, timeout=30)
        assert bad.status_code == 400
    finally:
        requests.delete(f"{BASE}/api/portfolio/{pid}", timeout=10)
