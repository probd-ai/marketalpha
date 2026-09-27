import requests, time
BASE="http://127.0.0.1:3001"
FILTER=[[{"kind":"compare","lhs_metric":"mean_short","op":">","rhs":{"metric":"mean_long"}}]]
body={"strategy_id":"strategy_1788028291660","universe":"NIFTY200",
 "start_date":"2022-01-01","end_date":"2026-08-28","portfolio_base_value":100000,
 "rebalance_frequency":"monthly","rebalance_date":"first","rebalance_type":"equal_weight",
 "point_in_time_membership":True,"include_brokerage":True,"benchmark_symbol":"Nifty 50",
 "momentum_ranking":"none","max_holdings":None,"primary_filter_take":"none","filter":FILTER}
t=time.time(); r=requests.post(f"{BASE}/api/simulation/run",json=body,timeout=3600)
print("HTTP",r.status_code, f"{time.time()-t:.0f}s")
if r.status_code==200:
    sim=r.json()["simulation"]; s=sim["summary"]
    held=[len(d.get("holdings") or []) for d in sim["results"]]
    print(f"  totRet {s['total_return']:.2f}%  bench {s['benchmark_return']:.2f}%  "
          f"Sharpe {s['sharpe_ratio']:.3f}  DD {s['max_drawdown']:.2f}%  avg held {sum(held)/len(held):.1f}")
else:
    print(str(r.json().get('detail'))[:400])
