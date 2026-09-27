import requests, time
BASE="http://127.0.0.1:3001"
# exactly what run/page.tsx posts for that URL
UI={"strategy_id":"strategy_1781939795919","portfolio_base_value":100000,
 "rebalance_date":"first","rebalance_type":"equal_weight","universe":"NIFTY500",
 "benchmark_symbol":"Nifty 50","max_holdings":None,"momentum_ranking":"none",
 "primary_filter_take":"all","primary_filter_metric":"ath","primary_filter_band":5,
 "start_date":"2020-01-01","end_date":"2026-08-30","point_in_time_membership":True,
 "side":"long","annual_borrow_rate":0,"regime_gate":False,
 "gate_symbol":"Nifty 500","gate_sma":200}
for lbl,extra in [("your URL (quarterly, end 08-30)",{"rebalance_frequency":"quarterly"}),
                  ("sweep row 3 (monthly, end 08-30)",{"rebalance_frequency":"monthly"})]:
    t=time.time(); r=requests.post(f"{BASE}/api/simulation/run",json={**UI,**extra},timeout=5400)
    s=r.json()["simulation"]["summary"]; pf=s.get("primary_filter") or {}
    print(f"{lbl:<34} totRet {s['total_return']:>7.2f}%  Sharpe {s['sharpe_ratio']:>6.3f}  "
          f"DD {s['max_drawdown']:>7.2f}%  rebals {s['total_trades']:>3}  "
          f"pool {pf.get('avg_pool_before')}->{pf.get('avg_pool_after')}  ({time.time()-t:.0f}s)",flush=True)
