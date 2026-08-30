"""Cap-tier composition of the broad-universe book over its journey.

For each month, classify every held name point-in-time into its NSE cap tier:
  Large  = NIFTY100 (rank 1-100), Mid = NIFTYMIDCAP150 (101-250),
  Small  = NIFTYSMALLCAP250 (251-500), Micro = NIFTYMICROCAP250 (501-750).
Reports the % of the book in each tier (avg over journey + per-year) for two books on the
combined N500∪Microcap250 PIT universe: aligned-stack HOLD-ALL and TOP-30 by 22d ROC.
Shows where the strategy actually invests and how the mix migrates as risers graduate up.
"""
import os, sys
from collections import defaultdict
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2022, 6, 1)
ROC_DAYS, K = 22, 30
THRESH = {"mean_long": 50, "mean_mid": 60, "mean_short": 80, "truevx_score": 90}
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
mem = {ix: load_membership(db, ix, anchor=date.today())
       for ix in ("NIFTY100", "NIFTYMIDCAP150", "NIFTYSMALLCAP250", "NIFTYMICROCAP250", "NIFTY500")}
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snaps={len(snaps)} ({snaps[0]}..{snaps[-1]})  symbols={len(syms)}")

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

TIERS = ["Large(N100)", "Mid(150)", "Small(250)", "Micro(250)", "Other"]


def tier_of(s, snapdate, cache):
    if s in cache:
        return cache[s]
    if s in members_as_of(mem["NIFTY100"], snapdate):
        t = "Large(N100)"
    elif s in members_as_of(mem["NIFTYMIDCAP150"], snapdate):
        t = "Mid(150)"
    elif s in members_as_of(mem["NIFTYSMALLCAP250"], snapdate):
        t = "Small(250)"
    elif s in members_as_of(mem["NIFTYMICROCAP250"], snapdate):
        t = "Micro(250)"
    else:
        t = "Other"
    cache[s] = t
    return t


def is_elig(s, d0):
    e = elig_at.get(s)
    return e is not None and e.get(d0) == True   # noqa: E712


# accumulate tier counts per book, overall and per-year
overall = {"hold_all": defaultdict(int), "top30": defaultdict(int)}
peryear = {"hold_all": defaultdict(lambda: defaultdict(int)), "top30": defaultdict(lambda: defaultdict(int))}
for t in range(len(snap_ts) - 1):
    d0 = snap_ts[t]; sd = snaps[t]; yr = sd.year
    broad = members_as_of(mem["NIFTY500"], sd) | members_as_of(mem["NIFTYMICROCAP250"], sd)
    elig = [s for s in broad if is_elig(s, d0) and close_at.get(s) is not None
            and np.isfinite(close_at[s].get(d0, np.nan))]
    top = sorted(elig, key=lambda s: roc_at[s].get(d0, -9e9) if np.isfinite(roc_at[s].get(d0, np.nan)) else -9e9,
                 reverse=True)[:K]
    tcache = {}
    for book, names in [("hold_all", elig), ("top30", top)]:
        for s in names:
            ti = tier_of(s, sd, tcache)
            overall[book][ti] += 1
            peryear[book][yr][ti] += 1


def pct_row(counts):
    tot = sum(counts.values()) or 1
    return [counts.get(ti, 0) / tot * 100 for ti in TIERS]


for book in ("hold_all", "top30"):
    print(f"\n=== {book} · cap-tier composition (% of holdings, 2022-06→2026-06) ===")
    print(f"{'':8}" + "".join(f"{ti:>13}" for ti in TIERS))
    p = pct_row(overall[book])
    print(f"{'AVG':8}" + "".join(f"{v:>12.1f}%" for v in p))
    print("  per-year:")
    for yr in sorted(peryear[book]):
        py = pct_row(peryear[book][yr])
        print(f"  {yr:6}" + "".join(f"{v:>12.1f}%" for v in py))
print("\n[point-in-time tier of each holding; Large=N100, Mid=Midcap150, Small=Smallcap250, Micro=Microcap250]")
