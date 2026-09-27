"""Ladder with capital recycling — Rs5L, NIFTY50, cut the worst loser to fund a new entry.

This addresses the flaw that sank the earlier versions: the exit rule only fires on a close
ABOVE the next rung, so a losing tranche never closes and its capital is dead forever. The
Rs15L run ended with 165 open tranches marked at -Rs1.8L.

New rule:
  - deploy until cash is exhausted
  - once fully deployed, a fresh trigger forces a FULL exit of the worst-performing name
    (all its tranches at once, not one rung), and that capital funds the new entry
  - the exited name is BLACKLISTED for 90 days -- no re-entry
  - only a name at an unrealised LOSS may be cut, and never the name that just triggered

Tranche is a percentage of equity (equity/DIV), the fix that mattered most last run.
Everything else unchanged: PIT ever-members, previous-bar 222 range, dynamic exits one
rung up, below-low lockout, mean_long>50 gate for fresh buys, 12bps per side.
"""
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
from settings import MONGO_URI, DB_NAME
from membership import load_membership, union_over

START, END, WARM = datetime(2020, 1, 1), datetime(2026, 8, 28), datetime(2017, 1, 1)
CAPITAL, COST, LOOKBACK, RF = 500_000.0, 0.0012, 222, 0.06
BLACKLIST_DAYS = 90
DIVISORS = [25, 33, 40, 50]
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
YRS = (DATES[-1] - DATES[0]).days / 365.25

bn = []
for c in COLLS:
    bn += list(db[c].find({"symbol": "Nifty 50", "date": {"$gte": START, "$lte": END}},
                          {"_id": 0, "date": 1, "close_price": 1}))
bn = pd.DataFrame(bn).dropna().drop_duplicates("date").sort_values("date")
BTOT = (bn.close_price.iloc[-1] / bn.close_price.iloc[0] - 1) * 100
BCAGR = ((1 + BTOT / 100) ** (1 / YRS) - 1) * 100


def run(div):
    cash, book = CAPITAL, {}
    locked = {s: False for s in SYMS}
    black = {}
    eq, csh, nop, nname = [], [], [], []
    cuts, cut_loss, normal_exits, skipped = 0, 0.0, 0, 0

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
        for s in list(black):
            if dt >= black[s]:
                del black[s]
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
                normal_exits += 1

        def equity():
            return cash + sum(t["sh"] * C.at[dt, s] for (s, r), t in book.items()
                              if np.isfinite(C.at[dt, s]))

        tranche = equity() / div
        trig = []
        for s, d_ in lv.items():
            if locked[s] or s in black:
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
            if (s, r) in book or locked[s] or s in black:
                continue
            while cash < tranche * (1 + COST):
                # fully deployed: cut the worst LOSER to fund this entry
                pnl = {}
                for (bs, br), t in book.items():
                    if bs == s or bs not in lv:
                        continue
                    pnl.setdefault(bs, [0.0, 0.0])
                    pnl[bs][0] += t["sh"] * lv[bs]["c"]
                    pnl[bs][1] += t["sh"] * t["px"]
                losers = {k: v[0] / v[1] - 1 for k, v in pnl.items() if v[0] < v[1]}
                if not losers:
                    break
                worst = min(losers, key=losers.get)
                for (bs, br) in [k for k in book if k[0] == worst]:
                    t = book.pop((bs, br))
                    proceeds = t["sh"] * lv[worst]["c"] * (1 - COST)
                    cash += proceeds
                    cut_loss += proceeds - t["sh"] * t["px"] * (1 + COST)
                black[worst] = dt + timedelta(days=BLACKLIST_DAYS)
                locked[worst] = False
                cuts += 1
            if cash < tranche * (1 + COST):
                skipped += 1
                continue
            px = lv[s]["c"]
            book[(s, r)] = {"px": px, "sh": tranche / px, "dt": dt}
            cash -= tranche * (1 + COST)
            if r == "low":
                locked[s] = True

        e = equity()
        eq.append(e); csh.append(cash); nop.append(len(book))
        nname.append(len({k[0] for k in book}))

    eq = np.array(eq); csh = np.array(csh)
    peak = np.maximum.accumulate(eq)
    rr = np.diff(eq) / eq[:-1]
    return {"div": div, "final": eq[-1], "tot": (eq[-1] / CAPITAL - 1) * 100,
            "cagr": ((eq[-1] / CAPITAL) ** (1 / YRS) - 1) * 100,
            "dd": ((eq - peak) / peak).min() * 100,
            "sh": (rr.mean() * 252 - RF) / (rr.std(ddof=1) * np.sqrt(252)),
            "cash": 100 * csh.mean() / eq.mean(), "cuts": cuts, "cutloss": cut_loss,
            "nx": normal_exits, "skip": skipped, "ntr": np.mean(nop),
            "nname": np.mean(nname), "maxname": max(nname), "open": len(book)}


print(f"Rs{CAPITAL:,.0f} · NIFTY50 · recycle-the-loser · blacklist {BLACKLIST_DAYS}d · "
      f"{DATES[0].date()}..{DATES[-1].date()} ({YRS:.2f}y)")
print(f"benchmark Nifty 50  {BTOT:+.2f}%   CAGR {BCAGR:+.2f}%\n")
print(f"{'tranche':<12}{'final Rs':>11}{'totRet%':>10}{'CAGR%':>8}{'maxDD%':>9}{'Sharpe':>8}"
      f"{'cash%':>7}{'cuts':>6}{'cut P&L':>11}{'names':>7}")
print("-" * 91)
for d in DIVISORS:
    r = run(d)
    print(f"{'equity/'+str(d):<12}{r['final']:>11,.0f}{r['tot']:>10.2f}{r['cagr']:>8.2f}"
          f"{r['dd']:>9.2f}{r['sh']:>8.3f}{r['cash']:>7.1f}{r['cuts']:>6}"
          f"{r['cutloss']:>11,.0f}{r['nname']:>7.1f}")
