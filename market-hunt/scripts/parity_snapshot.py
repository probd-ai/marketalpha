"""Refactor parity harness: snapshot GET endpoints, then diff after a code move.

Usage (server running on MH_API_PORT or :3001):
  python scripts/parity_snapshot.py capture baseline.json
  ... refactor ...
  python scripts/parity_snapshot.py compare baseline.json

Volatile keys (timestamps, durations) are stripped recursively before
hashing so only real payload changes fail the diff. Exit 0 = identical.
"""
import json
import re
import sys
import urllib.request

BASE = "http://127.0.0.1:3001"

# Read-only endpoints covering every router that migrates out of the monolith.
ENDPOINTS = [
    "/health",
    "/api/urls",
    "/api/urls?active_only=true",
    "/api/data",
    "/api/data/universes",
    "/api/data/index/NIFTY50",
    "/api/data/index/NIFTY50/industries",
    "/api/industries",
    "/api/stock/mappings?limit=50",
    "/api/stock/statistics",
    "/api/stock/available?limit=50",
    "/api/stock/indicators/supported",
    "/api/indicators/stored",
    "/api/indicators/batch",
    "/api/analytics/strength-distribution?universe=NIFTY50&date=2026-06-30",
    "/api/analytics/price-return-distribution?universe=NIFTY50&start=2026-06-01&end=2026-06-30",
    "/api/analytics/strength-distribution-all?universe=NIFTY50&start=2026-01-01&end=2026-06-30",
    "/api/analytics/strength-distribution-long?universe=NIFTY50&start=2026-01-01&end=2026-06-30&windows=222",
    "/api/analytics/index-distribution?index_symbol=NIFTY50&start_date=2026-06-01&end_date=2026-06-30&include_price=false",
    "/api/analytics/index-distribution/symbols?index_symbol=NIFTY50&date=2026-06-30",
    # POST endpoints: (method, path, json_body). Windows chosen short for runtime.
    ("POST", "/api/simulation/run", {
        "strategy_id": "strategy_1781939795919", "universe": "NIFTY50",
        "start_date": "2026-01-01", "end_date": "2026-03-31",
        "point_in_time_membership": True}),
    ("POST", "/api/simulation/run", {
        "strategy_id": "strategy_1781970939024", "universe": "NIFTY50",
        "start_date": "2026-01-01", "end_date": "2026-03-31", "max_holdings": 5,
        "rebalance_type": "skewed", "momentum_ranking": "truevx_roc",
        "point_in_time_membership": True}),
    ("POST", "/api/simulation/run", {
        "strategy_id": "strategy_1781970939024", "universe": "NIFTY50",
        "start_date": "2026-01-01", "end_date": "2026-03-31", "side": "short",
        "annual_borrow_rate": 0.04, "point_in_time_membership": True}),
    ("POST", "/api/simulation/run", {
        "strategy_id": "strategy_1781970939024", "universe": "NIFTY50",
        "start_date": "2026-01-01", "end_date": "2026-03-31",
        "regime_gate": True, "gate_mode": "dynamic_fib",
        "point_in_time_membership": True}),
    ("POST", "/api/simulation/run", {
        "universe": "NIFTY50", "start_date": "2026-01-01", "end_date": "2026-03-31",
        "point_in_time_membership": True,
        "wave": {"capital_N": 1000000, "tranches": 20, "buy_cadence": "daily",
                 "exit_mode": "full",
                 "entry": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
                 "exit": [[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "down"}]]}}),
    ("POST", "/api/simulation/debug", {
        "strategy_id": "strategy_1781939795919", "universe": "NIFTY50",
        "start_date": "2026-02-01", "end_date": "2026-03-31",
        "point_in_time_membership": True}),
    ("POST", "/api/simulation/estimate-charges", {
        "strategy_id": "strategy_1781970939024", "universe": "NIFTY50",
        "start_date": "2026-01-01", "end_date": "2026-03-31"}),
    "/api/simulation/charge-rates",
    "/api/simulation/strategies",
    "/api/portfolio",
    "/api/portfolio/portfolio_1782072903878",
    "/api/portfolio/portfolio_1782072903878/preview",
    "/api/portfolio/portfolio_1782072903878/last-run",
    "/api/portfolio/portfolio_1783106608552/preview",
    # Broker endpoints that stay local (no live-broker calls, no credentials leaked)
    "/api/broker/status",
    "/api/broker-mapping",
]

_VOLATILE = re.compile(r"(timestamp|generated_at|ran_at|elapsed|duration_ms|last_update|last_updated|last_calculated|trade_id|execution_time.*|calculation_time.*)$", re.I)


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in sorted(obj.items()) if not _VOLATILE.search(k)}
    if isinstance(obj, list):
        return [_strip(x) for x in obj]
    return obj


def _canon(obj):
    """Order-insensitive canonical form: Mongo $group emits tied rows in
    nondeterministic order, so sort dict-lists by their canonical dump."""
    if isinstance(obj, dict):
        return {k: _canon(v) for k, v in obj.items()}
    if isinstance(obj, list):
        canoned = [_canon(x) for x in obj]
        try:
            return sorted(canoned, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        except TypeError:
            return canoned
    return obj


def _key(ep) -> str:
    """Stable snapshot key for an endpoint spec (string GET or (method, path, body))."""
    if isinstance(ep, str):
        return ep
    method, path, body = ep
    return f"{method} {path} {json.dumps(body, sort_keys=True)}"


def fetch(ep):
    if isinstance(ep, str):
        req = urllib.request.Request(BASE + ep)
    else:
        method, path, body = ep
        req = urllib.request.Request(
            BASE + path, method=method,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, _strip(json.loads(r.read()))
    except urllib.error.HTTPError as e:
        try:
            body = _strip(json.loads(e.read()))
        except Exception:
            body = None
        return e.code, body


def capture(out_path: str) -> None:
    snap = {}
    for ep in ENDPOINTS:
        status, body = fetch(ep)
        snap[_key(ep)] = {"status": status, "body": body}
        print(f"  {status} {_key(ep)[:100]}")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=1, sort_keys=True, default=str)
    print(f"captured {len(snap)} endpoints -> {out_path}")


def compare(baseline_path: str) -> int:
    with open(baseline_path, encoding="utf-8") as f:
        baseline = json.load(f)
    failures = []
    for ep in ENDPOINTS:
        k = _key(ep)
        if k not in baseline:
            print(f"  SKIP {k[:100]} (not in baseline)")
            continue
        expect = _canon(_strip(baseline[k]))  # re-strip: volatile list may grow after capture
        status, body = fetch(ep)
        got = _canon(json.loads(json.dumps({"status": status, "body": body}, default=str)))
        if got != expect:
            failures.append(k)
            print(f"  DIFF {k[:100]} (status {expect['status']} -> {status})")
        else:
            print(f"  OK   {k[:100]}")
    if failures:
        print(f"PARITY FAILED: {len(failures)}/{len(baseline)} endpoints differ")
        return 1
    print(f"PARITY OK: {len(baseline)} endpoints identical")
    return 0


if __name__ == "__main__":
    mode, path = sys.argv[1], sys.argv[2]
    if mode == "capture":
        capture(path)
    elif mode == "compare":
        sys.exit(compare(path))
    else:
        sys.exit(f"unknown mode {mode}")
