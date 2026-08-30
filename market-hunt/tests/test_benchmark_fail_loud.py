"""Benchmark data must exist for a simulation run — never a silent alpha=CAGR.

An empty benchmark series used to flow through summarize() as benchmark_cagr=0,
so alpha silently equalled the strategy's own CAGR (observed on NIFTY100 runs
before "Nifty 100" prices were loaded). Both simulation paths must fail loud
with HTTP 400 instead.

Requires a live server: MH_API_BASE=http://127.0.0.1:3001
"""
import os
import pytest
import requests

pytestmark = pytest.mark.integration

BASE = os.environ.get("MH_API_BASE", "http://127.0.0.1:3001")

MISSING_BENCH = "NO_SUCH_INDEX__"


def test_wave_run_rejects_missing_benchmark_data():
    body = {
        "universe": "NIFTY50",
        "start_date": "2022-01-01",
        "end_date": "2022-06-30",
        "point_in_time_membership": True,
        "benchmark_symbol": MISSING_BENCH,
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
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:300]}"
    assert MISSING_BENCH in r.json().get("detail", ""), r.text[:300]


def test_stateless_run_rejects_missing_benchmark_data():
    body = {
        "strategy_id": "strategy_1781939795919",
        "universe": "NIFTY50",
        "start_date": "2022-01-01",
        "end_date": "2022-03-31",
        "point_in_time_membership": True,
        "benchmark_symbol": MISSING_BENCH,
    }
    r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=180)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:300]}"
    assert MISSING_BENCH in r.json().get("detail", ""), r.text[:300]
