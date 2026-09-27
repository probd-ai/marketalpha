"""Dynamic-Fib ladder as a real Rs15L portfolio — cash-constrained, daily marked.

The previous run measured P&L with unlimited capital (peak demand Rs23.5L), which is not
a return. Here capital is finite:

  - start Rs15,00,000 cash, tranche Rs10,000  -> 150 tranches maximum
  - an entry only fires if cash >= tranche; otherwise it is SKIPPED and recorded
  - exits return proceeds to cash
  - equity = cash + mark-to-market of every open tranche, every day

When more triggers fire on one day than cash can fund, the deepest rung is taken first
(the ladder's own premise: lower rung = more oversold). Skipped-entry count is reported so
we can see whether that choice matters.

Everything else identical: NIFTY50 PIT ever-members, previous bar's 222-range, dynamic
exits one rung up, below-low lockout, mean_long>50 gate for fresh buys, 12bps per side.
"""
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
from settings import MONGO_URI, DB_NAME
from membership import load_membership, union_over

START, END, WARM = datetime(2020, 1, 1), datetime(2026, 8, 28), datetime(2017, 1, 1)
CAPITAL, TRANCHE, COST, LOOKBACK = 1_500_000.0, 10_000.0, 0.0012, 222
RF = 0.06
FRAC = {"77": .77, "66": .66, "50": .50, "33": .33, "23": .23, "low": 0.0}
RUNGS = ["77", "66", "50", "33", "23", "low"]
DEPTH = {"low": 0, "23": 1, "33": 2, "50": 3, "66": 4, "77": 5}   # deepest first
UP = {"77": "HH", "66": "77", "50": "66", "33": "50", "23": "33", "low": "23"}

db = MongoClient(MONGO_URI)[DB_NAME]
COLLS = sorted(c for c in db.list_collection_names() if c.startswith("prices_"))
iv = load_membership(db, "NIFTY50", date.today())
SYMS = sorted(union_over(iv, START.date(), END.date()))

rows = []
for c in COLLS:
    rows += list(db[c].find({"symbol": {"$in": SYMS}, "date": {"$gte": WARM, "$lte": END}},
                            {"_id": 0, "symbol": 1, "date": 1, "close_price": 1,
                             "high_price": 1, "low_price": 1}))
df = pd.DataFrame(rows).dropna(subset=["close_price"])
df["date"] = pd.to_datetime(df["date"]).dt.normalize()
df = df.drop_duplicates(["symbol", "date"])
C = df.pivot(index="date", columns="symbol", values="close_price").sort_index().astype(float)
H = df.pivot(index="date", columns="symbol", values="high_price").sort_index().astype(float).fillna(C)
L = df.pivot(index="date", columns="symbol", values="low_price").sort_index().astype(float).fillna(C)
HH = H.rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
LL = L.rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)

ML = {}
for d in db.indicators.find({"indicator_type": "truevx", "symbol": {"$in": SYMS},
                             "date": {"$gte": WARM, "$lte": END}},
                            {"_id": 0, "symbol": 1, "date": 1, "data.mean_long": 1}):
    v = (d.get("data") or {}).get("mean_long")
    if v is not None:
        ML.setdefault(d["symbol"], {})[pd.Timestamp(d["date"]).normalize()] = v
MLdf = pd.DataFrame(ML).reindex(C.index).ffill()

dates = [d for d in C.index if d >= START]
cash = CAPITAL
book = {}                     # (sym, rung) -> {px, sh, dt}
locked = {s: False for s in SYMS}
equity, cashser, nopen = [], [], []
skipped = 0
closed = []

for dt in dates:
    lv = {}
    for s in SYMS:
        if s not in C.columns:
            continue
        hh, ll, c = HH.at[dt, s], LL.at[dt, s], C.at[dt, s]
        if not (np.isfinite(hh) and np.isfinite(ll) and np.isfinite(c)) or hh <= ll:
            continue
        rng = hh - ll
        d_ = {k: ll + rng * f for k, f in FRAC.items()}
        d_["HH"], d_["LL"], d_["c"] = hh, ll, c
        lv[s] = d_

    # ---- lockout release + exits ----
    for s, d_ in lv.items():
        if locked[s] and d_["c"] > d_["23"]:
            locked[s] = False
    for (s, r) in list(book):
        d_ = lv.get(s)
        if d_ is None or locked[s]:
            continue
        if d_["c"] > d_[UP[r]]:
            t = book.pop((s, r))
            proceeds = t["sh"] * d_["c"] * (1 - COST)
            cash += proceeds
            closed.append({"sym": s, "rung": r,
                           "pnl": proceeds - t["sh"] * t["px"] * (1 + COST),
                           "days": (dt - t["dt"]).days})

    # ---- entries, deepest rung first, cash permitting ----
    trig = []
    for s, d_ in lv.items():
        if locked[s]:
            continue
        g = MLdf[s].get(dt, np.nan) if s in MLdf.columns else np.nan
        if not (np.isfinite(g) and g > 50):
            continue
        for r in RUNGS:
            if (s, r) in book:
                continue
            if (d_["c"] < d_["LL"]) if r == "low" else (d_["c"] < d_[r]):
                trig.append((DEPTH[r], s, r))
    for _, s, r in sorted(trig):
        if (s, r) in book or locked[s]:
            continue
        if cash < TRANCHE * (1 + COST):
            skipped += 1
            continue
        px = lv[s]["c"]
        book[(s, r)] = {"px": px, "sh": TRANCHE / px, "dt": dt}
        cash -= TRANCHE * (1 + COST)
        if r == "low":
            locked[s] = True

    mv = sum(t["sh"] * C.at[dt, s] for (s, r), t in book.items() if np.isfinite(C.at[dt, s]))
    equity.append(cash + mv); cashser.append(cash); nopen.append(len(book))

eq = np.array(equity); cs = np.array(cashser)
yrs = (dates[-1] - dates[0]).days / 365.25
tot = (eq[-1] / CAPITAL - 1) * 100
cagr = ((eq[-1] / CAPITAL) ** (1 / yrs) - 1) * 100
peak = np.maximum.accumulate(eq); dd = ((eq - peak) / peak).min() * 100
r = np.diff(eq) / eq[:-1]
sh = (r.mean() * 252 - RF) / (r.std(ddof=1) * np.sqrt(252))

# benchmark
bn = []
for c in COLLS:
    bn += list(db[c].find({"symbol": "Nifty 50", "date": {"$gte": START, "$lte": END}},
                          {"_id": 0, "date": 1, "close_price": 1}))
bn = pd.DataFrame(bn).dropna().drop_duplicates("date").sort_values("date")
btot = (bn.close_price.iloc[-1] / bn.close_price.iloc[0] - 1) * 100
bcagr = ((1 + btot / 100) ** (1 / yrs) - 1) * 100

cl = pd.DataFrame(closed)
openpnl = sum(t["sh"] * C[s].dropna().iloc[-1] * (1 - COST) - t["sh"] * t["px"] * (1 + COST)
              for (s, r), t in book.items())
print(f"{'='*74}\nLADDER PORTFOLIO   Rs{CAPITAL:,.0f}   {dates[0].date()} .. {dates[-1].date()}  "
      f"({yrs:.2f}y)\n{'='*74}")
print(f"  final equity      Rs{eq[-1]:,.0f}")
print(f"  TOTAL RETURN      {tot:+.2f}%        CAGR {cagr:+.2f}%")
print(f"  benchmark N50     {btot:+.2f}%        CAGR {bcagr:+.2f}%      alpha {tot-btot:+.2f}pp")
print(f"  max drawdown      {dd:.2f}%          Sharpe {sh:.3f}")
print(f"\n  realised P&L      Rs{cl.pnl.sum():,.0f} over {len(cl):,} closed round-trips")
print(f"  open book         {len(book)} tranches, marked at Rs{openpnl:+,.0f}")
print(f"  closed win rate   {100*(cl.pnl>0).mean():.1f}%   mean hold {cl.days.mean():.0f}d")
print(f"\n  CASH / DEPLOYMENT")
print(f"    mean cash idle  Rs{cs.mean():,.0f}  ({100*cs.mean()/eq.mean():.1f}% of equity)")
print(f"    min cash        Rs{cs.min():,.0f}      max tranches open {max(nopen)}")
print(f"    mean tranches   {np.mean(nopen):.0f} of 150      fully invested on "
      f"{100*np.mean(np.array(nopen)>=145):.0f}% of days")
print(f"    entries skipped for want of cash: {skipped:,}")
