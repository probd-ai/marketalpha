"""Final ladder run — percentage-of-equity tranches instead of a fixed Rs10,000.

The Rs10k run left 55.9% of equity in cash: the tranche stayed flat while equity nearly
doubled, so the deployed fraction decayed over the whole test. Sizing the tranche as
equity/DIVISOR fixes that by construction — deployment stays constant as the book grows.

Sweeps DIVISOR. Smaller divisor = bigger tranche = more deployed but more entries skipped
when several rungs fire at once (peak demand in the Rs10k run was 235 concurrent tranches).

Also reports max single-name exposure, since 6 rungs at equity/60 puts 10% of the book in
one stock — the concentration this sizing quietly introduces.

Everything else unchanged: NIFTY50 PIT ever-members, previous-bar 222 range, dynamic exits
one rung up, below-low lockout, mean_long>50 gate, 12bps per side, Rs15L start.
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
CAPITAL, COST, LOOKBACK, RF = 1_500_000.0, 0.0012, 222, 0.06
DIVISORS = [60, 80, 100, 120, 150]
FRAC = {"77": .77, "66": .66, "50": .50, "33": .33, "23": .23, "low": 0.0}
RUNGS = ["77", "66", "50", "33", "23", "low"]
DEPTH = {"low": 0, "23": 1, "33": 2, "50": 3, "66": 4, "77": 5}
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
DATES = [d for d in C.index if d >= START]

# benchmark
bn = []
for c in COLLS:
    bn += list(db[c].find({"symbol": "Nifty 50", "date": {"$gte": START, "$lte": END}},
                          {"_id": 0, "date": 1, "close_price": 1}))
bn = pd.DataFrame(bn).dropna().drop_duplicates("date").sort_values("date")
YRS = (DATES[-1] - DATES[0]).days / 365.25
BTOT = (bn.close_price.iloc[-1] / bn.close_price.iloc[0] - 1) * 100
BCAGR = ((1 + BTOT / 100) ** (1 / YRS) - 1) * 100


def run(div):
    cash, book = CAPITAL, {}
    locked = {s: False for s in SYMS}
    eq, csh, nop, skipped, maxname = [], [], [], 0, 0.0
    for dt in DATES:
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
        for s, d_ in lv.items():
            if locked[s] and d_["c"] > d_["23"]:
                locked[s] = False
        for (s, r) in list(book):
            d_ = lv.get(s)
            if d_ is None or locked[s]:
                continue
            if d_["c"] > d_[UP[r]]:
                t = book.pop((s, r))
                cash += t["sh"] * d_["c"] * (1 - COST)
        mv = sum(t["sh"] * C.at[dt, s] for (s, r), t in book.items()
                 if np.isfinite(C.at[dt, s]))
        tranche = (cash + mv) / div                      # % of CURRENT equity
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
            if cash < tranche * (1 + COST):
                skipped += 1
                continue
            px = lv[s]["c"]
            book[(s, r)] = {"px": px, "sh": tranche / px, "dt": dt}
            cash -= tranche * (1 + COST)
            if r == "low":
                locked[s] = True
        mv = sum(t["sh"] * C.at[dt, s] for (s, r), t in book.items()
                 if np.isfinite(C.at[dt, s]))
        e = cash + mv
        byname = {}
        for (s, r), t in book.items():
            if np.isfinite(C.at[dt, s]):
                byname[s] = byname.get(s, 0) + t["sh"] * C.at[dt, s]
        if byname:
            maxname = max(maxname, max(byname.values()) / e * 100)
        eq.append(e); csh.append(cash); nop.append(len(book))
    eq = np.array(eq); csh = np.array(csh)
    peak = np.maximum.accumulate(eq)
    r_ = np.diff(eq) / eq[:-1]
    return {"div": div, "tot": (eq[-1] / CAPITAL - 1) * 100,
            "cagr": ((eq[-1] / CAPITAL) ** (1 / YRS) - 1) * 100,
            "dd": ((eq - peak) / peak).min() * 100,
            "sh": (r_.mean() * 252 - RF) / (r_.std(ddof=1) * np.sqrt(252)),
            "cash": 100 * csh.mean() / eq.mean(), "skip": skipped,
            "ntr": np.mean(nop), "maxname": maxname, "final": eq[-1]}


print(f"benchmark Nifty 50  {BTOT:+.2f}%   CAGR {BCAGR:+.2f}%   ({YRS:.2f}y)\n")
print(f"{'tranche':<14}{'final Rs':>12}{'totRet%':>10}{'CAGR%':>8}{'maxDD%':>9}"
      f"{'Sharpe':>8}{'cash%':>8}{'skips':>7}{'maxname%':>10}")
print("-" * 86)
print(f"{'fixed Rs10k':<14}{'2,637,445':>12}{75.83:>10.2f}{8.85:>8.2f}{-33.31:>9.2f}"
      f"{0.268:>8.3f}{55.9:>8.1f}{457:>7}{'—':>10}")
for d in DIVISORS:
    r = run(d)
    print(f"{'equity/'+str(d):<14}{r['final']:>12,.0f}{r['tot']:>10.2f}{r['cagr']:>8.2f}"
          f"{r['dd']:>9.2f}{r['sh']:>8.3f}{r['cash']:>8.1f}{r['skip']:>7}{r['maxname']:>10.1f}")
