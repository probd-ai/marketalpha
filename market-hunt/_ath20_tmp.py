import json, time, requests
BASE="http://127.0.0.1:3001"; YEARS=6.657
COMMON={"strategy_id":"strategy_1781939795919","universe":"NIFTY500",
 "start_date":"2020-01-01","end_date":"2026-08-28","portfolio_base_value":100000,
 "rebalance_frequency":"monthly","rebalance_date":"first","rebalance_type":"equal_weight",
 "point_in_time_membership":True,"include_brokerage":True,"benchmark_symbol":"Nifty 50",
 "primary_filter_take":"all","primary_filter_metric":"ath","primary_filter_band":20.0}
CELLS=[("none",{"momentum_ranking":"none","max_holdings":None}),
       ("22d/30",{"momentum_ranking":"price_roc_22d","max_holdings":30})]
out=[]
for lbl,mom in CELLS:
    print(f"running ATH 20% all + {lbl} ...",flush=True); t=time.time()
    r=requests.post(f"{BASE}/api/simulation/run",json={**COMMON,**mom},timeout=5400); el=time.time()-t
    if r.status_code!=200: print("  ERROR",r.status_code,str(r.json().get('detail'))[:200],flush=True); continue
    sim=r.json()["simulation"]; s=sim["summary"]; days=sim["results"]
    c=[len(d.get("holdings") or []) for d in days]; pf=s.get("primary_filter") or {}
    row={"primary":"ATH 20% all","momentum":lbl,"total_return":s["total_return"],
      "cagr":round(((1+s["total_return"]/100)**(1/YEARS)-1)*100,2),"benchmark_return":s["benchmark_return"],
      "alpha":s["alpha"],"max_drawdown":s["max_drawdown"],"sharpe":s["sharpe_ratio"],
      "avg_held":round(sum(c)/len(c),1),"max_held":max(c),"cash_days":sum(1 for x in c if x==0),
      "sessions":len(c),"pool_before":pf.get("avg_pool_before"),"pool_after":pf.get("avg_pool_after"),
      "days_emptied":pf.get("days_emptied"),"secs":round(el)}
    out.append(row); json.dump(out,open("_ath20_results.json","w"),indent=2)
    print(f"  CAGR {row['cagr']:>6.2f}  Sharpe {row['sharpe']:>6.3f}  DD {row['max_drawdown']:>7.2f}  "
          f"pool {row['pool_before']}->{row['pool_after']}  held {row['avg_held']}  cash {row['cash_days']}d  ({row['secs']}s)",flush=True)
print("DONE",flush=True)
