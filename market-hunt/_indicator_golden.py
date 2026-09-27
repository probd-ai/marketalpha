"""Oracle for the indicator vectorisation.

Captures the EXACT output of calculate_truevx_ranking plus every TrueValueXHelper
primitive, on real data, before any rewrite. Run once before and once after; the two
dumps must be byte-identical. The indicator is what every strategy in this repo depends
on, so nothing less than bit-equality is acceptable.

    python _indicator_golden.py before
    ...rewrite...
    python _indicator_golden.py after
"""
import hashlib
import json
import sys
from datetime import datetime

import numpy as np
from pymongo import MongoClient

sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
from settings import MONGO_URI, DB_NAME
from indicator_engine import TrueValueXHelper as H, calculate_truevx_ranking

TAG = sys.argv[1] if len(sys.argv) > 1 else "before"
OUT = f"_golden_indicator_{TAG}.json"
SYMS = ["RELIANCE", "TCS", "HDFCBANK", "ITC", "SBIN", "WIPRO", "BEL", "PAYTM"]
BENCH = "Nifty 50"
END = datetime(2026, 8, 28)

db = MongoClient(MONGO_URI)[DB_NAME]
COLLS = sorted(c for c in db.list_collection_names() if c.startswith("prices_"))


def bars(sym):
    rows = []
    for c in COLLS:
        rows += list(db[c].find({"symbol": sym, "date": {"$lte": END}},
                                {"_id": 0, "date": 1, "open_price": 1, "high_price": 1,
                                 "low_price": 1, "close_price": 1}))
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: x["date"]):
        d = r["date"].date()
        if d in seen or r.get("close_price") is None:
            continue
        seen.add(d)
        out.append({"date": r["date"].strftime("%Y-%m-%d"),
                    "open_price": float(r.get("open_price") or r["close_price"]),
                    "high_price": float(r.get("high_price") or r["close_price"]),
                    "low_price": float(r.get("low_price") or r["close_price"]),
                    "close_price": float(r["close_price"])})
    return out


def enc(a):
    """Full-precision, NaN-safe encoding so nothing hides in rounding."""
    return ["nan" if not np.isfinite(x) else float(np.float64(x)) for x in np.asarray(a, float)]


dump = {}
bench = bars(BENCH)
print(f"benchmark {BENCH}: {len(bench)} bars", flush=True)

# --- primitives, exercised on a real relative series ---
b = {k: np.array([r[k] for r in bench], float)
     for k in ("open_price", "high_price", "low_price", "close_price")}
t = bars("RELIANCE")
n = min(len(t), len(bench))
th = np.array([r["high_price"] for r in t], float)[-n:]
tl = np.array([r["low_price"] for r in t], float)[-n:]
tc = np.array([r["close_price"] for r in t], float)[-n:]
h = th / b["high_price"][-n:]
l = tl / b["low_price"][-n:]
c = tc / b["close_price"][-n:]

prim = {}
for lb in (22, 66, 222):
    prim[f"dynamic_fib_{lb}"] = enc(H.dynamic_fib(h, l, lb))
for p in (2, 3, 22):
    prim[f"ema_{p}"] = enc(H.ema(c, p))
for p in (22, 66, 222):
    prim[f"sma_{p}"] = enc(H.sma(c, p))
for s in (1, 2, 3):
    prim[f"is_rising_{s}"] = [bool(x) for x in H.is_rising(c, s)]
    prim[f"is_falling_{s}"] = [bool(x) for x in H.is_falling(c, s)]
    prim[f"trend_color_{s}"] = [int(x) for x in H.get_trend_color(c, s)]
lvl = H.ema(H.dynamic_fib(h, l, 66), 3)
db_ = np.maximum(0.02 * (np.nanmax(h) - np.nanmin(l)), 0.001) * np.ones_like(c)
prim["vote_scaled"] = enc(H.vote_scaled(c, lvl, db_))
dump["primitives"] = prim
print(f"primitives captured: {len(prim)} arrays over {n} bars", flush=True)

# --- the full indicator, end to end, per symbol ---
for s in SYMS:
    tb = bars(s)
    if len(tb) < 250:
        print(f"  {s}: only {len(tb)} bars, skipped", flush=True); continue
    res = calculate_truevx_ranking(tb, bench)
    dump[s] = [[r["date"], r["truevx_score"], r["mean_short"], r["mean_mid"],
                r["mean_long"], r["structural_score"], r["trend_score"]] for r in res]
    print(f"  {s}: {len(res)} indicator rows", flush=True)

blob = json.dumps(dump, sort_keys=True, allow_nan=False)
open(OUT, "w").write(blob)
print(f"\nwrote {OUT}")
print("sha256", hashlib.sha256(blob.encode()).hexdigest())
