"""Per-function timing of the indicator path on a real 5,354-bar series."""
import sys, time
from datetime import datetime
import numpy as np
from pymongo import MongoClient
sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
from settings import MONGO_URI, DB_NAME
from indicator_engine import TrueValueXHelper as H, calculate_truevx_ranking

db = MongoClient(MONGO_URI)[DB_NAME]
COLLS = sorted(c for c in db.list_collection_names() if c.startswith("prices_"))
def bars(sym):
    rows=[]
    for c in COLLS:
        rows += list(db[c].find({"symbol": sym}, {"_id":0,"date":1,"open_price":1,
                     "high_price":1,"low_price":1,"close_price":1}))
    seen,out=set(),[]
    for r in sorted(rows,key=lambda x:x["date"]):
        d=r["date"].date()
        if d in seen or r.get("close_price") is None: continue
        seen.add(d)
        out.append({"date":r["date"].strftime("%Y-%m-%d"),
                    "open_price":float(r.get("open_price") or r["close_price"]),
                    "high_price":float(r.get("high_price") or r["close_price"]),
                    "low_price":float(r.get("low_price") or r["close_price"]),
                    "close_price":float(r["close_price"])})
    return out

bench=bars("Nifty 50"); tgt=bars("RELIANCE")
n=min(len(tgt),len(bench))
h=np.array([r["high_price"] for r in tgt],float)[-n:]/np.array([r["high_price"] for r in bench],float)[-n:]
l=np.array([r["low_price"] for r in tgt],float)[-n:]/np.array([r["low_price"] for r in bench],float)[-n:]
c=np.array([r["close_price"] for r in tgt],float)[-n:]/np.array([r["close_price"] for r in bench],float)[-n:]
print(f"series length {n}\n")
def T(name, fn, rep=3):
    t=time.perf_counter()
    for _ in range(rep): fn()
    el=(time.perf_counter()-t)/rep*1000
    print(f"  {name:<26}{el:>9.1f} ms"); return el
tot=0
tot+=T("dynamic_fib(22)",  lambda: H.dynamic_fib(h,l,22))
tot+=T("dynamic_fib(66)",  lambda: H.dynamic_fib(h,l,66))
tot+=T("dynamic_fib(222)", lambda: H.dynamic_fib(h,l,222))
tot+=T("ema(3)",           lambda: H.ema(c,3))
tot+=T("sma(222)",         lambda: H.sma(c,222))
tot+=T("is_rising(2)",     lambda: H.is_rising(c,2))
tot+=T("get_trend_color(2)",lambda: H.get_trend_color(c,2))
lvl=H.ema(H.dynamic_fib(h,l,66),3); dbv=np.full_like(c,0.02)
tot+=T("vote_scaled",      lambda: H.vote_scaled(c,lvl,dbv))
print(f"  {'-'*35}\n  {'sum of primitives':<26}{tot:>9.1f} ms\n")
t=time.perf_counter(); calculate_truevx_ranking(tgt,bench); full=(time.perf_counter()-t)*1000
print(f"  {'FULL calculate_truevx_ranking':<26}{full:>9.1f} ms  (one symbol)")
print(f"\n  extrapolated: 700 symbols = {full*700/1000:.0f}s")
