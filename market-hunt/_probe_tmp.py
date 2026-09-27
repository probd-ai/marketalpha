import time, requests
t = time.time()
r = requests.post("http://127.0.0.1:3001/api/simulation/run", json={
    "strategy_id": "strategy_1781939795919", "universe": "NIFTY500",
    "start_date": "2020-01-01", "end_date": "2026-08-28",
    "portfolio_base_value": 100000, "rebalance_frequency": "monthly",
    "rebalance_date": "first", "rebalance_type": "equal_weight",
    "point_in_time_membership": True, "include_brokerage": True,
    "benchmark_symbol": "Nifty 50",
    "momentum_ranking": "none", "max_holdings": None,
    "primary_filter_take": "none",
}, timeout=5400)
el = time.time() - t
print("HTTP", r.status_code, f"{el:.0f}s")
if r.status_code == 200:
    s = r.json()["simulation"]["summary"]
    print("return", s["total_return"], "bench", s["benchmark_return"], "sharpe", s["sharpe_ratio"], "dd", s["max_drawdown"])
else:
    print(str(r.json().get("detail"))[:300])
