# Timing-luck / two-tranche analysis: replicate Alpha's selection (gate + mom20 top-25, EW,
# monthly) on schedules 'first' vs 'mid', frictionless, NIFTY500MICRO750 PIT.
import os, sys
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

REPO = r"D:\gs\workspace\tvagent\market-hunt"
sys.path.insert(0, REPO)
from membership import load_membership, members_as_of, union_over  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
START, END = date(2020, 1, 1), date(2026, 7, 3)
LOAD_FROM = datetime(2019, 6, 1)
U = "NIFTY500MICRO750"

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
sched = {
    "first": [g.iloc[0] for _, g in groups],
    "mid":   [g.iloc[len(g)//2] for _, g in groups],
}

def pick(d0, k=25):
    mem = members_as_of(iv, d0.date())
    cols = [s for s in closes.columns if s in mem]
    g = gate.loc[d0].reindex(cols)
    g = g.astype(object).where(g.notna(), False).astype(bool)
    m = mom20.loc[d0].reindex(cols)
    elig = m[g & m.notna()]
    return list(elig.nlargest(min(k, len(elig))).index)

def run(dates):
    dates = [d for d in dates if d in gate.index]
    curve = pd.Series(index=cal, dtype=float)
    val = 1.0
    holds = {}
    books = {}
    for i, d0 in enumerate(dates):
        S = pick(d0)
        books[d0] = set(S)
        d1 = dates[i+1] if i+1 < len(dates) else cal[-1]
        seg = cal[(cal >= d0) & (cal <= d1)]
        if len(S) == 0 or len(seg) < 2:
            curve.loc[seg] = val
            continue
        base = closes.loc[d0, S]
        rel = closes.loc[seg, S].div(base).ffill()
        pv = rel.mean(axis=1) * val   # EW buy-and-hold within the month
        curve.loc[seg] = pv.values
        val = pv.iloc[-1]
    return curve.ffill().dropna(), books

def stats(c):
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    cagr = (c.iloc[-1] / c.iloc[0]) ** (1/yrs) - 1
    sharpe = r.mean() / r.std() * np.sqrt(252)
    dd = (c / c.cummax() - 1).min()
    return cagr*100, sharpe, dd*100, r.std()*np.sqrt(252)*100

c_first, b_first = run(sched["first"])
c_mid, b_mid = run(sched["mid"])
idx = c_first.index.intersection(c_mid.index)
c_first, c_mid = c_first.loc[idx], c_mid.loc[idx]
combo = (c_first/c_first.iloc[0] + c_mid/c_mid.iloc[0]) / 2

print("=== frictionless panel replication, NIFTY500MICRO750, gate+mom20 top25 EW ===")
for name, c in [("first", c_first), ("mid", c_mid), ("50/50 combo", combo)]:
    cg, sh, dd, vol = stats(c)
    print(f"{name:12s} CAGR {cg:6.1f}%  Sharpe {sh:4.2f}  maxDD {dd:6.1f}%  vol {vol:5.1f}%")

rf, rm = c_first.pct_change().dropna(), c_mid.pct_change().dropna()
print(f"\ndaily return corr(first, mid): {rf.corr(rm):.3f}")
mf = c_first.resample('ME').last().pct_change().dropna()
mm_ = c_mid.resample('ME').last().pct_change().dropna()
print(f"monthly return corr(first, mid): {mf.corr(mm_):.3f}")

# holdings overlap between the two books (per calendar month)
ov = []
for d0f, Sf in b_first.items():
    key = (d0f.year, d0f.month)
    match = [S for dm, S in b_mid.items() if (dm.year, dm.month) == key]
    if match and Sf:
        Smid = match[0]
        ov.append(len(Sf & Smid) / max(len(Sf | Smid), 1))
print(f"avg Jaccard overlap of first-book vs mid-book (same month): {np.mean(ov):.2f}")
un = [len(Sf | ([S for dm,S in b_mid.items() if (dm.year,dm.month)==(d0f.year,d0f.month)] or [set()])[0]) for d0f, Sf in b_first.items()]
print(f"avg combined distinct names (union of both books): {np.mean(un):.1f}")

# month-to-month turnover of the mid book
bs = sorted(b_mid.items())
to = [1 - len(bs[i][1] & bs[i+1][1]) / max(len(bs[i][1]), 1) for i in range(len(bs)-1) if bs[i][1]]
print(f"avg month-over-month name turnover (mid book): {np.mean(to)*100:.0f}%")
print("DONE")
