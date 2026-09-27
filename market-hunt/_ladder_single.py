"""Single-stock Dynamic-Fib ladder — the rules as specified, on one name at a time.

Levels, from a rolling 222-bar range (matching the Pine script with factor=1.0):
    TrendHH = highest(high,222)   TrendLL = lowest(low,222)
    per_X   = TrendLL + (TrendHH - TrendLL) * X      X in {.23,.33,.50,.66,.77}

ENTRY   close below a rung, one tranche per rung, rungs 77/66/50/33/23/low (max 6 open).
        A rung can be re-entered only after its own tranche has exited.
EXIT    each tranche exits on close ABOVE the next rung up, evaluated at TODAY's level
        (dynamic, not frozen at entry) -- so a falling range can exit you at a loss.
            77 -> above TrendHH   66 -> above 77   50 -> above 66
            33 -> above 50        23 -> above 33   low -> above 23
LOCKOUT once close is below TrendLL that is the last entry; no exits and no new entries
        until close is back above per_23.
GATE    mean_long <= 50 -> no fresh buys, exits still run.

Levels use the PREVIOUS bar's range so a new low cannot redefine the level that triggered it.
Tranche = fixed rupee. Statutory-ish cost of 12bps per side.
"""
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
from settings import MONGO_URI, DB_NAME

SYMBOLS = ["RELIANCE", "TCS"]
START, END = datetime(2020, 1, 1), datetime(2026, 8, 28)
WARM = datetime(2017, 1, 1)
LOOKBACK = 222
TRANCHE = 10_000.0
COST = 0.0012                      # per side
FRAC = {"77": .77, "66": .66, "50": .50, "33": .33, "23": .23, "low": 0.0}
RUNGS = ["77", "66", "50", "33", "23", "low"]
UP = {"77": "HH", "66": "77", "50": "66", "33": "50", "23": "33", "low": "23"}

db = MongoClient(MONGO_URI)[DB_NAME]
COLLS = sorted(c for c in db.list_collection_names() if c.startswith("prices_"))


def load(sym):
    rows = []
    for c in COLLS:
        rows += list(db[c].find({"symbol": sym, "date": {"$gte": WARM, "$lte": END}},
                                {"_id": 0, "date": 1, "close_price": 1,
                                 "high_price": 1, "low_price": 1}))
    df = pd.DataFrame(rows).dropna(subset=["close_price"])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.drop_duplicates("date").set_index("date").sort_index().astype(float)
    df["high_price"] = df["high_price"].fillna(df["close_price"])
    df["low_price"] = df["low_price"].fillna(df["close_price"])
    ml = {}
    for d in db.indicators.find({"indicator_type": "truevx", "symbol": sym,
                                 "date": {"$gte": WARM, "$lte": END}},
                                {"_id": 0, "date": 1, "data.mean_long": 1}):
        v = (d.get("data") or {}).get("mean_long")
        if v is not None:
            ml[pd.Timestamp(d["date"]).normalize()] = v
    df["mean_long"] = pd.Series(ml).reindex(df.index).ffill()
    return df


def run(sym):
    df = load(sym)
    df["HH"] = df["high_price"].rolling(LOOKBACK, min_periods=LOOKBACK).max()
    df["LL"] = df["low_price"].rolling(LOOKBACK, min_periods=LOOKBACK).min()
    d = df[df.index >= START]
    prev = df.shift(1).reindex(d.index)      # previous bar's range -> no self-reference

    open_t = {}          # rung -> dict(entry_px, shares, date)
    trades, locked = [], False
    held_hist, gated_days = [], 0

    for i, (dt, row) in enumerate(d.iterrows()):
        hh, ll = prev.at[dt, "HH"], prev.at[dt, "LL"]
        if not np.isfinite(hh) or not np.isfinite(ll) or hh <= ll:
            held_hist.append(len(open_t)); continue
        rng = hh - ll
        lvl = {k: ll + rng * f for k, f in FRAC.items()}
        lvl["HH"] = hh
        c = row["close_price"]
        ok = row["mean_long"] > 50 if np.isfinite(row["mean_long"]) else False
        if not ok:
            gated_days += 1

        # ---- lockout: below the 222-bar low, nothing moves until back above 23 ----
        if locked:
            if c > lvl["23"]:
                locked = False
            else:
                held_hist.append(len(open_t)); continue

        # ---- exits: close above the next rung up, at today's level ----
        for r in list(open_t):
            if c > lvl[UP[r]]:
                t = open_t.pop(r)
                gross = t["shares"] * c
                pnl = gross * (1 - COST) - t["shares"] * t["entry_px"] * (1 + COST)
                trades.append({"rung": r, "in": t["date"], "out": dt,
                               "px_in": t["entry_px"], "px_out": c, "pnl": pnl,
                               "pct": pnl / (t["shares"] * t["entry_px"]) * 100,
                               "days": (dt - t["date"]).days})

        # ---- entries: one tranche per rung, only while the gate is on ----
        if ok:
            for r in RUNGS:
                if r in open_t:
                    continue
                if c < lvl[r] if r != "low" else c < ll:
                    sh = TRANCHE / c
                    open_t[r] = {"entry_px": c, "shares": sh, "date": dt}
                    if r == "low":
                        locked = True
                        break
        held_hist.append(len(open_t))

    # ---- report ----
    t = pd.DataFrame(trades)
    print(f"\n{'='*76}\n{sym}   {d.index[0].date()} .. {d.index[-1].date()}   "
          f"tranche Rs{TRANCHE:,.0f}\n{'='*76}")
    if t.empty:
        print("  no completed trades"); return
    win = (t.pnl > 0).mean() * 100
    print(f"  completed round-trips : {len(t)}     win rate {win:.1f}%")
    print(f"  total P&L             : Rs{t.pnl.sum():,.0f}   "
          f"mean {t.pct.mean():+.2f}%   median {t.pct.median():+.2f}%")
    print(f"  best {t.pct.max():+.1f}%   worst {t.pct.min():+.1f}%   "
          f"mean hold {t.days.mean():.0f} days   max hold {t.days.max()} days")
    print(f"  capital use           : mean {np.mean(held_hist):.2f} of 6 tranches "
          f"(Rs{np.mean(held_hist)*TRANCHE:,.0f})   max {max(held_hist)}")
    print(f"  days with 0 open      : {100*np.mean(np.array(held_hist)==0):.0f}%   "
          f"days gated (mean_long<=50): {100*gated_days/len(d):.0f}%")
    print(f"  still open at end     : {len(open_t)} tranches")
    print(f"\n  {'rung':<6}{'trades':>7}{'win%':>7}{'mean%':>8}{'median%':>9}{'total Rs':>11}{'avg days':>10}")
    for r in RUNGS:
        g = t[t.rung == r]
        if g.empty:
            print(f"  {r:<6}{0:>7}"); continue
        print(f"  {r:<6}{len(g):>7}{100*(g.pnl>0).mean():>7.0f}{g.pct.mean():>8.2f}"
              f"{g.pct.median():>9.2f}{g.pnl.sum():>11,.0f}{g.days.mean():>10.0f}")


for s in SYMBOLS:
    run(s)
