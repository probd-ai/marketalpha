"""Aligned-stack eligibility, then select TOP N by 66-day price ROC.

Eligible (same as aligned_stack): mean_long>50 & mean_mid>60 & mean_short>80 & truevx_score>90.
Each month, among eligible PIT-N500 members, rank by 66-trading-day price ROC and hold the
top N equal-weight. Compares N=10/20/30 vs HOLD-ALL eligible (~79 names) vs EW-all-N500
control vs Nifty 50. Tests whether concentrating the aligned-stack by momentum adds return
over just holding all of it. No look-ahead (ROC and eligibility use data <= snapshot).
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2019, 1, 1)
ROC_DAYS = 66
THRESH = {"mean_long": 50, "mean_mid": 60, "mean_short": 80, "truevx_score": 90}
NS = [10, 20, 30]
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
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
iv500 = load_membership(db, "NIFTY500", anchor=date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snapshots={len(snaps)} ({snaps[0]}..{snaps[-1]})  symbols={len(syms)}  ROC={ROC_DAYS}d")

elig_at, roc_at, close_at = {}, {}, {}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 80:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_long": 1, "data.mean_mid": 1,
         "data.mean_short": 1, "data.truevx_score": 1}))
    if not irows:
        continue
    idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
    cond = None
    for f, thr in THRESH.items():
        c = pd.Series({d: r["data"].get(f) for d, r in zip(idx, irows)}).sort_index() > thr
        cond = c if cond is None else (cond & c)
    elig_at[sym] = cond.reindex(snap_ts, method="ffill")     # NaN where unknown
    roc_at[sym] = (px / px.shift(ROC_DAYS) - 1).reindex(snap_ts, method="ffill")
    close_at[sym] = px.reindex(snap_ts)

nifty = close_series("Nifty 50").reindex(snap_ts)

series = {f"top{n}": [] for n in NS}
series["hold_all"] = []
control, nif = [], []
prev = {k: set() for k in series}
holds = {k: [] for k in series}
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    cand, elig = [], []                       # cand=(ret) all members; elig=(sym,roc,ret)
    for sym in members:
        c = close_at.get(sym)
        if c is None:
            continue
        c0, c1 = c.get(d0), c.get(d1)
        if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
            continue
        ret = c1 / c0 - 1
        cand.append(ret)
        if elig_at.get(sym) is not None and elig_at[sym].get(d0) == True:  # noqa: E712
            r = roc_at[sym].get(d0)
            if r is not None and np.isfinite(r):
                elig.append((sym, r, ret))
    control.append(float(np.mean(cand)) if cand else 0.0)
    nif.append((nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan)
    elig_sorted = sorted(elig, key=lambda x: x[1], reverse=True)   # high ROC first

    def book(sel, key):
        n = len(sel)
        g = float(np.mean([r for _, _, r in sel])) if sel else 0.0
        cur = {s for s, _, _ in sel}
        turn = (len(cur - prev[key]) / n) if n else 0.0
        prev[key] = cur
        holds[key].append(n)
        series[key].append(g - turn * (BUY_BPS + SELL_BPS))

    for n in NS:
        book(elig_sorted[:n], f"top{n}")
    book(elig_sorted, "hold_all")

months = len(control)


def stats(rets):
    s = np.array([r for r in rets if np.isfinite(r)])
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12.0 / months) - 1
    sharpe = (s.mean() / s.std() * math.sqrt(12)) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return (eq[-1] - 1) * 100, cagr * 100, sharpe, mdd * 100


print(f"\n=== aligned-stack, then TOP N by {ROC_DAYS}d price ROC (net) · PIT N500 · {snaps[0]}..{snaps[-1]} ===")
print(f"{'book':24} {'avgN':>5} {'totRet%':>9} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
for k in [f"top{n}" for n in NS] + ["hold_all"]:
    tr, cg, sh, dd = stats(series[k])
    print(f"{k:24} {np.mean(holds[k]):>5.0f} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
for lab, col in [("EW-all-N500 (control)", control), ("Nifty 50", nif)]:
    tr, cg, sh, dd = stats(col)
    print(f"{lab:24} {'-':>5} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
print(f"\n[net of statutory cost; top-N ranked by {ROC_DAYS}d price ROC within aligned-stack eligible]")
