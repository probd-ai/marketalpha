"""Consistency check: is the aligned-stack champion's edge steady across regimes, or carried
by a couple of years?

Reproduces the champion (aligned-stack: long>50 & mid>60 & short>80 & score>90, hold-all
eligible PIT-N500 equal-weight, monthly, net) and the EW-all-N500 control, then breaks the
edge down BY CALENDAR YEAR and FIRST-HALF vs SECOND-HALF:
  - per-period return (champion vs control vs Nifty) and spread
  - % of months champion beats control (consistency, not just magnitude)
  - per-period Sharpe + the champion's max drawdown per half
The question: does the champion beat the control in EVERY year / both halves (real, durable),
or is 23.8% concentrated in 2021/2023 (regime-carried, fragile)? In-sample diagnostic.
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2019, 1, 1)
THRESH = {"mean_long": 50, "mean_mid": 60, "mean_short": 80, "truevx_score": 90}
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
COST = BUY_BPS + SELL_BPS
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
iv500 = load_membership(db, "NIFTY500", anchor=date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})

elig_at, close_at = {}, {}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_long": 1, "data.mean_mid": 1,
         "data.mean_short": 1, "data.truevx_score": 1}))
    if not irows:
        continue
    idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
    cond = None
    for f, thr in THRESH.items():
        c = pd.Series({d: r["data"].get(f) for d, r in zip(idx, irows)}).sort_index() > thr
        cond = c if cond is None else (cond & c)
    elig_at[sym] = cond.reindex(snap_ts, method="ffill")
    close_at[sym] = px.reindex(snap_ts)

nifty = close_series("Nifty 50").reindex(snap_ts)

recs = []          # (month_date, champ_net, control, nifty_ret, breadth)
prev = set()
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    cand, sel = [], []
    for sym in members:
        c = close_at.get(sym)
        if c is None:
            continue
        c0, c1 = c.get(d0), c.get(d1)
        if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
            continue
        ret = c1 / c0 - 1
        cand.append(ret)
        e = elig_at.get(sym)
        if e is not None and e.get(d0) == True:   # noqa: E712
            sel.append((sym, ret))
    n = len(sel)
    gross = float(np.mean([r for _, r in sel])) if sel else 0.0
    cur = {s for s, _ in sel}
    turn = (len(cur - prev) / n) if n else 0.0
    champ_net = gross - turn * COST
    prev = cur
    control = float(np.mean(cand)) if cand else 0.0
    nret = (nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan
    recs.append((snaps[t], champ_net, control, nret, n))

R = pd.DataFrame(recs, columns=["month", "champ", "control", "nifty", "breadth"])
R["year"] = pd.to_datetime(R["month"]).dt.year


def block(df):
    def cum(col):
        return (np.prod(1 + df[col].dropna().values) - 1) * 100
    def shp(col):
        s = df[col].dropna().values
        return (s.mean() / s.std() * math.sqrt(12)) if len(s) > 1 and s.std() > 0 else float("nan")
    win = float((df["champ"] > df["control"]).mean()) * 100
    return cum("champ"), cum("control"), cum("nifty"), shp("champ"), shp("control"), win, df["breadth"].mean(), len(df)


print(f"=== aligned-stack champion vs EW-all control · per-year consistency (net) ===")
print(f"{'period':10} {'mo':>3} {'champ%':>7} {'ctrl%':>7} {'nifty%':>7} {'spread':>7} "
      f"{'chSh':>5} {'ctSh':>5} {'win%':>5} {'avgN':>5}")
for y in sorted(R["year"].unique()):
    ch, ct, nf, csh, ctsh, win, bn, m = block(R[R.year == y])
    print(f"{y:<10} {m:>3} {ch:>7.1f} {ct:>7.1f} {nf:>7.1f} {ch-ct:>+7.1f} "
          f"{csh:>5.2f} {ctsh:>5.2f} {win:>5.0f} {bn:>5.0f}")

print(f"\n=== first-half vs second-half ===")
half = len(R) // 2
for lbl, df in [("H1 " + f"{R.month.iloc[0]}..{R.month.iloc[half-1]}", R.iloc[:half]),
                ("H2 " + f"{R.month.iloc[half]}..{R.month.iloc[-1]}", R.iloc[half:])]:
    ch, ct, nf, csh, ctsh, win, bn, m = block(df)
    # champion maxDD within the half
    eq = np.cumprod(1 + df["champ"].values); peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    print(f"{lbl}")
    print(f"   champ {ch:+.1f}%  ctrl {ct:+.1f}%  spread {ch-ct:+.1f}  |  champ Sharpe {csh:.2f} "
          f"vs ctrl {ctsh:.2f}  |  champ maxDD {mdd*100:.1f}%  |  win {win:.0f}%  avgN {bn:.0f}  ({m}mo)")

beat = sum(block(R[R.year == y])[0] > block(R[R.year == y])[1] for y in R["year"].unique())
print(f"\nyears champion beat control: {beat}/{R['year'].nunique()}  |  "
      f"overall months champion>control: {(R['champ']>R['control']).mean()*100:.0f}%")
print("[in-sample regime diagnostic; not yet walk-forward OOS]")
