"""ATH 5% and 10% bands, uncapped, across all six universes.

Tests whether the band width that worked in the 2020-2026 sweep (+2.5 to +2.7pp)
survives the 2022-2026 window. Identical setup to the other two sweeps so rows merge.
"""
import json, time, requests
BASE="http://127.0.0.1:3001"; OUT="_ath_tight_results.json"; YEARS=4.657
UNIVERSES=["NIFTY200","NIFTYLARGEMIDCAP250","NIFTYSMALLCAP250","NIFTY500",
           "NIFTYMICROCAP250","NIFTY500MICRO750"]
COMMON={"strategy_id":"strategy_1781939795919","start_date":"2022-01-01","end_date":"2026-08-28",
 "portfolio_base_value":100000,"rebalance_frequency":"monthly","rebalance_date":"first",
 "rebalance_type":"equal_weight","point_in_time_membership":True,"include_brokerage":True,
 "benchmark_symbol":"Nifty 50","momentum_ranking":"none","max_holdings":None,
 "primary_filter_take":"all","primary_filter_metric":"ath"}
res=[]; i=0; total=len(UNIVERSES)*2
for u in UNIVERSES:
    for band in (5.0,10.0):
        i+=1; lbl=f"ATH{band:g}"
        print(f"[{i}/{total}] {u} | {lbl}",flush=True); t=time.time()
        try:
            r=requests.post(f"{BASE}/api/simulation/run",
                json={**COMMON,"universe":u,"primary_filter_band":band},timeout=7200)
        except Exception as e:
            res.append({"universe":u,"variant":lbl,"error":str(e)[:150]})
            json.dump(res,open(OUT,"w"),indent=2); print("        ERROR",str(e)[:100],flush=True); continue
        el=time.time()-t
        if r.status_code!=200:
            res.append({"universe":u,"variant":lbl,"error":f"HTTP {r.status_code}"})
            json.dump(res,open(OUT,"w"),indent=2); print(f"        ERROR HTTP {r.status_code}",flush=True); continue
        sim=r.json()["simulation"]; s=sim["summary"]; c=[len(d.get("holdings") or []) for d in sim["results"]]
        pf=s.get("primary_filter") or {}
        row={"universe":u,"variant":lbl,"band":band,"total_return":s["total_return"],
          "cagr":round(((1+s["total_return"]/100)**(1/YEARS)-1)*100,2),
          "benchmark_return":s["benchmark_return"],"alpha":s["alpha"],
          "max_drawdown":s["max_drawdown"],"sharpe":s["sharpe_ratio"],
          "avg_held":round(sum(c)/len(c),1),"cash_days":sum(1 for x in c if x==0),
          "sessions":len(c),"pool_before":pf.get("avg_pool_before"),
          "pool_after":pf.get("avg_pool_after"),"days_emptied":pf.get("days_emptied"),"secs":round(el)}
        res.append(row); json.dump(res,open(OUT,"w"),indent=2)
        print(f"        CAGR {row['cagr']:>6.2f}%  Sharpe {row['sharpe']:>6.3f}  DD {row['max_drawdown']:>7.2f}%  "
              f"pool {row['pool_before']}->{row['pool_after']}  held {row['avg_held']:>5.1f}  "
              f"cash {row['cash_days']:>4}d  ({row['secs']}s)",flush=True)
print(f"DONE — {len(res)}/{total}",flush=True)
