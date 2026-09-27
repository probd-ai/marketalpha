"""Dynamic-Fib ladder across the whole NIFTY50 (PIT ever-members), marked to market.

Fixes the two things the single-stock run exposed:
  1. OPEN tranches are marked to market at the final close and counted in every statistic.
     Closed-trades-only made Reliance look 97.6% -- the losers simply never exit, because
     the exit rule only fires on a close above the next rung up.
  2. Runs the whole universe so aggregate capital demand is measurable: how many tranches
     are live at once across all names is what decides whether Rs5L is enough.

Rules identical to the single-stock version (previous bar's 222-range, dynamic exits,
below-low lockout, mean_long>50 gate for fresh buys, Rs10k tranche, 12bps per side).

Universe is the PIT union of NIFTY50 members over the window, so names that dropped out
of the index are included -- no survivorship in the universe. Index membership is not
gated bar-by-bar inside a ladder; a position opened as a member is carried to its exit.
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
LOOKBACK, TRANCHE, COST = 222, 10_000.0, 0.0012
FRAC = {"77": .77, "66": .66, "50": .50, "33": .33, "23": .23, "low": 0.0}
RUNGS = ["77", "66", "50", "33", "23", "low"]
UP = {"77": "HH", "66": "77", "50": "66", "33": "50", "23": "33", "low": "23"}

db = MongoClient(MONGO_URI)[DB_NAME]
COLLS = sorted(c for c in db.list_collection_names() if c.startswith("prices_"))
iv = load_membership(db, "NIFTY50", date.today())
SYMS = sorted(union_over(iv, START.date(), END.date()))
print(f"NIFTY50 PIT ever-members {START.date()}..{END.date()}: {len(SYMS)}", flush=True)

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
trades, per_stock = [], {}
live = pd.Series(0, index=dates, dtype=int)      # tranches open across the whole book

for s in SYMS:
    if s not in C.columns:
        continue
    open_t, locked, nclosed = {}, False, 0
    ml = MLdf[s] if s in MLdf.columns else pd.Series(np.nan, index=C.index)
    for dt in dates:
        hh, ll, c = HH.at[dt, s], LL.at[dt, s], C.at[dt, s]
        if not np.isfinite(hh) or not np.isfinite(ll) or not np.isfinite(c) or hh <= ll:
            live[dt] += len(open_t); continue
        rng = hh - ll
        lvl = {k: ll + rng * f for k, f in FRAC.items()}; lvl["HH"] = hh
        gate = ml.get(dt, np.nan)
        ok = bool(np.isfinite(gate) and gate > 50)

        if locked:
            if c > lvl["23"]:
                locked = False
            else:
                live[dt] += len(open_t); continue

        for r in list(open_t):
            if c > lvl[UP[r]]:
                t = open_t.pop(r)
                pnl = t["sh"] * c * (1 - COST) - t["sh"] * t["px"] * (1 + COST)
                trades.append({"sym": s, "rung": r, "pnl": pnl, "open": False,
                               "pct": pnl / (t["sh"] * t["px"]) * 100,
                               "days": (dt - t["dt"]).days})
                nclosed += 1
        if ok:
            for r in RUNGS:
                if r in open_t:
                    continue
                trig = (c < ll) if r == "low" else (c < lvl[r])
                if trig:
                    open_t[r] = {"px": c, "sh": TRANCHE / c, "dt": dt}
                    if r == "low":
                        locked = True
                        break
        live[dt] += len(open_t)

    last = C[s].dropna()
    if len(last):
        pe = last.iloc[-1]; pd_ = last.index[-1]
        for r, t in open_t.items():                       # mark to market
            pnl = t["sh"] * pe * (1 - COST) - t["sh"] * t["px"] * (1 + COST)
            trades.append({"sym": s, "rung": r, "pnl": pnl, "open": True,
                           "pct": pnl / (t["sh"] * t["px"]) * 100,
                           "days": (pd_ - t["dt"]).days})
    per_stock[s] = (nclosed, len(open_t))

t = pd.DataFrame(trades)
cl, op = t[~t.open], t[t.open]
print(f"\n{'='*78}\nALL NIFTY50 · ladder · Rs{TRANCHE:,.0f} tranche · {START.date()}..{END.date()}\n{'='*78}")
print(f"  positions taken        : {len(t):,}   closed {len(cl):,}   still open {len(op):,}")
print(f"  CLOSED only            : win {100*(cl.pnl>0).mean():.1f}%   "
      f"mean {cl.pct.mean():+.2f}%   P&L Rs{cl.pnl.sum():,.0f}")
print(f"  OPEN marked to market  : win {100*(op.pnl>0).mean():.1f}%   "
      f"mean {op.pct.mean():+.2f}%   P&L Rs{op.pnl.sum():,.0f}")
print(f"  ALL positions (honest) : win {100*(t.pnl>0).mean():.1f}%   "
      f"mean {t.pct.mean():+.2f}%   P&L Rs{t.pnl.sum():,.0f}")
print(f"  worst position {t.pct.min():+.1f}%   best {t.pct.max():+.1f}%   "
      f"mean hold {t.days.mean():.0f}d   max {t.days.max()}d")

print(f"\n  CAPITAL DEMAND — tranches live across the whole universe")
q = live.quantile([.5, .75, .9, .95, 1.0])
for k, v in q.items():
    print(f"    p{int(k*100):<4} {v:>6.0f} tranches = Rs{v*TRANCHE:,.0f}")
print(f"    mean  {live.mean():>6.1f} tranches = Rs{live.mean()*TRANCHE:,.0f}")
print(f"    Rs5L caps you at 50 tranches — exceeded on "
      f"{100*(live>50).mean():.0f}% of days")

print(f"\n  {'rung':<6}{'n':>7}{'win%':>7}{'mean%':>8}{'median%':>9}{'total Rs':>12}{'avg days':>10}")
for r in RUNGS:
    g = t[t.rung == r]
    if g.empty:
        continue
    print(f"  {r:<6}{len(g):>7}{100*(g.pnl>0).mean():>7.0f}{g.pct.mean():>8.2f}"
          f"{g.pct.median():>9.2f}{g.pnl.sum():>12,.0f}{g.days.mean():>10.0f}")

by = t.groupby("sym").agg(n=("pnl", "size"), pnl=("pnl", "sum"), pct=("pct", "mean")).sort_values("pnl")
print(f"\n  worst 6 names : " + ", ".join(f"{i} Rs{r.pnl:,.0f}" for i, r in by.head(6).iterrows()))
print(f"  best 6 names  : " + ", ".join(f"{i} Rs{r.pnl:,.0f}" for i, r in by.tail(6).iloc[::-1].iterrows()))
print(f"  names with negative total: {(by.pnl<0).sum()} of {len(by)}")
