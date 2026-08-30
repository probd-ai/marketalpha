"""Do the 4 TrueVX regimes actually predict forward returns? (the edge test)

Honest construction:
  - regime at date t uses ONLY truevx_score & mean_short known at t (no look-ahead);
    forward return uses close_price AFTER t.
  - sample on MONTHLY snapshot dates (first trading day each month) -> near
    non-overlapping windows, each a real rebalance decision.
  - universe = point-in-time NIFTY500 members as of t (survivorship-safe).
  - horizons: 21 trading days (~1m) and 63 (~3m).

Each regime is treated as an equal-weight basket rebalanced monthly: we take the
cross-sectional mean forward return of its members each month, then compound that
monthly series. Also reports the fresh-minus-fading long-short with a t-stat.
"""
import sys, os
from collections import defaultdict
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

H = {"1m": 21, "3m": 63}
LEVEL_HI, START = 60.0, datetime(2020, 1, 1)
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]

def regime(level, vel):
    hi, rise = level >= LEVEL_HI, vel > 0
    return ("fresh_leaders" if hi and rise else "fading_leaders" if hi and not rise
            else "early_recovery" if (not hi) and rise else "laggards")

# --- market calendar -> monthly snapshot dates (first trading day / month) ---
def load_close(symbol):
    rows = []
    for c in ("prices_2020_2024", "prices_2025_2029"):
        rows += list(db[c].find({"symbol": symbol, "date": {"$gte": START}},
                                {"_id": 0, "date": 1, "close_price": 1}))
    if not rows:
        return None
    s = pd.DataFrame(rows)
    s["date"] = pd.to_datetime(s["date"]).dt.normalize()
    s = s.dropna(subset=["close_price"]).drop_duplicates("date").sort_values("date")
    return s.set_index("date")["close_price"].astype(float)

cal = load_close("RELIANCE")
snap = pd.Series(cal.index).groupby([cal.index.year, cal.index.month]).min().tolist()
snap = [d.date() for d in snap]
print(f"monthly snapshot dates: {len(snap)}  ({snap[0]} .. {snap[-1]})")

iv500 = load_membership(db, "NIFTY500", anchor=date.today())
ind_symbols = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"indicator symbols: {len(ind_symbols)}")

# per (horizon, snapshot_date, regime) -> list of forward returns
bucket = {h: defaultdict(lambda: defaultdict(list)) for h in H}
pooled = {h: defaultdict(list) for h in H}        # regime -> all slot returns
covered, missing = 0, 0

for sym in ind_symbols:
    px = load_close(sym)
    if px is None or len(px) < 80:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.truevx_score": 1, "data.mean_short": 1}))
    if not irows:
        continue
    idf = pd.DataFrame([{"date": pd.to_datetime(r["date"]).normalize(),
                         "level": r["data"].get("truevx_score"),
                         "ms": r["data"].get("mean_short")} for r in irows])
    idf = idf.dropna(subset=["level", "ms"]).drop_duplicates("date").set_index("date")
    px = px.reindex(px.index)                      # already sorted
    fwd = {h: px.shift(-n) / px - 1.0 for h, n in H.items()}
    snap_set = set(pd.Timestamp(d) for d in snap)
    for ts in idf.index:
        if ts not in snap_set or ts not in px.index:
            continue
        d = ts.date()
        if sym not in members_as_of(iv500, d):
            continue
        reg = regime(idf.at[ts, "level"], idf.at[ts, "level"] - idf.at[ts, "ms"])
        for h in H:
            r = fwd[h].get(ts)
            if r is not None and np.isfinite(r):
                bucket[h][d][reg].append(r)
                pooled[h][reg].append(r)

REGS = ["fresh_leaders", "fading_leaders", "early_recovery", "laggards"]

for h in H:
    print(f"\n{'='*72}\n=== horizon {h} ({H[h]} trading days) ===")
    print(f"{'regime':16} {'slots':>7} {'mean%':>7} {'med%':>7} {'win%':>6} "
          f"{'basketCAGR%':>11} {'Sharpe':>7}")
    monthly = {r: [] for r in REGS}                # regime -> monthly basket return
    months = sorted(bucket[h].keys())
    for d in months:
        for r in REGS:
            v = bucket[h][d].get(r)
            if v:
                monthly[r].append(np.mean(v))
    for r in REGS:
        allv = np.array(pooled[h][r])
        m = np.array(monthly[r])
        n_m = len(m)
        cagr = ((np.prod(1 + m)) ** (12.0 / max(n_m, 1)) - 1) * 100 if n_m else float("nan")
        shp = (m.mean() / m.std() * np.sqrt(12)) if n_m > 1 and m.std() > 0 else float("nan")
        print(f"{r:16} {len(allv):>7} {allv.mean()*100:>7.2f} "
              f"{np.median(allv)*100:>7.2f} {(allv>0).mean()*100:>6.1f} "
              f"{cagr:>11.1f} {shp:>7.2f}")
    # long-short: fresh minus fading, paired by month
    fa, fd = monthly["fresh_leaders"], monthly["fading_leaders"]
    k = min(len(fa), len(fd))
    if k > 2:
        # pair only months present in both
        common = [d for d in months
                  if bucket[h][d].get("fresh_leaders") and bucket[h][d].get("fading_leaders")]
        diff = np.array([np.mean(bucket[h][d]["fresh_leaders"]) -
                         np.mean(bucket[h][d]["fading_leaders"]) for d in common])
        t = diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))
        print(f"  fresh - fading: avg monthly spread = {diff.mean()*100:+.2f}%  "
              f"over {len(diff)} months  t-stat = {t:.2f}  "
              f"({'SIGNIFICANT' if abs(t) > 2 else 'not significant'} at |t|>2)")

print("\n[PIT NIFTY500 universe, monthly snapshots, no look-ahead in regime assignment]")
