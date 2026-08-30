# Staggered-execution test: selection FROZEN at rebalance date T (gate + mom20
# top-25, PIT NIFTY500MICRO750, monthly mid), execution at close of T+k for
# k=0..4. Compares all-at-T vs the 5-day staggered average book (1/5 each day).
# Frictionless EW — the DELTA between policies is the object of interest.
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

REPO = "D:/gs/workspace/tvagent/market-hunt"
sys.path.insert(0, REPO)
from membership import load_membership, members_as_of, union_over  # noqa: E402

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
START, END = date(2022, 1, 1), date(2026, 7, 4)
LOAD_FROM = datetime(2021, 6, 1)
U = "NIFTY500MICRO750"
K_MAX = 5  # execution days T..T+4

iv = load_membership(db, U, anchor=date.today())
ever = sorted(union_over(iv, START, END))

prows = []
for cname in sorted(db.list_collection_names()):
    if not cname.startswith("prices_"):
        continue
    cur = db[cname].find({"symbol": {"$in": ever}, "date": {"$gte": LOAD_FROM}},
                         {"symbol": 1, "date": 1, "close_price": 1, "_id": 0}).batch_size(50000)
    prows += [(d["symbol"], d["date"], d["close_price"]) for d in cur]
px = pd.DataFrame(prows, columns=["symbol", "date", "close"])
px["date"] = pd.to_datetime(px["date"]).dt.normalize()
px = px.drop_duplicates(["symbol", "date"], keep="last")
closes = px.pivot(index="date", columns="symbol", values="close").sort_index()

irows = []
cur = db["indicators"].find(
    {"indicator_type": "truevx", "symbol": {"$in": ever}, "date": {"$gte": LOAD_FROM}},
    {"symbol": 1, "date": 1, "data.truevx_score": 1, "data.mean_short": 1,
     "data.mean_mid": 1, "data.mean_long": 1, "_id": 0}).batch_size(50000)
irows = [(d["symbol"], d["date"], (d.get("data") or {}).get("truevx_score"),
          (d.get("data") or {}).get("mean_short"), (d.get("data") or {}).get("mean_mid"),
          (d.get("data") or {}).get("mean_long")) for d in cur]
ind = pd.DataFrame(irows, columns=["symbol", "date", "score", "ms", "mm", "ml"])
ind["date"] = pd.to_datetime(ind["date"]).dt.normalize()
ind = ind.drop_duplicates(["symbol", "date"], keep="last")
score = ind.pivot(index="date", columns="symbol", values="score").sort_index()
ms = ind.pivot(index="date", columns="symbol", values="ms").sort_index()
mm = ind.pivot(index="date", columns="symbol", values="mm").sort_index()
ml = ind.pivot(index="date", columns="symbol", values="ml").sort_index()
gate = (score > 90) & (ms > 80) & (mm > 60) & (ml > 50)
mom20 = closes.pct_change(20) * 100

cal = closes.index.intersection(score.index)
cal = cal[(cal >= pd.Timestamp(START)) & (cal <= pd.Timestamp(END))]
groups = pd.Series(cal, index=cal).groupby([cal.year, cal.month])
T_dates = [g.iloc[len(g) // 2] for _, g in groups]
cal_pos = {d: i for i, d in enumerate(cal)}


def select(T, k=25):
    mem = members_as_of(iv, T.date())
    cols = [s for s in closes.columns if s in mem]
    g = gate.loc[T].reindex(cols)
    g = g.astype(object).where(g.notna(), False).astype(bool)
    m = mom20.loc[T].reindex(cols)
    elig = m[g & m.notna()]
    return list(elig.nlargest(min(k, len(elig))).index)


selections = {T: select(T) for T in T_dates if T in gate.index}
T_list = [T for T in T_dates if T in selections and selections[T]]

print(f"rebalances: {len(T_list)}, avg selection {np.mean([len(selections[T]) for T in T_list]):.1f}", flush=True)


def run_book(offset: int):
    """EW book: selection from T, bought at close of cal[T+offset], held to next T+offset."""
    curve = pd.Series(index=cal, dtype=float)
    val = 1.0
    for i, T in enumerate(T_list):
        S = selections[T]
        i0 = min(cal_pos[T] + offset, len(cal) - 1)
        d0 = cal[i0]
        if i + 1 < len(T_list):
            i1 = min(cal_pos[T_list[i + 1]] + offset, len(cal) - 1)
        else:
            i1 = len(cal) - 1
        d1 = cal[i1]
        seg = cal[(cal >= d0) & (cal <= d1)]
        if len(seg) < 2:
            curve.loc[seg] = val
            continue
        base = closes.loc[d0, S]
        rel = closes.loc[seg, S].div(base)
        rel = rel.ffill()
        pv = rel.mean(axis=1) * val
        curve.loc[seg] = pv.values
        val = pv.iloc[-1]
    return curve.ffill().dropna()


def stats(c):
    c = c.dropna()
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    cagr = (c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    dd = (c / c.cummax() - 1).min()
    return cagr * 100, sharpe, dd * 100


books = {k: run_book(k) for k in range(K_MAX)}
# align to the common window where every offset book exists (T0+4 onward)
start = max(b.index[0] for b in books.values())
end = min(b.index[-1] for b in books.values())
idx = books[0].index[(books[0].index >= start) & (books[0].index <= end)]
for k in books:
    books[k] = books[k].reindex(idx).ffill()
stagger = sum(books[k] / books[k].iloc[0] for k in range(K_MAX)) / K_MAX

print("\n=== execution policy comparison (frictionless, Alpha config, 2022->2026) ===")
for k in range(K_MAX):
    cg, sh, dd = stats(books[k])
    print(f"execute T+{k} only : CAGR {cg:6.2f}%  Sharpe {sh:5.2f}  maxDD {dd:6.1f}%", flush=True)
cg, sh, dd = stats(stagger)
print(f"staggered T..T+4  : CAGR {cg:6.2f}%  Sharpe {sh:5.2f}  maxDD {dd:6.1f}%")

# entry-price drift: how much more expensive is the same basket at T+k vs T?
drift = {k: [] for k in range(1, K_MAX)}
for T in T_list:
    S = selections[T]
    p0 = closes.loc[T, S]
    for k in range(1, K_MAX):
        i = min(cal_pos[T] + k, len(cal) - 1)
        pk = closes.loc[cal[i], S]
        rel = (pk / p0 - 1) * 100
        drift[k].append(rel.mean())
print("\n=== basket entry-price drift vs decision-day close (mean per rebalance) ===")
for k in range(1, K_MAX):
    d = pd.Series(drift[k])
    print(f"T+{k}: {d.mean():+.2f}% (median {d.median():+.2f}%, std {d.std():.2f}%)")

# variance-reduction check: dispersion of monthly outcomes, day0 vs staggered
m0 = books[0].resample("ME").last().pct_change().dropna()
msg = stagger.resample("ME").last().pct_change().dropna()
print(f"\nmonthly return std: T+0 only {m0.std()*100:.2f}%  vs staggered {msg.std()*100:.2f}%")
print(f"daily corr(T+0, staggered): {books[0].pct_change().corr(stagger.pct_change()):.3f}")
print("ALL DONE", flush=True)
