"""Does price POSITION add anything after Alpha's strength gate?

Seven measures, scored on the population Alpha already qualifies, against forward
1-month return. Two universes, two disjoint windows each.

  Family A - absolute position on the stock's OWN price chart. The indicator never
             sees this: every TrueVX input is divided by the benchmark first.
                 pos_abs_N = (c - min(c,N)) / (max(c,N) - min(c,N))
                 N in {66d, 126d, 252d, all-time}

  Family B - UNSATURATED position on the ratio. The indicator computes these ranges
             (dynamic_fib at 22/66/222) but vote_scaled's tanh pins anything above
             ~30% of range to +1, discarding the magnitude.
                 pos_rel_N = (cr - min(lr,N)) / (max(hr,N) - min(lr,N))
                 N in {22d, 66d, 222d}, on the target/benchmark ratio, matching
                 dynamic_fib's use of high/low.

ACCEPTANCE (fixed before looking): a measure passes only if the correlation with
forward return has the SAME SIGN in both windows for the same universe, AND |t| > 2
in at least one. With 28 tests some will look significant by chance; sign-consistency
across disjoint windows is what noise cannot fake.

Windows are universe-specific so both halves are point-in-time honest:
  NIFTY500          2020-01..2023-06 | 2023-07..2026-08   (recon from 2020-03)
  NIFTYMICROCAP250  2022-04..2024-06 | 2024-07..2026-08   (recon from 2022-03)
"""
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
from settings import MONGO_URI, DB_NAME
from membership import load_membership, members_as_of, union_over
from engine.stateless import fetch_pre_window_extremes

LOAD_START = datetime(2018, 1, 1)          # 2y warm-up before the earliest window
END = datetime(2026, 8, 28)
BENCH = "Nifty 50"
GATE = [("truevx_score", 90.0), ("mean_short", 80.0), ("mean_mid", 60.0), ("mean_long", 50.0)]

SPECS = [
    ("NIFTY500", [("W1 2020-01..2023-06", datetime(2020, 1, 1), datetime(2023, 6, 30)),
                  ("W2 2023-07..2026-08", datetime(2023, 7, 1), END)]),
    ("NIFTYMICROCAP250", [("W1 2022-04..2024-06", datetime(2022, 4, 1), datetime(2024, 6, 30)),
                          ("W2 2024-07..2026-08", datetime(2024, 7, 1), END)]),
]
ABS_H = [("pos_abs_66", 66), ("pos_abs_126", 126), ("pos_abs_252", 252), ("pos_abs_all", None)]
REL_H = [("pos_rel_22", 22), ("pos_rel_66", 66), ("pos_rel_222", 222)]
MEASURES = [m for m, _ in ABS_H] + [m for m, _ in REL_H]

db = MongoClient(MONGO_URI)[DB_NAME]
PRICE_COLLS = sorted(c for c in db.list_collection_names() if c.startswith("prices_"))


def frames(symbols):
    """close / high / low pivots (dates x symbols) over the load window."""
    rows = []
    for c in PRICE_COLLS:
        rows += list(db[c].find(
            {"symbol": {"$in": symbols}, "date": {"$gte": LOAD_START, "$lte": END}},
            {"_id": 0, "symbol": 1, "date": 1, "close_price": 1, "high_price": 1, "low_price": 1}))
    df = pd.DataFrame(rows).dropna(subset=["close_price"])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.drop_duplicates(["symbol", "date"])
    C = df.pivot(index="date", columns="symbol", values="close_price").sort_index().astype(float)
    H = df.pivot(index="date", columns="symbol", values="high_price").sort_index().astype(float)
    L = df.pivot(index="date", columns="symbol", values="low_price").sort_index().astype(float)
    return C, H.fillna(C), L.fillna(C)


def bench_frames(index):
    rows = []
    for c in PRICE_COLLS:
        rows += list(db[c].find({"symbol": BENCH, "date": {"$gte": LOAD_START, "$lte": END}},
                                {"_id": 0, "date": 1, "close_price": 1,
                                 "high_price": 1, "low_price": 1}))
    b = pd.DataFrame(rows).dropna(subset=["close_price"])
    b["date"] = pd.to_datetime(b["date"]).dt.normalize()
    b = b.drop_duplicates("date").set_index("date").sort_index().astype(float)
    b = b.reindex(index).ffill()
    return b["close_price"], b["high_price"].fillna(b["close_price"]), b["low_price"].fillna(b["close_price"])


def positions(universe, symbols):
    C, H, L = frames(symbols)
    bc, bh, bl = bench_frames(C.index)
    seed = fetch_pre_window_extremes(db, symbols, LOAD_START)

    pos = {}
    for name, N in ABS_H:
        if N is None:
            hi, lo = C.cummax(), C.cummin()
            for s in C.columns:                       # fold in pre-window history
                if s in seed:
                    hi[s] = np.maximum(hi[s], seed[s][0])
                    lo[s] = np.minimum(lo[s], seed[s][1])
        else:
            hi, lo = C.rolling(N, min_periods=N).max(), C.rolling(N, min_periods=N).min()
        pos[name] = ((C - lo) / (hi - lo).replace(0, np.nan))

    Cr, Hr, Lr = C.div(bc, axis=0), H.div(bh, axis=0), L.div(bl, axis=0)
    for name, N in REL_H:
        hi = Hr.rolling(N, min_periods=N).max()
        lo = Lr.rolling(N, min_periods=N).min()
        pos[name] = ((Cr - lo) / (hi - lo).replace(0, np.nan))
    return C, pos


def indicators(symbols, lo, hi):
    out = {}
    for d in db.indicators.find({"indicator_type": "truevx", "symbol": {"$in": symbols},
                                 "date": {"$gte": lo, "$lte": hi}}):
        out.setdefault(d["date"].strftime("%Y-%m-%d"), {})[d["symbol"]] = {
            k: (d["data"].get(k) or 0) for k, _ in GATE}
    return out


def collect(universe, iv, C, pos, ind, w_lo, w_hi):
    idx = [d for d in C.index if w_lo <= d <= w_hi]
    reb, seen = [], set()
    for d in idx:
        if (d.year, d.month) not in seen:
            seen.add((d.year, d.month)); reb.append(d)
    recs = []
    for i, d in enumerate(reb[:-1]):
        nxt = reb[i + 1]
        ds = d.strftime("%Y-%m-%d")
        day = ind.get(ds)
        if not day:
            continue
        members = members_as_of(iv, d.date())
        for s, v in day.items():
            if s not in members or s not in C.columns:
                continue
            if not all(v.get(k, 0) > t for k, t in GATE):
                continue
            c0, c1 = C.at[d, s], C.at[nxt, s]
            if not np.isfinite(c0) or not np.isfinite(c1) or c0 <= 0:
                continue
            row = {"fwd": (c1 / c0 - 1) * 100}
            ok = True
            for m in MEASURES:
                x = pos[m].at[d, s]
                if not np.isfinite(x):
                    ok = False; break
                row[m] = x
            if ok:
                recs.append(row)
    return pd.DataFrame(recs)


results = {}
for universe, windows in SPECS:
    iv = load_membership(db, universe, date.today())
    syms = sorted(union_over(iv, LOAD_START.date(), END.date()))
    print(f"\n{'='*78}\n{universe}   {len(syms)} ever-members   loading...", flush=True)
    C, pos = positions(universe, syms)
    ind = indicators(syms, windows[0][1], END)

    for wname, lo, hi in windows:
        df = collect(universe, iv, C, pos, ind, lo, hi)
        print(f"\n  {wname}   n = {len(df):,} stock-months   "
              f"pool mean fwd 1M = {df['fwd'].mean():.2f}%")
        print(f"    {'measure':<14}{'corr':>9}{'t':>8}{'  decile-1 fwd':>15}{'decile-10 fwd':>15}{'  spread':>10}")
        for m in MEASURES:
            x, y = df[m].values, df["fwd"].values
            r = np.corrcoef(x, y)[0, 1]
            n = len(x)
            t = r * np.sqrt((n - 2) / max(1e-12, 1 - r * r))
            q = pd.qcut(df[m], 10, labels=False, duplicates="drop")
            d1, d10 = df["fwd"][q == q.min()].mean(), df["fwd"][q == q.max()].mean()
            print(f"    {m:<14}{r:>+9.4f}{t:>+8.2f}{d1:>14.2f}%{d10:>14.2f}%{d10-d1:>+10.2f}")
            results.setdefault((universe, m), {})[wname] = (r, t, n)

print(f"\n{'='*78}\nACCEPTANCE  — same sign in both windows AND |t|>2 in at least one\n{'='*78}")
print(f"{'universe':<20}{'measure':<14}{'W1 corr':>10}{'W1 t':>8}{'W2 corr':>10}{'W2 t':>8}   verdict")
for (u, m), w in results.items():
    (r1, t1, _), (r2, t2, _) = list(w.values())
    same = (r1 > 0) == (r2 > 0)
    strong = abs(t1) > 2 or abs(t2) > 2
    verdict = "PASS" if (same and strong) else ("sign flip" if not same else "weak")
    print(f"{u:<20}{m:<14}{r1:>+10.4f}{t1:>+8.2f}{r2:>+10.4f}{t2:>+8.2f}   {verdict}")
