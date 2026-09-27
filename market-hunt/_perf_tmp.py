"""Same run 3x back-to-back against a freshly restarted backend.
If durations climb, the cost is process state (leak/GC/pool), not the workload."""
import time, requests
BASE="http://127.0.0.1:3001"
BODY={"strategy_id":"strategy_1781939795919","universe":"NIFTY500",
 "start_date":"2020-01-01","end_date":"2026-08-28","portfolio_base_value":100000,
 "rebalance_frequency":"monthly","rebalance_date":"first","rebalance_type":"equal_weight",
 "point_in_time_membership":True,"include_brokerage":True,"benchmark_symbol":"Nifty 50",
 "momentum_ranking":"none","max_holdings":None,"primary_filter_take":"none"}
for i in range(1,4):
    t=time.time(); r=requests.post(f"{BASE}/api/simulation/run",json=BODY,timeout=5400)
    print(f"run {i}: HTTP {r.status_code}  {time.time()-t:.0f}s", flush=True)
