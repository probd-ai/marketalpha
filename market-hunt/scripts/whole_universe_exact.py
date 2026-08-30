"""Exact replication + whole-universe: aligned-stack (90/80/60/50), top-K by 20-DAY return,
monthly EW, PIT, from 2022-01-01 (matches the simulator URL). No tier exclusion — the full
Large+Mid+Small+Micro universe; gate + momentum decide. Gross AND net (sim under-charges cost).
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2022, 1, 1)
ROC_DAYS = 20
THRESH = {"mean_long": 50, "mean_mid": 60, "mean_short": 80, "truevx_score": 90}
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
COST = BUY_BPS + SELL_BPS
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
ivmic = load_membership(db, "NIFTYMICROCAP250", anchor=date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snaps={len(snaps)} ({snaps[0]}..{snaps[-1]})  ROC={ROC_DAYS}d  gate=90/80/60/50  (sim-exact)")

close_at, elig_at, roc_at = {}, {}, {}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    close_at[sym] = px.reindex(snap_ts)
    roc_at[sym] = (px / px.shift(ROC_DAYS) - 1).reindex(snap_ts, method="ffill")
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


def is_elig(s, d0):
    e = elig_at.get(s)
    return e is not None and e.get(d0) == True   # noqa: E712


def rocv(s, d0):
    v = roc_at[s].get(d0, np.nan)
    return v if np.isfinite(v) else -9e9


def run(univ_fn, K):
    prev, gross, net, nn = set(), [], [], []
    for t in range(len(snap_ts) - 1):
        d0, d1 = snap_ts[t], snap_ts[t + 1]
        uni = univ_fn(snaps[t])
        ret = {}
        for s in uni:
            c = close_at.get(s)
            c0, c1 = (c.get(d0), c.get(d1)) if c is not None else (None, None)
            if c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0:
                ret[s] = c1 / c0 - 1
        elig = sorted([s for s in uni if is_elig(s, d0) and s in ret], key=lambda s: rocv(s, d0), reverse=True)
        sel = elig if K is None else elig[:K]
        n = len(sel)
        g = float(np.mean([ret[s] for s in sel])) if sel else 0.0
        cur = set(sel)
        turn = (len(cur - prev) / n) if n else 0.0
        gross.append(g); net.append(g - turn * COST); nn.append(n); prev = cur
    return gross, net, nn


def stats(s):
    s = np.asarray([x for x in s if np.isfinite(x)])
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12 / len(s)) - 1
    sh = s.mean() / s.std() * math.sqrt(12) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return cagr * 100, sh, mdd * 100


micro = lambda d: members_as_of(ivmic, d)
broad = lambda d: members_as_of(iv500, d) | members_as_of(ivmic, d)
configs = [("Micro-only top-10 (YOUR URL)", micro, 10),
           ("WHOLE top-10 (L+M+S+Micro)", broad, 10),
           ("WHOLE top-15", broad, 15),
           ("WHOLE top-30", broad, 30),
           ("WHOLE hold-all", broad, None)]

print(f"\n{'config':30}{'avgN':>5}{'CAGRg%':>8}{'CAGRn%':>8}{'Sharpe':>7}{'maxDD%':>8}")
for name, fn, K in configs:
    g, n, nn = run(fn, K)
    cgg = stats(g)[0]
    cgn, shn, ddn = stats(n)
    print(f"{name:30}{np.mean(nn):>5.0f}{cgg:>8.1f}{cgn:>8.1f}{shn:>7.2f}{ddn:>8.1f}")
print(f"\n[gross≈what the sim shows (it under-charges turnover); net=realized cost; PIT; 2022-01→2026-06]")
