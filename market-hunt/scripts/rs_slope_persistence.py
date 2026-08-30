"""Is the trend's ACCELERATION persistent? (does ml_slope predict its own future?)

ml_slope = mean_long - mean_long[3m ago]  (trend acceleration; the t=2.31 signal from #13).
A trend-dynamics model is only tradeable if slope persists -- if today's accelerating names
keep accelerating (and keep paying) tomorrow. Four honest tests, full PIT N500, monthly:

  A. Rank-IC of slope on its own FUTURE value, lags k=1/3/6/12m (Spearman, per-month, averaged).
     High & decaying-slowly = persistent. ~0 = acceleration is memoryless (untradeable).
  B. Quintile transition: sort by slope_t, show avg slope at t+3m per starting quintile.
     Monotone, preserved spread = persistent.
  C. Run-length of a rising trend: consecutive months with slope>0. Median/quartiles + hazard
     P(still rising next month | risen k months). Tells the DURATION of tradeable acceleration.
  D. Forward-return decay: slope quintile -> mean fwd 3m return over successive blocks
     (0-3, 3-6, 6-9, 9-12m). Does the return edge persist forward or fade/reverse?

No look-ahead in features; forward values are labels. Returns winsorized per-month [2%,98%].
"""
import os, sys
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2017, 1, 1)
RET_FROM, RET_TO = pd.Timestamp(2019, 1, 1), pd.Timestamp(2025, 6, 1)
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


def ml_monthly(symbol):
    irows = list(db["indicators"].find(
        {"symbol": symbol, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_long": 1}))
    if not irows:
        return None
    idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
    s = pd.Series([r["data"].get("mean_long") for r in irows], index=idx).sort_index()
    return s.resample("ME").last()


iv500 = load_membership(db, "NIFTY500", anchor=date.today())
members = members_as_of(iv500, RET_TO.date())
print(f"PIT N500 @ {RET_TO.date()}: {len(members)}")

rows, runs = [], []
for sym in members:
    ml = ml_monthly(sym)
    px = close_series(sym)
    if ml is None or px is None:
        continue
    pxm = px.resample("ME").last()
    slope = ml.diff(3)
    df = pd.DataFrame({"ml": ml, "slope": slope, "px": pxm.reindex(ml.index)})
    df["sf1"] = df["slope"].shift(-1)
    df["sf3"] = df["slope"].shift(-3)
    df["sf6"] = df["slope"].shift(-6)
    df["sf12"] = df["slope"].shift(-12)
    df["r03"] = df["px"].shift(-3) / df["px"] - 1
    df["r36"] = df["px"].shift(-6) / df["px"].shift(-3) - 1
    df["r69"] = df["px"].shift(-9) / df["px"].shift(-6) - 1
    df["r912"] = df["px"].shift(-12) / df["px"].shift(-9) - 1
    df = df[(df.index >= RET_FROM) & (df.index <= RET_TO)]
    for ts, r in df.iterrows():
        rows.append((ts, sym, r["ml"], r["slope"], r["sf1"], r["sf3"], r["sf6"], r["sf12"],
                     r["r03"], r["r36"], r["r69"], r["r912"]))
    # C: run lengths of consecutive slope>0 (use full series, not just window)
    rising = (slope > 0).dropna().astype(int).values
    c = 0
    for v in rising:
        if v:
            c += 1
        elif c > 0:
            runs.append(c); c = 0
    if c > 0:
        runs.append(c)

P = pd.DataFrame(rows, columns=["month", "sym", "ml", "slope", "sf1", "sf3", "sf6", "sf12",
                                "r03", "r36", "r69", "r912"])
for c in ("r03", "r36", "r69", "r912"):
    P[c] = P.groupby("month")[c].transform(lambda s: s.clip(s.quantile(.02), s.quantile(.98)))
print(f"panel rows: {len(P)}  names: {P['sym'].nunique()}  months: {P['month'].nunique()}\n")


def rank_ic(a, b, g):
    """avg per-month Spearman (rank-Pearson) between cols a,b"""
    ics = []
    for _, x in g:
        d = x[[a, b]].dropna()
        if len(d) < 25:
            continue
        ra, rb = d[a].rank(), d[b].rank()
        if ra.std() == 0 or rb.std() == 0:
            continue
        ics.append(np.corrcoef(ra, rb)[0, 1])
    return np.mean(ics), len(ics)


print("=== A. persistence of slope: rank-IC of slope_t vs slope_{t+k} ===")
g = list(P.groupby("month"))
for lag, col in [(1, "sf1"), (3, "sf3"), (6, "sf6"), (12, "sf12")]:
    ic, n = rank_ic("slope", col, g)
    print(f"  lag {lag:2d}m   rank-IC = {ic:5.2f}   (n_months={n})")
print("  [1.0=perfectly persistent, 0=memoryless. Decay across lags = how fast acceleration forgets]")

print("\n=== B. quintile transition: sort by slope_t -> avg slope at t+3m ===")
def quintile_future(col):
    out = []
    for _, x in g:
        d = x[["slope", col]].dropna()
        if len(d) < 25:
            continue
        d = d.assign(q=pd.qcut(d["slope"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]))
        out.append(d.groupby("q", observed=True)[col].mean())
    return pd.concat(out, axis=1).mean(axis=1)
qf = quintile_future("sf3")
print(f"  {'start quintile (slope_t)':28}" + "".join(f"Q{i}".rjust(9) for i in range(1, 6)))
print(f"  {'avg slope at t+3m':28}" + "".join(f"{qf.get(i, np.nan):9.2f}" for i in range(1, 6)))
print("  [monotone Q1<...<Q5 with preserved gap = high-slope names stay high-slope]")

print("\n=== C. run-length of a rising trend (consecutive months slope>0) ===")
R = np.array(runs)
print(f"  runs={len(R)}  median={np.median(R):.0f}mo  mean={R.mean():.1f}mo  "
      f"p75={np.percentile(R,75):.0f}mo  p90={np.percentile(R,90):.0f}mo  max={R.max()}mo")
print(f"  {'survived k months:':20}" + "".join(f"{k:>6}" for k in range(1, 13)))
surv = [(R >= k).mean() for k in range(1, 14)]
haz = [surv[k] / surv[k - 1] if surv[k - 1] > 0 else 0 for k in range(1, 13)]
print(f"  {'P(continue | at k):':20}" + "".join(f"{h:6.2f}" for h in haz))
print("  [hazard rising toward 1 = momentum (longer it's risen, more likely to continue)]")

print("\n=== D. forward-return decay by slope_t quintile (median fwd 3m %, successive blocks) ===")
def block_by_q(col):
    out = []
    for _, x in g:
        d = x[["slope", col]].dropna()
        if len(d) < 25:
            continue
        d = d.assign(q=pd.qcut(d["slope"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]))
        out.append(d.groupby("q", observed=True)[col].median())
    return pd.concat(out, axis=1).mean(axis=1) * 100
print(f"  {'block':12}" + "".join(f"Q{i}".rjust(8) for i in range(1, 6)) + "   Q5-Q1")
for lbl, col in [("0-3m", "r03"), ("3-6m", "r36"), ("6-9m", "r69"), ("9-12m", "r912")]:
    b = block_by_q(col)
    spread = b.get(5, np.nan) - b.get(1, np.nan)
    print(f"  {lbl:12}" + "".join(f"{b.get(i, np.nan):8.1f}" for i in range(1, 6)) + f"  {spread:6.1f}")
print("  [Q5=fastest-accelerating now. Spread shrinking across blocks = edge decays; "
      "flipping negative = reverses]")
