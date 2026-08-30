"""Build the research panel: (symbol, date) x {close, truevx metrics, fwd returns, PIT flags}.

Output: panel.parquet in scratchpad. One-off, ~3M rows.
"""
import sys, os, time
sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
os.chdir(r"D:\gs\workspace\tvagent\market-hunt")
import numpy as np
import pandas as pd
from pymongo import MongoClient
from datetime import date, datetime

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.parquet")
db = MongoClient("mongodb://localhost:27017")["market_hunt"]

t0 = time.time()

# ---- indicators (2.7M docs) ----
rows = []
cur = db["indicators"].find(
    {"indicator_type": "truevx", "base_symbol": "Nifty 50"},
    {"symbol": 1, "date": 1, "data.truevx_score": 1, "data.mean_short": 1,
     "data.mean_mid": 1, "data.mean_long": 1, "data.structural_score": 1,
     "data.trend_score": 1, "_id": 0},
).batch_size(50000)
for d in cur:
    dd = d.get("data") or {}
    rows.append((d["symbol"], d["date"], dd.get("truevx_score"), dd.get("mean_short"),
                 dd.get("mean_mid"), dd.get("mean_long"), dd.get("structural_score"),
                 dd.get("trend_score")))
ind = pd.DataFrame(rows, columns=["symbol", "date", "score", "ms", "mm", "ml", "struct", "trend"])
ind["date"] = pd.to_datetime(ind["date"]).dt.normalize()
print(f"indicators: {len(ind):,} rows, {ind.symbol.nunique()} symbols  [{time.time()-t0:.0f}s]")

# ---- prices for those symbols (partitioned collections) ----
syms = set(ind["symbol"].unique())
prows = []
for cname in sorted(db.list_collection_names()):
    if not cname.startswith("prices_"):
        continue
    cur = db[cname].find({"symbol": {"$in": list(syms)}},
                         {"symbol": 1, "date": 1, "close_price": 1, "_id": 0}).batch_size(50000)
    for d in cur:
        prows.append((d["symbol"], d["date"], d["close_price"]))
px = pd.DataFrame(prows, columns=["symbol", "date", "close"])
px["date"] = pd.to_datetime(px["date"]).dt.normalize()
px = px.drop_duplicates(["symbol", "date"], keep="last")
print(f"prices: {len(px):,} rows  [{time.time()-t0:.0f}s]")

# ---- benchmark (Nifty 500 as broad market) ----
brows = []
for cname in sorted(db.list_collection_names()):
    if not cname.startswith("prices_"):
        continue
    for d in db[cname].find({"symbol": "Nifty 500"}, {"date": 1, "close_price": 1, "_id": 0}):
        brows.append((d["date"], d["close_price"]))
bench = pd.DataFrame(brows, columns=["date", "bench"]).drop_duplicates("date")
bench["date"] = pd.to_datetime(bench["date"]).dt.normalize()
bench = bench.sort_values("date").reset_index(drop=True)
print(f"bench rows: {len(bench):,}")

# ---- merge ----
df = ind.merge(px, on=["symbol", "date"], how="inner")
df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
df = df.merge(bench, on="date", how="left")
print(f"panel after merge: {len(df):,} rows  [{time.time()-t0:.0f}s]")

# ---- forward & trailing returns (per symbol, trading-day offsets) ----
g = df.groupby("symbol", sort=False)
for h in (1, 5, 10, 20):
    df[f"fr{h}"] = g["close"].shift(-h) / df["close"] - 1.0
    df[f"bfr{h}"] = g["bench"].shift(-h) / df["bench"] - 1.0
    df[f"xr{h}"] = df[f"fr{h}"] - df[f"bfr{h}"]          # market-relative forward return
df["roc20"] = df["close"] / g["close"].shift(20) - 1.0     # trailing momentum baseline
df["roc5"] = df["close"] / g["close"].shift(5) - 1.0
df["d5_score"] = df["score"] - g["score"].shift(5)         # 5d change in score
df["d5_ms"] = df["ms"] - g["ms"].shift(5)
df["gap_sms"] = df["score"] - df["ms"]                     # score vs mean_short gap

# ---- PIT membership flags (N100, N500) ----
from membership import load_membership
for uni, col in (("NIFTY100", "in_n100"), ("NIFTY500", "in_n500")):
    try:
        intervals = load_membership(db, uni, date(2026, 6, 24))
    except ValueError as e:
        print(f"membership {uni} unavailable: {e}")
        df[col] = False
        continue
    # Build per-symbol boolean via interval join (intervals: list[MembershipInterval])
    by_sym = {}
    for iv in intervals:
        by_sym.setdefault(iv.symbol, []).append(
            (iv.effective_from or date(1900, 1, 1), iv.effective_to or date(2099, 1, 1)))
    df[col] = False
    dts = df["date"].dt.date
    for sym, ivals in by_sym.items():
        m = df["symbol"] == sym
        if not m.any():
            continue
        sub = dts[m]
        flag = pd.Series(False, index=sub.index)
        for s, e in ivals:
            flag |= (sub >= s) & (sub <= e)
        df.loc[flag[flag].index, col] = True
    print(f"{col}: {df[col].sum():,} member-rows  [{time.time()-t0:.0f}s]")

df.to_parquet(OUT, index=False)
print(f"WROTE {OUT}  {len(df):,} rows x {len(df.columns)} cols  [{time.time()-t0:.0f}s]")
print(df["date"].min(), "->", df["date"].max())
