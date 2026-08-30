# Mechanism analysis for strategy "Alpha" (truevx gate + 20d-return top-25, monthly mid, EW).
# Answers: why does it work, and why does performance scale with universe breadth?
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
LOAD_FROM = datetime(2019, 6, 1)  # lookback headroom for 20d returns

UNIVERSES = ["NIFTY500MICRO750", "NIFTY500", "NIFTY200"]

print("loading membership ...", flush=True)
IV = {u: load_membership(db, u, anchor=date.today()) for u in UNIVERSES}
ever_all = sorted(set().union(*[union_over(IV[u], START, END) for u in UNIVERSES]))
print(f"ever-members across universes: {len(ever_all)}", flush=True)

# ---- prices ----
print("loading prices ...", flush=True)
prows = []
for cname in sorted(db.list_collection_names()):
    if not cname.startswith("prices_"):
        continue
    cur = db[cname].find({"symbol": {"$in": ever_all}, "date": {"$gte": LOAD_FROM}},
                         {"symbol": 1, "date": 1, "close_price": 1, "_id": 0}).batch_size(50000)
    prows += [(d["symbol"], d["date"], d["close_price"]) for d in cur]
px = pd.DataFrame(prows, columns=["symbol", "date", "close"])
px["date"] = pd.to_datetime(px["date"]).dt.normalize()
px = px.drop_duplicates(["symbol", "date"], keep="last")
closes = px.pivot(index="date", columns="symbol", values="close").sort_index()
print(f"closes: {closes.shape}", flush=True)

# ---- indicators (gate fields) ----
print("loading indicators ...", flush=True)
irows = []
cur = db["indicators"].find(
    {"indicator_type": "truevx", "symbol": {"$in": ever_all}, "date": {"$gte": LOAD_FROM}},
    {"symbol": 1, "date": 1, "data.truevx_score": 1, "data.mean_short": 1,
     "data.mean_mid": 1, "data.mean_long": 1, "_id": 0}).batch_size(50000)
for d in cur:
    dd = d.get("data") or {}
    irows.append((d["symbol"], d["date"], dd.get("truevx_score"), dd.get("mean_short"),
                  dd.get("mean_mid"), dd.get("mean_long")))
ind = pd.DataFrame(irows, columns=["symbol", "date", "score", "ms", "mm", "ml"])
ind["date"] = pd.to_datetime(ind["date"]).dt.normalize()
ind = ind.drop_duplicates(["symbol", "date"], keep="last")
score = ind.pivot(index="date", columns="symbol", values="score").sort_index()
ms = ind.pivot(index="date", columns="symbol", values="ms").sort_index()
mm = ind.pivot(index="date", columns="symbol", values="mm").sort_index()
ml = ind.pivot(index="date", columns="symbol", values="ml").sort_index()
print(f"indicators: {score.shape}", flush=True)

# align calendars: engine trades dates having BOTH price and indicator data
cal = closes.index.intersection(score.index)
cal = cal[(cal >= pd.Timestamp(START)) & (cal <= pd.Timestamp(END))]
closes_c = closes.reindex(closes.index)  # full for returns lookback
mom20 = closes.pct_change(20) * 100     # 20-trading-bar return, engine-equivalent

# gate mask (Alpha's filter)
gate = (score > 90) & (ms > 80) & (mm > 60) & (ml > 50)

# mid-of-month rebalance dates on the joint calendar (engine: middle trading day)
months = pd.Series(cal, index=cal).groupby([cal.year, cal.month])
mid_dates = [g.iloc[len(g)//2] for _, g in months]
mid_dates = [d for d in mid_dates if d <= cal[-1]]
# forward return: mid-date to next mid-date
fwd = {}
for i in range(len(mid_dates) - 1):
    d0, d1 = mid_dates[i], mid_dates[i+1]
    fwd[d0] = (closes.loc[d1] / closes.loc[d0] - 1) * 100

nifty50 = None
try:
    n50 = db["prices_2020_2024"].find({"symbol": "Nifty 50"}, {"date":1,"close_price":1,"_id":0})
except Exception:
    pass

results = {}
for u in UNIVERSES:
    iv = IV[u]
    rows = []
    for i in range(len(mid_dates) - 1):
        d0 = mid_dates[i]
        elig_mem = members_as_of(iv, d0.date())
        cols = [s for s in closes.columns if s in elig_mem]
        if d0 not in gate.index:
            continue
        g = gate.loc[d0].reindex(cols).fillna(False)
        m = mom20.loc[d0].reindex(cols)
        f = fwd[d0].reindex(cols)
        ok = g & m.notna() & f.notna()
        elig = pd.DataFrame({"mom": m[ok], "fwd": f[ok]})
        uni_f = f.dropna()
        row = dict(date=d0, n_members=len(cols), n_elig=len(elig),
                   uni_ew_fwd=uni_f.mean(), uni_disp=uni_f.std())
        if len(elig) >= 3:
            row["elig_ew_fwd"] = elig["fwd"].mean()
            row["elig_disp"] = elig["fwd"].std()
            row["rank_ic"] = elig["mom"].corr(elig["fwd"], method="spearman") if len(elig) >= 5 else np.nan
            top = elig.nlargest(min(25, len(elig)), "mom")
            row["top25_fwd"] = top["fwd"].mean()
            row["n_top"] = len(top)
        rows.append(row)
    df = pd.DataFrame(rows).set_index("date")
    results[u] = df
    df.to_csv(os.path.join(OUT, f"mech_{u}.csv"))

# ---- micro-only contribution inside the 750 book ----
u750, u500 = results["NIFTY500MICRO750"], None
micro_rows = []
for i in range(len(mid_dates) - 1):
    d0 = mid_dates[i]
    mem750 = members_as_of(IV["NIFTY500MICRO750"], d0.date())
    mem500 = members_as_of(IV["NIFTY500"], d0.date())
    micro_only = mem750 - mem500
    cols = [s for s in closes.columns if s in mem750]
    if d0 not in gate.index:
        continue
    g = gate.loc[d0].reindex(cols).fillna(False)
    m = mom20.loc[d0].reindex(cols); f = fwd[d0].reindex(cols)
    ok = g & m.notna() & f.notna()
    elig = pd.DataFrame({"mom": m[ok], "fwd": f[ok]})
    if len(elig) < 3:
        continue
    top = elig.nlargest(min(25, len(elig)), "mom")
    im = top.index.isin(micro_only)
    micro_rows.append(dict(date=d0, n_top=len(top), n_micro=int(im.sum()),
                           micro_fwd=top["fwd"][im].mean() if im.any() else np.nan,
                           n500_fwd=top["fwd"][~im].mean() if (~im).any() else np.nan))
micro_df = pd.DataFrame(micro_rows).set_index("date")
micro_df.to_csv(os.path.join(OUT, "mech_micro_contrib.csv"))

# ---- summary print ----
def tstat(x):
    x = x.dropna()
    return x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan

print("\n================ SUMMARY (monthly cross-sections, mid-month, PIT) ================")
for u in UNIVERSES:
    df = results[u]
    print(f"\n--- {u} ---")
    print(f"members avg: {df['n_members'].mean():.0f} | eligibles avg: {df['n_elig'].mean():.1f} "
          f"(median {df['n_elig'].median():.0f}, min {df['n_elig'].min():.0f}, max {df['n_elig'].max():.0f})")
    print(f"months with <25 eligibles: {(df['n_elig'] < 25).mean()*100:.0f}%")
    print(f"universe EW fwd 1M: {df['uni_ew_fwd'].mean():+.2f}%  | eligibles EW fwd: {df['elig_ew_fwd'].mean():+.2f}%  "
          f"| top25-by-mom20 fwd: {df['top25_fwd'].mean():+.2f}%")
    print(f"gate spread (elig - uni): {(df['elig_ew_fwd']-df['uni_ew_fwd']).mean():+.2f}%/mo  t={tstat(df['elig_ew_fwd']-df['uni_ew_fwd']):.2f}")
    print(f"ranker spread (top25 - elig): {(df['top25_fwd']-df['elig_ew_fwd']).mean():+.2f}%/mo  t={tstat(df['top25_fwd']-df['elig_ew_fwd']):.2f}")
    print(f"rank IC (mom20 vs fwd, among eligibles): mean {df['rank_ic'].mean():+.3f}  t={tstat(df['rank_ic']):.2f}")
    print(f"cross-sec dispersion: universe {df['uni_disp'].mean():.1f}%  eligibles {df['elig_disp'].mean():.1f}%")

print("\n--- micro-only names inside the 750 top-25 book ---")
print(f"avg micro-only names in top25: {micro_df['n_micro'].mean():.1f} / {micro_df['n_top'].mean():.1f}")
print(f"fwd 1M: micro-only {micro_df['micro_fwd'].mean():+.2f}%  vs N500 names {micro_df['n500_fwd'].mean():+.2f}%")
print("\nDONE")
