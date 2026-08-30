"""RS-wave -> price model substrate.

Decompose each name's relative strength into TREND + OSCILLATOR ("the breath"):
    trend     = mean_long                       (slow component, the regime)
    ml_slope  = mean_long - mean_long[3m ago]   (trend direction)
    breath    = mean_short - mean_long          (fast minus slow; oscillates around 0)
                  breath < 0  -> inhale  (fast RS dipped below trend; a pullback)
                  breath > 0  -> exhale  (fast RS extended above trend; over-bought)
    breath_chg= breath - breath[1m ago]         (phase: >0 recovering, <0 rolling over)

Then ask, across the WHOLE PIT N500 (no survivorship -- all names, all months are
rows; forward return is only the label): how does forward 3-month PRICE return depend
on the wave state? Three views:
  A. winners-vs-field contrast on the per-name signature stats (does the shape separate?)
  B. forward-return SURFACE over (trend band x breath band) -- the empirical relation
  C. Fama-MacBeth cross-sectional fit -> standardized coefficients = the draft equation,
     run full-sample and split by trend regime to test if the oscillator pays only
     inside an uptrend (the 'breathe-in around a rising trend' thesis).

No look-ahead: every feature at month t uses data <= t; forward return is t -> t+3.
Forward returns winsorized per-month at [2%,98%] so one multibagger can't carry a cell.
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
H = int(sys.argv[1]) if len(sys.argv) > 1 else 3  # forward horizon, months
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


def rs_monthly(symbol):
    irows = list(db["indicators"].find(
        {"symbol": symbol, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_short": 1, "data.mean_mid": 1, "data.mean_long": 1}))
    if not irows:
        return None
    idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
    d = pd.DataFrame({
        "ms": [r["data"].get("mean_short") for r in irows],
        "mm": [r["data"].get("mean_mid") for r in irows],
        "ml": [r["data"].get("mean_long") for r in irows],
    }, index=idx).sort_index()
    return d.resample("ME").last()


def signature(dm):
    dm = dm.dropna()
    if len(dm) < 18:
        return None
    ml = dm["ml"]
    ml_rise = float((ml > ml.shift(3)).mean())
    s_below = dm["ms"] < dm["mm"]
    breaths, dips, in_dip, trough = 0, [], False, None
    for t in range(1, len(dm)):
        if dm["ml"].iloc[t] <= 50:
            in_dip, trough = False, None
            continue
        if s_below.iloc[t]:
            in_dip = True
            tv = dm["ms"].iloc[t] - dm["ml"].iloc[t]
            trough = tv if trough is None else min(trough, tv)
        elif in_dip:
            breaths += 1
            if trough is not None:
                dips.append(trough)
            in_dip, trough = False, None
    return dict(ml_rise=ml_rise, ml_med=float(ml.median()),
                breaths=breaths, dip_dpth=float(np.median(dips)) if dips else np.nan)


iv500 = load_membership(db, "NIFTY500", anchor=date.today())
members = members_as_of(iv500, RET_TO.date())
print(f"PIT N500 @ {RET_TO.date()}: {len(members)}  | horizon={H}m")

rows = []          # panel rows for surface + Fama-MacBeth
sig = {}           # per-name signature
ret55 = {}         # 5.5y price multiple (for winner labeling)
for sym in members:
    px = close_series(sym)
    rm = rs_monthly(sym)
    if px is None or rm is None:
        continue
    pxm = px.resample("ME").last()
    # 5.5y multiple for winner labeling
    a = px[(px.index >= RET_FROM) & (px.index <= RET_FROM + pd.Timedelta(days=45))]
    b = px[(px.index >= RET_TO - pd.Timedelta(days=45)) & (px.index <= RET_TO + pd.Timedelta(days=15))]
    if not a.empty and not b.empty:
        ret55[sym] = b.iloc[-1] / a.iloc[0]
    s = signature(rm)
    if s:
        sig[sym] = s
    # panel
    df = rm.copy()
    df["breath"] = df["ms"] - df["ml"]
    df["breath_chg"] = df["breath"].diff()
    df["ml_slope"] = df["ml"].diff(3)
    df["px"] = pxm.reindex(df.index)
    df["fwd"] = df["px"].shift(-H) / df["px"] - 1.0
    df = df.dropna(subset=["ml", "breath", "breath_chg", "ml_slope", "fwd"])
    df = df[(df.index >= RET_FROM) & (df.index <= RET_TO)]
    for ts, r in df.iterrows():
        rows.append((ts, sym, r["ml"], r["ml_slope"], r["breath"], r["breath_chg"], r["fwd"]))

P = pd.DataFrame(rows, columns=["month", "sym", "ml", "ml_slope", "breath", "breath_chg", "fwd"])
# winsorize forward return per-month at 2/98 pct
P["fwdw"] = P.groupby("month")["fwd"].transform(
    lambda s: s.clip(lower=s.quantile(0.02), upper=s.quantile(0.98)))
print(f"panel rows: {len(P)}  names: {P['sym'].nunique()}  months: {P['month'].nunique()}")

# ---- A. winners vs field contrast ----
if ret55:
    rr = pd.Series(ret55)
    cut = rr.quantile(0.90)
    win = set(rr[rr >= cut].index)
    print(f"\n=== A. signature contrast: winners (top-decile 5.5y ret, n={len(win)}) vs field ===")
    print(f"{'stat':10} {'winners':>9} {'field':>9} {'separation':>11}")
    for k in ("ml_med", "ml_rise", "breaths", "dip_dpth"):
        w = [sig[s][k] for s in sig if s in win and np.isfinite(sig[s][k])]
        f = [sig[s][k] for s in sig if s not in win and np.isfinite(sig[s][k])]
        wm, fm = np.median(w), np.median(f)
        pooled_sd = np.std(w + f) or 1.0
        print(f"{k:10} {wm:9.2f} {fm:9.2f} {(wm-fm)/pooled_sd:11.2f}")
    print("  [separation = (winner_median - field_median) / pooled SD; |.|>0.3 = the feature discriminates]")

# ---- B. forward-return surface over (trend band x breath band) ----
tb = [(-1, 50, "ml<50"), (50, 65, "50-65"), (65, 80, "65-80"), (80, 101, "ml>80")]
bb = [(-99, -15, "deep inhale<-15"), (-15, -5, "inhale -15..-5"), (-5, 5, "neutral -5..5"),
      (5, 15, "exhale 5..15"), (15, 99, "extended>15")]
print(f"\n=== B. median forward {H}m PRICE return (%) by trend x breath  [count] ===")
hdr = f"{'trend\\breath':16}" + "".join(f"{lbl.split()[0]:>16}" for _, _, lbl in bb)
print(hdr)
for tlo, thi, tlbl in tb:
    cells = []
    for blo, bhi, _ in bb:
        m = P[(P.ml >= tlo) & (P.ml < thi) & (P.breath >= blo) & (P.breath < bhi)]
        cells.append(f"{(m.fwdw.median()*100):6.1f} [{len(m):5d}]" if len(m) >= 30 else f"{'--':>6} [{len(m):5d}]")
    print(f"{tlbl:16}" + "".join(f"{c:>16}" for c in cells))
print("  [reads: in each trend band, does buying the inhale (left) beat buying the exhale (right)?]")

# ---- B2. phase test: inside an intact uptrend, inhale turning UP vs DOWN ----
up = P[(P.ml >= 55) & (P.ml <= 80) & (P.ml_slope > 0)]
inh = up[(up.breath >= -20) & (up.breath < -3)]   # in a dip
print(f"\n=== B2. inside intact uptrend (ml 55-80 & rising), in a dip (breath -20..-3) ===")
for lbl, sub in [("breath RISING (inhaling->recovering)", inh[inh.breath_chg > 0]),
                 ("breath FALLING (still dropping)", inh[inh.breath_chg <= 0])]:
    if len(sub) >= 30:
        print(f"  {lbl:38} fwd{H}m median={sub.fwdw.median()*100:5.1f}%  mean={sub.fwdw.mean()*100:5.1f}%  n={len(sub)}")

# ---- C. Fama-MacBeth: standardized cross-sectional coefficients = draft equation ----
def fama_macbeth(panel, feats):
    betas = []
    for _, g in panel.groupby("month"):
        if len(g) < 25:
            continue
        X = g[feats].values.astype(float)
        mu, sd = X.mean(0), X.std(0)
        sd[sd == 0] = 1.0
        Xz = np.column_stack([np.ones(len(g)), (X - mu) / sd])
        y = (g["fwdw"].values * 100.0)
        beta, *_ = np.linalg.lstsq(Xz, y, rcond=None)
        betas.append(beta)
    B = np.array(betas)
    mean, se = B.mean(0), B.std(0) / np.sqrt(len(B))
    return mean, mean / se, len(B)

feats = ["ml", "ml_slope", "breath", "breath_chg"]
print(f"\n=== C. Fama-MacBeth: E[fwd{H}m %] on standardized features (coef = % per +1 SD) ===")
print(f"{'subset':22} {'n_mo':>5} {'const':>8} " + " ".join(f"{f:>10}" for f in feats))
for lbl, sub in [("ALL", P),
                 ("UPTREND ml55-80 rising", P[(P.ml >= 55) & (P.ml <= 80) & (P.ml_slope > 0)]),
                 ("DOWNTREND ml<50 fall", P[(P.ml < 50) & (P.ml_slope < 0)])]:
    if sub["month"].nunique() < 6:
        continue
    mean, t, nmo = fama_macbeth(sub, feats)
    print(f"{lbl:22} {nmo:5d} {mean[0]:8.2f} " + " ".join(f"{mean[i+1]:6.2f}" for i in range(len(feats))))
    print(f"{'  t-stat':22} {'':5} {'':8} " + " ".join(f"{t[i+1]:6.2f}" for i in range(len(feats))))
print("  [|t|>2 = real; b_breath<0 = buy the inhale; b_breath_chg>0 = buy the recovery; "
      "b_ml>0 = strength pays]")
