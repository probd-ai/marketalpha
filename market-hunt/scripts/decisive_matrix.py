"""Decisive matrix: aligned-stack across {N500, BROAD=N500+Micro} x {PIT, non-PIT} x concentration
(hold-all / top-50 / 30 / 15 / 10 / 5 by 22d ROC), monthly EW, net. 2022-06->2026-06.

Answers in one run:
  - where the non-PIT 'extra alpha' comes from (PIT vs non-PIT gap, per universe & concentration)
  - how much is honestly recoverable via broad PIT (microcaps, point-in-time) vs pure hindsight
  - the return-vs-concentration frontier for portfolio design
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
KS = [("all", None), ("top50", 50), ("top30", 30), ("top15", 15), ("top10", 10), ("top5", 5)]
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
cur500 = members_as_of(iv500, date.today())
curmic = members_as_of(ivmic, date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snaps={len(snaps)} ({snaps[0]}..{snaps[-1]})  symbols={len(syms)}  ROC={ROC_DAYS}d")

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

UNIVERSES = {
    "N500 PIT": lambda d: members_as_of(iv500, d),
    "N500 nonPIT": lambda d: cur500,
    "BROAD PIT": lambda d: members_as_of(iv500, d) | members_as_of(ivmic, d),
    "BROAD nonPIT": lambda d: cur500 | curmic,
}


def is_elig(s, d0):
    e = elig_at.get(s)
    return e is not None and e.get(d0) == True   # noqa: E712


def roc(s, d0):
    v = roc_at[s].get(d0, np.nan)
    return v if np.isfinite(v) else -9e9


series = {(u, kn): [] for u in UNIVERSES for kn, _ in KS}
prev = {(u, kn): set() for u in UNIVERSES for kn, _ in KS}
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    superset = (cur500 | curmic | members_as_of(iv500, snaps[t]) | members_as_of(ivmic, snaps[t]))
    ret = {}
    for s in superset:
        c = close_at.get(s)
        if c is None:
            continue
        c0, c1 = c.get(d0), c.get(d1)
        if c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0:
            ret[s] = c1 / c0 - 1
    for uname, ufn in UNIVERSES.items():
        elig = [s for s in ufn(snaps[t]) if is_elig(s, d0) and s in ret]
        elig.sort(key=lambda s: roc(s, d0), reverse=True)
        for kn, k in KS:
            sel = elig if k is None else elig[:k]
            n = len(sel)
            g = float(np.mean([ret[s] for s in sel])) if sel else 0.0
            cur = set(sel)
            turn = (len(cur - prev[(uname, kn)]) / n) if n else 0.0
            series[(uname, kn)].append(g - turn * COST)
            prev[(uname, kn)] = cur

months = len(series[("N500 PIT", "all")])


def stats(s):
    s = np.asarray([x for x in s if np.isfinite(x)])
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12.0 / len(s)) - 1
    sh = s.mean() / s.std() * math.sqrt(12) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return cagr * 100, sh, mdd * 100


print(f"\n=== CAGR% matrix (net, {months} months, 2022-06->2026-06) ===")
print(f"{'universe':14}" + "".join(f"{kn:>8}" for kn, _ in KS))
cagrs = {}
for u in UNIVERSES:
    row = []
    for kn, _ in KS:
        cg, sh, dd = stats(series[(u, kn)])
        cagrs[(u, kn)] = (cg, sh, dd)
        row.append(f"{cg:>7.1f}")
    print(f"{u:14}" + "".join(row))
print(f"\n=== Sharpe matrix ===")
print(f"{'universe':14}" + "".join(f"{kn:>8}" for kn, _ in KS))
for u in UNIVERSES:
    print(f"{u:14}" + "".join(f"{cagrs[(u,kn)][1]:>8.2f}" for kn, _ in KS))
print(f"\n=== maxDD% matrix ===")
print(f"{'universe':14}" + "".join(f"{kn:>8}" for kn, _ in KS))
for u in UNIVERSES:
    print(f"{u:14}" + "".join(f"{cagrs[(u,kn)][2]:>8.0f}" for kn, _ in KS))

print(f"\n=== survivorship tax (nonPIT - PIT, CAGR pts) ===")
for base in ("N500", "BROAD"):
    print(f"{base:14}" + "".join(f"{cagrs[(base+' nonPIT',kn)][0]-cagrs[(base+' PIT',kn)][0]:>8.1f}" for kn, _ in KS))
print(f"\n=== honest broad gain over N500 PIT (BROAD PIT - N500 PIT, CAGR pts) ===")
print(f"{'gain':14}" + "".join(f"{cagrs[('BROAD PIT',kn)][0]-cagrs[('N500 PIT',kn)][0]:>8.1f}" for kn, _ in KS))
