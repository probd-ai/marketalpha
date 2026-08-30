"""Regime gate on the stateless simulation path.

regime_gate=true flattens the book to cash while the gate index closes below its
N-day SMA (default: Nifty 500, 200d) and re-enters the day it recovers. Long-only.

Requires a live server: MH_API_BASE=http://127.0.0.1:3001
"""
import os
import pytest
import requests

pytestmark = pytest.mark.integration

BASE = os.environ.get("MH_API_BASE", "http://127.0.0.1:3001")

BODY = {
    "strategy_id": "strategy_1781939795919",
    "universe": "NIFTY50",
    "start_date": "2020-01-01",
    "end_date": "2020-12-31",
    "portfolio_base_value": 100000,
    "point_in_time_membership": True,
    "include_brokerage": True,
}


def test_gated_run_flattens_during_covid_and_reports_gated_days():
    body = {**BODY, "regime_gate": True, "gate_symbol": "Nifty 50", "gate_sma": 200}
    r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=600)
    r.raise_for_status()
    sim = r.json()["simulation"]
    s = sim["summary"]
    assert s.get("gated_days", 0) > 20, f"expected COVID-2020 gating, got {s.get('gated_days')}"
    # While gated (April 2020 was firmly below the 200d SMA) the book must hold nothing.
    april = [d for d in sim["results"] if d["date"].startswith("2020-04")]
    assert april, "no April 2020 days in results"
    assert all(len(d.get("holdings", [])) == 0 for d in april), (
        f"holdings present while gated: {[(d['date'], len(d['holdings'])) for d in april if d['holdings']][:3]}")


def test_ungated_run_reports_zero_gated_days_and_holds_through():
    r = requests.post(f"{BASE}/api/simulation/run", json=BODY, timeout=600)
    r.raise_for_status()
    sim = r.json()["simulation"]
    assert sim["summary"].get("gated_days", 0) == 0
    april = [d for d in sim["results"] if d["date"].startswith("2020-04")]
    assert any(len(d.get("holdings", [])) > 0 for d in april), "ungated run should hold in April 2020"


def test_gate_rejects_short_side():
    body = {**BODY, "regime_gate": True, "side": "short"}
    r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=600)
    assert r.status_code == 400, f"expected 400 for gate+short, got {r.status_code}"


def test_gate_rejects_missing_gate_symbol_data():
    body = {**BODY, "regime_gate": True, "gate_symbol": "NO_SUCH_GATE__"}
    r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=600)
    assert r.status_code == 400, f"expected 400, got {r.status_code}"
