"""CORRECTED replication of strategy_1781939795919 ("N100" = the aligned-stack champion).

Engine logic: apply the 4 aligned-stack rules FIRST (long>50 & mid>60 & short>80 & score>90),
THEN take top-30 by 20-day return among THOSE eligible names (not the whole universe -- that was
my earlier error). Compare:
  - mom30_stack : top-30 by 20d return WITHIN aligned-stack eligible  (the simulator config)
  - champ_holdall: ALL aligned-stack eligible, equal-weight           (the pure champion)
  - EW-all-N500  : honest benchmark
  - Nifty 50     : weak benchmark (what the simulator shows)
Windows: A) 2020-01->2026-06 (incl bull)  B) 2023-01->2026-06 (post-bull).
Reports gross AND net (statutory cost x realized turnover) -- the top-30 trim churns more.
PIT N500, monthly, rebalance first trading day, no look-ahead.
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2019, 7, 1)
TOPN = 30
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
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})

close_at, mom_at, elig_at = {}, {}, {}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    close_at[sym] = px.reindex(snap_ts)
    mom_at[sym] = (px / px.shift(20) - 1).reindex(snap_ts, method="ffill")
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

recs = []
pm, pc = set(), set()
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    ret, elig = {}, []
    for sym in members:
        c = close_at.get(sym)
        if c is None:
            continue
        c0, c1 = c.get(d0), c.get(d1)
        if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
            continue
        ret[sym] = c1 / c0 - 1
        e = elig_at.get(sym)
        if e is not None and e.get(d0) == True:        # noqa: E712
            elig.append(sym)
    # top-30 by 20d momentum WITHIN aligned-stack eligible
    mom = [(s, mom_at[s].get(d0)) for s in elig if np.isfinite(mom_at[s].get(d0, np.nan))]
    top = [s for s, _ in sorted(mom, key=lambda x: x[1], reverse=True)[:TOPN]]
    m_set, c_set = set(top), set(elig)
    m_g = float(np.mean([ret[s] for s in top])) if top else 0.0
    c_g = float(np.mean([ret[s] for s in elig])) if elig else 0.0
    m_turn = (len(m_set - pm) / len(m_set)) if m_set else 0.0
    c_turn = (len(c_set - pc) / len(c_set)) if c_set else 0.0
    control = float(np.mean(list(ret.values()))) if ret else 0.0
    nif = (nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan
    recs.append((snaps[t], m_g, m_g - m_turn * COST, c_g, c_g - c_turn * COST, control, nif,
                 len(m_set), len(c_set), m_turn, c_turn))
    pm, pc = m_set, c_set

R = pd.DataFrame(recs, columns=["month", "m_g", "m_n", "c_g", "c_n", "control", "nifty",
                                "mN", "cN", "mT", "cT"])
R["month"] = pd.to_datetime(R["month"])


def stats(s):
    s = np.asarray([x for x in s if np.isfinite(x)])
    if len(s) < 2:
        return (np.nan,) * 3
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12.0 / len(s)) - 1
    sh = s.mean() / s.std() * math.sqrt(12) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return cagr * 100, sh, mdd * 100


for lbl, frm in [("A) 2020-01 -> 2026-06 (incl. 2020-21 bull)", "2020-01-01"),
                 ("B) 2023-01 -> 2026-06 (POST-bull)", "2023-01-01")]:
    w = R[R.month >= frm]
    print(f"\n=== {lbl} · {len(w)} months ===")
    print(f"{'strategy':30} {'avgN':>5} {'turn%':>6} {'CAGRg%':>7} {'CAGRn%':>7} {'Sharpe':>7} {'maxDD%':>8}")
    rows = [("mom30 within stack (SIM cfg)", "m_g", "m_n", "mN", "mT"),
            ("aligned-stack HOLD-ALL (champ)", "c_g", "c_n", "cN", "cT")]
    for name, g, n, ncol, tcol in rows:
        cg_g, _, _ = stats(w[g])
        cg_n, sh_n, dd_n = stats(w[n])
        print(f"{name:30} {w[ncol].mean():>5.0f} {w[tcol].mean()*100:>6.0f} {cg_g:>7.1f} {cg_n:>7.1f} {sh_n:>7.2f} {dd_n:>8.1f}")
    for name, col in [("EW-all-N500 (honest bm)", "control"), ("Nifty 50 (weak bm)", "nifty")]:
        cg, sh, dd = stats(w[col])
        print(f"{name:30} {'-':>5} {'-':>6} {cg:>7.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
print("\n[CAGRg=gross, CAGRn=net of statutory cost x realized turnover; Sharpe/maxDD on net;")
print(" mom30 = top-30 by 20d return WITHIN aligned-stack eligible; engine retains holdings -> its")
print(" realized turnover (hence net) is a bit better than this fresh-selection replication]")
