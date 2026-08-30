"""Optimal cap-tier allocation for the stacked (aligned-stack) system, PIT, 2022-06→2026-06.

Build 4 monthly return streams = EW return of aligned-stack-eligible names IN each cap tier
(Large=N100, Mid=Midcap150, Small=Smallcap250, Micro=Microcap250), point-in-time. Then:
  - per-tier stats + correlation (diversification)
  - mean-variance optima: max-Sharpe (tangency), min-variance, max-return — long-only, sum=1
  - STABILITY: re-solve max-Sharpe on H1 (22-24) and H2 (24-26) — if weights swing wildly the
    'optimum' is regime-noise, so report a robust range not false precision.
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2022, 6, 1)
THRESH = {"mean_long": 50, "mean_mid": 60, "mean_short": 80, "truevx_score": 90}
TIERS = ["Large", "Mid", "Small", "Micro"]
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]


def close_series(symbol):
    rows = []
    for c in ("prices_2015_2019", "prices_2020_2024", "prices_2025_2029"):
        rows += list(db[c].find({"symbol": symbol, "date": {"$gte": START}},
                                {"_id": 0, "date": 1, "close_price": 1}))
    if not rows:
        return None
    s = pd.DataFrame(rows)
    s["date"] = pd.to_datetime(s["date"]).dt.normalize()
    s = s.dropna(subset=["close_price"]).drop_duplicates("date").sort_values("date")
    return s.set_index("date")["close_price"].astype(float)


cal = close_series("RELIANCE")
snaps = [d.date() for d in pd.Series(cal.index).groupby([cal.index.year, cal.index.month]).min()]
snaps = [d for d in snaps if d >= SNAP_FROM]
snap_ts = [pd.Timestamp(d) for d in snaps]
mem = {ix: load_membership(db, ix, anchor=date.today())
       for ix in ("NIFTY100", "NIFTYMIDCAP150", "NIFTYSMALLCAP250", "NIFTYMICROCAP250")}
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})

close_at, elig_at = {}, {}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    close_at[sym] = px.reindex(snap_ts)
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_long": 1, "data.mean_mid": 1,
         "data.mean_short": 1, "data.truevx_score": 1}))
    if irows:
        idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
        cond = None
        for f, thr in THRESH.items():
            c = pd.Series({d: r["data"].get(f) for d, r in zip(idx, irows)}).sort_index() > thr
            cond = c if cond is None else (cond & c)
        elig_at[sym] = cond.reindex(snap_ts, method="ffill")


def tier_of(s, sd, cache):
    if s in cache:
        return cache[s]
    t = ("Large" if s in members_as_of(mem["NIFTY100"], sd) else
         "Mid" if s in members_as_of(mem["NIFTYMIDCAP150"], sd) else
         "Small" if s in members_as_of(mem["NIFTYSMALLCAP250"], sd) else
         "Micro" if s in members_as_of(mem["NIFTYMICROCAP250"], sd) else None)
    cache[s] = t
    return t


def is_elig(s, d0):
    e = elig_at.get(s)
    return e is not None and e.get(d0) == True   # noqa: E712


rows = []
for t in range(len(snap_ts) - 1):
    d0, d1, sd = snap_ts[t], snap_ts[t + 1], snaps[t]
    buckets = {ti: [] for ti in TIERS}
    cache = {}
    for s in elig_at:
        if not is_elig(s, d0):
            continue
        c = close_at.get(s)
        c0, c1 = (c.get(d0), c.get(d1)) if c is not None else (None, None)
        if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
            continue
        ti = tier_of(s, sd, cache)
        if ti:
            buckets[ti].append(c1 / c0 - 1)
    rows.append({ti: (np.mean(buckets[ti]) if buckets[ti] else np.nan) for ti in TIERS})

R = pd.DataFrame(rows)[TIERS]
R_full = R.dropna(how="all").fillna(0.0)
n = len(R_full)


def ann_stats(s):
    s = np.asarray(s)
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12 / len(s)) - 1
    sh = s.mean() / s.std() * math.sqrt(12) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return cagr * 100, sh, mdd * 100


print(f"=== per-tier (aligned-stack eligible, PIT, EW), {n} months 2022-06→2026-06 ===")
print(f"{'tier':8}{'CAGR%':>8}{'Sharpe':>8}{'vol%':>7}{'maxDD%':>8}")
for ti in TIERS:
    cg, sh, dd = ann_stats(R_full[ti])
    print(f"{ti:8}{cg:>8.1f}{sh:>8.2f}{R_full[ti].std()*math.sqrt(12)*100:>7.1f}{dd:>8.1f}")
print("\ncorrelation:")
print(R_full.corr().round(2).to_string())


def opt(returns, objective):
    mu = returns.mean().values * 12
    cov = returns.cov().values * 12
    k = len(mu)
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1}]
    bnds = [(0, 1)] * k
    if objective == "sharpe":
        f = lambda w: -(w @ mu) / math.sqrt(max(w @ cov @ w, 1e-12))
    elif objective == "minvar":
        f = lambda w: w @ cov @ w
    res = minimize(f, np.repeat(1 / k, k), method="SLSQP", bounds=bnds, constraints=cons)
    return res.x


def blend_stats(returns, w):
    s = (returns.values @ w)
    return ann_stats(s)


print("\n=== optimal tier allocations (long-only, sum=100%) ===")
print(f"{'objective':16}" + "".join(f"{ti:>8}" for ti in TIERS) + f"{'CAGR%':>8}{'Sharpe':>8}{'maxDD%':>8}")
for lbl, obj in [("max-Sharpe", "sharpe"), ("min-variance", "minvar")]:
    w = opt(R_full, obj)
    cg, sh, dd = blend_stats(R_full, w)
    print(f"{lbl:16}" + "".join(f"{x*100:>7.0f}%" for x in w) + f"{cg:>8.1f}{sh:>8.2f}{dd:>8.1f}")
# max-return = 100% best-CAGR tier
best = max(TIERS, key=lambda ti: ann_stats(R_full[ti])[0])
w = np.array([1.0 if ti == best else 0.0 for ti in TIERS])
cg, sh, dd = blend_stats(R_full, w)
print(f"{'max-return':16}" + "".join(f"{x*100:>7.0f}%" for x in w) + f"{cg:>8.1f}{sh:>8.2f}{dd:>8.1f}")
# equal-tier and the strategy's natural mix for reference
for lbl, w in [("equal-tier", np.repeat(0.25, 4)), ("natural(15/22/31/32)", np.array([.15, .22, .31, .32])),
               ("robust(18/45/5/32)", np.array([.18, .45, .05, .32])),
               ("deployable(25/45/0/30)", np.array([.25, .45, .0, .30])),
               ("balanced(20/40/10/30)", np.array([.20, .40, .10, .30]))]:
    cg, sh, dd = blend_stats(R_full, w)
    print(f"{lbl:22}" + "".join(f"{x*100:>7.0f}%" for x in w) + f"{cg:>8.1f}{sh:>8.2f}{dd:>8.1f}")

print("\n=== STABILITY: max-Sharpe weights by sub-period ===")
half = n // 2
print(f"{'period':16}" + "".join(f"{ti:>8}" for ti in TIERS))
for lbl, sub in [("full 22-26", R_full), ("H1 22-24", R_full.iloc[:half]), ("H2 24-26", R_full.iloc[half:])]:
    w = opt(sub, "sharpe")
    print(f"{lbl:16}" + "".join(f"{x*100:>7.0f}%" for x in w))
print("\n[if weights swing wildly across H1/H2, the 'optimum' is regime-noise -> use a robust range]")
