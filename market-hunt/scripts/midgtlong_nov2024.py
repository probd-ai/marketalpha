"""Stocks with mean_mid > mean_long as of Nov 2024 (latest reading within the month).

Scope: point-in-time NIFTY 500 members as of 2024-11-30. For each, take its latest TrueVX
reading in Nov 2024 and flag mean_mid > mean_long. Sorted by margin (mid - long) desc.
Writes full list to CSV in scratchpad.
"""
import os, sys
from datetime import date, datetime
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

LO, HI = datetime(2024, 11, 1), datetime(2024, 11, 30, 23, 59)
OUT = r"C:\Users\ARPITH~1\AppData\Local\Temp\claude\D--gs-workspace-tvagent\de871824-7cb6-4019-977c-e90deead06f5\scratchpad\midgtlong_nov2024.csv"
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]

iv500 = load_membership(db, "NIFTY500", anchor=date.today())
members = set(members_as_of(iv500, date(2024, 11, 30)))

rows = list(db["indicators"].find(
    {"indicator_type": "truevx", "date": {"$gte": LO, "$lte": HI}},
    {"_id": 0, "symbol": 1, "date": 1, "data.mean_mid": 1, "data.mean_long": 1}))

latest = {}
for r in rows:
    s = r["symbol"]
    if s not in members:
        continue
    d = pd.to_datetime(r["date"])
    if s not in latest or d > latest[s][0]:
        latest[s] = (d, r["data"].get("mean_mid"), r["data"].get("mean_long"))

recs = []
for s, (d, mid, lon) in latest.items():
    if mid is None or lon is None:
        continue
    recs.append((s, d.date(), round(mid, 1), round(lon, 1), round(mid - lon, 1), mid > lon))

df = pd.DataFrame(recs, columns=["symbol", "date", "mean_mid", "mean_long", "margin", "mid_gt_long"])
hits = df[df["mid_gt_long"]].sort_values("margin", ascending=False).reset_index(drop=True)
df.sort_values("margin", ascending=False).to_csv(OUT, index=False)

print(f"PIT N500 members @ 2024-11-30 with a Nov-2024 TrueVX reading: {len(df)}")
print(f"mean_mid > mean_long: {len(hits)}  ({len(hits)/len(df)*100:.0f}%)\n")
print(hits[["symbol", "mean_mid", "mean_long", "margin"]].to_string(index=False))
print(f"\n[full list incl. mid<long written to: {OUT}]")
