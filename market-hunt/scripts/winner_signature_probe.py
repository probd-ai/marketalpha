"""Winner autopsy: find the names that rallied hardest 2019->2025 and read off the
RS *trajectory* (signature) they traced.

DESCRIPTIVE / HYPOTHESIS-GENERATION ONLY. We deliberately select on the outcome
(today's biggest winners) to learn WHAT SHAPE to look for. This is survivorship by
design and is NOT a tradeable result -- it tells us which trajectory features to test
honestly (with prospective labels + walk-forward) later. No look-ahead guard needed
because nothing here is traded.

For each top winner we extract the monthly RS path (truevx_score, mean_short, mean_mid,
mean_long) and compute descriptive signature stats:
  - ret      : price multiple 2019-01 -> 2025-06
  - ml_rise  : fraction of months mean_long is higher than 3 months earlier (trend persistence)
  - ml_med   : median mean_long level (how high it lived)
  - breaths  : # of 'breathe-in' events -- mean_short dips below mean_mid then crosses
               back above, WHILE mean_long>50 (oscillation inside an intact uptrend)
  - dip_dpth : median trough of (mean_short - mean_long) during those dips (how deep it breathes in)
  - base_mo  : months before the trajectory first locked in (mean_long first crosses & holds >50)
Writes per-name monthly paths to scratchpad CSVs for charting.
"""
import os, sys
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
RET_FROM, RET_TO = date(2019, 1, 1), date(2025, 6, 1)
TOP_N = 12
OUT = r"C:\Users\ARPITH~1\AppData\Local\Temp\claude\D--gs-workspace-tvagent\de871824-7cb6-4019-977c-e90deead06f5\scratchpad"
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


def rs_path(symbol):
    irows = list(db["indicators"].find(
        {"symbol": symbol, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.truevx_score": 1, "data.mean_short": 1,
         "data.mean_mid": 1, "data.mean_long": 1}))
    if not irows:
        return None
    idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
    d = pd.DataFrame({
        "score": [r["data"].get("truevx_score") for r in irows],
        "ms": [r["data"].get("mean_short") for r in irows],
        "mm": [r["data"].get("mean_mid") for r in irows],
        "ml": [r["data"].get("mean_long") for r in irows],
    }, index=idx).sort_index()
    return d


# universe = today's PIT N500 members (we want names that exist now and rallied)
iv500 = load_membership(db, "NIFTY500", anchor=date.today())
members = members_as_of(iv500, RET_TO)
print(f"PIT N500 members @ {RET_TO}: {len(members)}")

# rank by price multiple over the window
rets = []
for sym in members:
    px = close_series(sym)
    if px is None:
        continue
    a = px[(px.index >= pd.Timestamp(RET_FROM)) & (px.index <= pd.Timestamp(RET_FROM) + pd.Timedelta(days=45))]
    b = px[(px.index >= pd.Timestamp(RET_TO) - pd.Timedelta(days=45)) & (px.index <= pd.Timestamp(RET_TO) + pd.Timedelta(days=15))]
    if a.empty or b.empty:
        continue
    rets.append((sym, b.iloc[-1] / a.iloc[0]))
rets.sort(key=lambda x: x[1], reverse=True)
winners = rets[:TOP_N]
print(f"\nTop {TOP_N} winners 2019-01 -> 2025-06 (price multiple):")
for sym, m in winners:
    print(f"  {sym:24} {m:6.1f}x")


def signature(d):
    dm = d.resample("MS").last().dropna()
    if len(dm) < 12:
        return None
    ml = dm["ml"]
    ml_rise = float((ml > ml.shift(3)).mean())
    ml_med = float(ml.median())
    # base: months until mean_long first crosses 50 and stays >40 thereafter
    above = ml > 50
    base_mo = int(above.values.argmax()) if above.any() else len(dm)
    # breathe-in events: ms dips below mm then back above, while ml>50
    s_below = (dm["ms"] < dm["mm"])
    breaths, dips = 0, []
    in_dip, trough = False, None
    for t in range(1, len(dm)):
        if dm["ml"].iloc[t] <= 50:
            in_dip, trough = False, None
            continue
        if s_below.iloc[t]:
            in_dip = True
            tv = dm["ms"].iloc[t] - dm["ml"].iloc[t]
            trough = tv if trough is None else min(trough, tv)
        elif in_dip:                      # crossed back above mm -> breath complete
            breaths += 1
            if trough is not None:
                dips.append(trough)
            in_dip, trough = False, None
    dip_dpth = float(np.median(dips)) if dips else np.nan
    return dict(ml_rise=ml_rise, ml_med=ml_med, base_mo=base_mo,
                breaths=breaths, dip_dpth=dip_dpth, months=len(dm))


print(f"\n{'symbol':24} {'ret':>6} {'ml_rise':>7} {'ml_med':>7} {'base_mo':>7} {'breaths':>7} {'dip_dpth':>8} {'months':>6}")
os.makedirs(OUT, exist_ok=True)
sig_rows = []
for sym, m in winners:
    d = rs_path(sym)
    if d is None:
        print(f"  {sym:24} (no RS data)")
        continue
    sg = signature(d)
    if sg is None:
        print(f"  {sym:24} (short RS history)")
        continue
    d.resample("MS").last().to_csv(os.path.join(OUT, f"winrs_{sym.replace(' ', '_')}.csv"))
    sig_rows.append((sym, m, sg))
    print(f"  {sym:24} {m:6.1f} {sg['ml_rise']:7.2f} {sg['ml_med']:7.1f} {sg['base_mo']:7d} "
          f"{sg['breaths']:7d} {sg['dip_dpth']:8.1f} {sg['months']:6d}")

if sig_rows:
    print("\n=== signature centroid (median across winners) ===")
    for k in ("ml_rise", "ml_med", "base_mo", "breaths", "dip_dpth"):
        vals = [s[k] for _, _, s in sig_rows if np.isfinite(s[k])]
        print(f"  {k:10} median={np.median(vals):.2f}")
print(f"\n[descriptive / survivorship-by-design; per-name monthly RS paths -> {OUT}\\winrs_*.csv]")
