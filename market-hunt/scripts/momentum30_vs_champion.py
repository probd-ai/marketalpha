"""Stress-test the 'beats everything' simulator config: top-30 by 20-day return, monthly,
equal-weight, PIT N500 — on risk-adjusted terms and post-bull, vs the honest benchmarks.

Replicates strategy_1781939795919's params (maxHoldings=30, momentum_ranking=20_day_return,
monthly, equal_weight, NIFTY500, PIT membership). Compares vs:
  - aligned-stack champion (the log's best multi-timeframe gate, hold-all)
  - EW-all-N500 control (the HONEST benchmark)
  - Nifty 50 (the simulator's weak benchmark)
Reports CAGR / Sharpe / maxDD / avg turnover for TWO windows:
  (A) 2020-01 -> 2026-06  (the simulator's window, includes the 2020-21 bull)
  (B) 2023-01 -> 2026-06  (post-bull: does the edge survive?)
Net of statutory cost. No look-ahead (momentum & eligibility use data <= snapshot).
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2019, 7, 1)         # warmup before first scored window
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
    mom_at[sym] = (px / px.shift(20) - 1).reindex(snap_ts, method="ffill")   # 20-trading-day return
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
prev_m, prev_c = set(), set()
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    ret = {}
    mom = []
    champ = []
    for sym in members:
        c = close_at.get(sym)
        if c is None:
            continue
        c0, c1 = c.get(d0), c.get(d1)
        if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
            continue
        r = c1 / c0 - 1
        ret[sym] = r
        m = mom_at[sym].get(d0)
        if m is not None and np.isfinite(m):
            mom.append((sym, m, r))
        e = elig_at.get(sym)
        if e is not None and e.get(d0) == True:   # noqa: E712
            champ.append(sym)
    # momentum top-30
    top = sorted(mom, key=lambda x: x[1], reverse=True)[:TOPN]
    m_set = {s for s, _, _ in top}
    m_gross = float(np.mean([r for _, _, r in top])) if top else 0.0
    m_turn = (len(m_set - prev_m) / len(m_set)) if m_set else 0.0
    # champion
    c_gross = float(np.mean([ret[s] for s in champ])) if champ else 0.0
    c_set = set(champ)
    c_turn = (len(c_set - prev_c) / len(c_set)) if c_set else 0.0
    control = float(np.mean(list(ret.values()))) if ret else 0.0
    nif = (nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan
    recs.append((snaps[t], m_gross - m_turn * COST, c_gross - c_turn * COST, control, nif,
                 len(m_set), len(c_set), m_turn, c_turn))
    prev_m, prev_c = m_set, c_set

R = pd.DataFrame(recs, columns=["month", "mom30", "champ", "control", "nifty",
                                "mN", "cN", "mTurn", "cTurn"])
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
    print(f"\n=== {lbl} · {len(w)} months · net ===")
    print(f"{'strategy':28} {'avgN':>5} {'turn%':>6} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
    for col, name, ncol, tcol in [("mom30", "momentum top-30 (20d)", "mN", "mTurn"),
                                   ("champ", "aligned-stack champion", "cN", "cTurn"),
                                   ("control", "EW-all-N500 (HONEST bm)", None, None),
                                   ("nifty", "Nifty 50 (weak bm)", None, None)]:
        cg, sh, dd = stats(w[col])
        n = f"{w[ncol].mean():.0f}" if ncol else "-"
        tn = f"{w[tcol].mean()*100:.0f}" if tcol else "-"
        print(f"{name:28} {n:>5} {tn:>6} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
print("\n[net statutory cost; momentum ranks on 20-trading-day return as of snapshot, top-30 EW, monthly]")
