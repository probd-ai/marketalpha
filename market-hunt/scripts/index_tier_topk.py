"""Aligned-stack (score>90 & short>80 & mid>60 & long>50), top-K by 22d ROC, monthly EW, PIT,
2022-06→2026-06 (all tiers from 2022 — Microcap250 was introduced then, so apples-to-apples).

Per tier index, top-10; Small+Micro combined, top-15. Hold-all shown for reference.
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2022, 6, 1)
ROC_DAYS = 22
THRESH = {"mean_long": 50, "mean_mid": 60, "mean_short": 80, "truevx_score": 90}
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
COST = BUY_BPS + SELL_BPS
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]

UNIVERSES = [
    ("Large (N100)", ["NIFTY100"], 10),
    ("Mid (150)", ["NIFTYMIDCAP150"], 10),
    ("Small (250)", ["NIFTYSMALLCAP250"], 10),
    ("Micro (250)", ["NIFTYMICROCAP250"], 10),
    ("Small+Micro", ["NIFTYSMALLCAP250", "NIFTYMICROCAP250"], 15),
]


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
idxnames = sorted({ix for _, ixs, _ in UNIVERSES for ix in ixs})
mem = {ix: load_membership(db, ix, anchor=date.today()) for ix in idxnames}
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snaps={len(snaps)} ({snaps[0]}..{snaps[-1]})  symbols={len(syms)}  ROC={ROC_DAYS}d  gate=90/80/60/50")

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

nifty = close_series("Nifty 50").reindex(snap_ts)


def is_elig(s, d0):
    e = elig_at.get(s)
    return e is not None and e.get(d0) == True   # noqa: E712


def roc(s, d0):
    v = roc_at[s].get(d0, np.nan)
    return v if np.isfinite(v) else -9e9


def run(ixs, K):
    prev_t, prev_h = set(), set()
    out_t, out_h, nt, nh = [], [], [], []
    for t in range(len(snap_ts) - 1):
        d0, d1 = snap_ts[t], snap_ts[t + 1]
        uni = set()
        for ix in ixs:
            uni |= members_as_of(mem[ix], snaps[t])
        ret = {}
        for s in uni:
            c = close_at.get(s)
            c0, c1 = (c.get(d0), c.get(d1)) if c is not None else (None, None)
            if c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0:
                ret[s] = c1 / c0 - 1
        elig = sorted([s for s in uni if is_elig(s, d0) and s in ret], key=lambda s: roc(s, d0), reverse=True)
        for sel, prev, out, nn in [(elig[:K], prev_t, out_t, nt), (elig, prev_h, out_h, nh)]:
            n = len(sel)
            g = float(np.mean([ret[s] for s in sel])) if sel else 0.0
            cur = set(sel)
            turn = (len(cur - prev) / n) if n else 0.0
            out.append(g - turn * COST); nn.append(n)
        prev_t, prev_h = set(elig[:K]), set(elig)
    return out_t, nt, out_h, nh


months = len(snap_ts) - 1


def stats(s):
    s = np.asarray([x for x in s if np.isfinite(x)])
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12 / len(s)) - 1
    sh = s.mean() / s.std() * math.sqrt(12) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return cagr * 100, sh, mdd * 100


print(f"\n{'universe':14}{'K':>4}{'avgN':>5}{'CAGR%':>7}{'Sharpe':>7}{'maxDD%':>8}   | hold-all: {'avgN':>4}{'CAGR%':>7}{'Sharpe':>7}{'maxDD%':>8}")
for name, ixs, K in UNIVERSES:
    ot, ntc, oh, nhc = run(ixs, K)
    cgt, sht, ddt = stats(ot)
    cgh, shh, ddh = stats(oh)
    print(f"{name:14}{K:>4}{np.mean(ntc):>5.0f}{cgt:>7.1f}{sht:>7.2f}{ddt:>8.1f}   |          {np.mean(nhc):>4.0f}{cgh:>7.1f}{shh:>7.2f}{ddh:>8.1f}")
ncg, nsh, ndd = stats([(nifty.get(snap_ts[t + 1]) / nifty.get(snap_ts[t]) - 1)
                       if (nifty.get(snap_ts[t]) and nifty.get(snap_ts[t + 1])) else np.nan
                       for t in range(months)])
print(f"{'Nifty 50':14}{'-':>4}{'-':>5}{ncg:>7.1f}{nsh:>7.2f}{ndd:>8.1f}")
print(f"\n[PIT each index; aligned-stack gate; top-K by {ROC_DAYS}d ROC; monthly EW; net; 2022-06→2026-06]")
