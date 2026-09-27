"""Buy when mean_long < 20, hold exactly 1 year.

The production engine cannot express this: it re-evaluates the filter at every rebalance,
so a name leaves the book the moment it stops qualifying. A fixed holding period is a
different object, so this is a standalone backtest.

Construction -- overlapping monthly cohorts, the standard way to run a fixed-horizon rule:
  - entry dates = first trading day of each month
  - a name qualifies if it is a PIT member that day AND mean_long < 20 that day
  - each cohort gets a 1/12 sleeve of current equity, split equally across its names
  - every position is sold exactly 12 calendar months later, at that day's close
  - in steady state 12 cohorts overlap, so the book is fully invested without rebalancing
  - 12 bps per side, matching the other studies in this repo

CONTROL is the same machine with the filter removed (every PIT member that month). That is
the comparison that matters: Nifty 50 inflates every alpha figure in this repo because it is
neither equal-weighted nor the same universe. Nifty 50 is reported too, as a reference only.

Delisting is handled honestly: if a symbol series ends before its exit date, the position is
closed at its last real price. No forward-filling to the exit, no zeroing the holding.
"""
import json
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from pymongo import MongoClient

sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
from settings import MONGO_URI, DB_NAME
from membership import load_membership, members_as_of, union_over

COST, HOLD_MONTHS, SLEEVES, CAPITAL, RF = 0.0012, 12, 12, 100_000.0, 0.06
START, END = datetime(2020, 1, 1), datetime(2026, 9, 4)
WARM = datetime(2019, 1, 1)
UNIVERSES = ["NIFTY200", "NIFTY500", "NIFTYSMALLCAP250", "NIFTYMICROCAP250", "NIFTY500MICRO750"]
WINDOWS = [("full", datetime(2020, 1, 1), datetime(2026, 9, 4)),
           ("W1", datetime(2020, 1, 1), datetime(2023, 6, 30)),
           ("W2", datetime(2023, 7, 1), datetime(2026, 9, 4))]
OUT = "_ml20_hold1y_results.json"

db = MongoClient(MONGO_URI)[DB_NAME]
COLLS = sorted(c for c in db.list_collection_names() if c.startswith("prices_"))


def load_prices(symbols):
    rows = []
    for c in COLLS:
        rows += list(db[c].find({"symbol": {"$in": list(symbols)},
                                 "date": {"$gte": WARM, "$lte": END}},
                                {"_id": 0, "symbol": 1, "date": 1, "close_price": 1}))
    df = pd.DataFrame(rows).dropna(subset=["close_price"])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.drop_duplicates(["symbol", "date"])
    return df.pivot(index="date", columns="symbol", values="close_price").sort_index().astype(float)


def load_mean_long(symbols):
    out = {}
    for d in db.indicators.find({"indicator_type": "truevx", "symbol": {"$in": list(symbols)},
                                 "date": {"$gte": WARM, "$lte": END}},
                                {"_id": 0, "symbol": 1, "date": 1, "data.mean_long": 1}):
        v = (d.get("data") or {}).get("mean_long")
        if v is not None:
            out.setdefault(d["symbol"], {})[pd.Timestamp(d["date"]).normalize()] = float(v)
    return pd.DataFrame(out).sort_index()


def entry_dates(index, lo, hi):
    """First trading day of each month in [lo, hi]."""
    d = index[(index >= lo) & (index <= hi)]
    return [g.index[0] for _, g in pd.DataFrame(index=d).groupby([d.year, d.month])]


def simulate(C, ML, ivals, lo, hi, use_filter):
    dates = C.index[(C.index >= lo) & (C.index <= hi)]
    if len(dates) < 60:
        return None
    entries = set(entry_dates(C.index, lo, hi))
    last_bar = {s: C[s].last_valid_index() for s in C.columns}

    cash, book, eq, n_names, cohort_sizes = CAPITAL, [], [], [], []
    for dt in dates:
        # --- exits: due date reached, or the series has run out (delisted) ---
        still = []
        for p in book:
            lb = last_bar[p["sym"]]
            dead = lb is not None and dt >= lb
            if dt >= p["exit_on"] or dead:
                px = C.at[dt, p["sym"]]
                if not np.isfinite(px):          # closed at its last real print
                    srs = C[p["sym"]].loc[:dt].dropna()
                    px = srs.iloc[-1] if len(srs) else 0.0
                cash += p["sh"] * px * (1 - COST)
            else:
                still.append(p)
        book = still

        # --- entries ---
        if dt in entries:
            members = members_as_of(ivals, dt.date())
            cand = [s for s in members if s in C.columns and np.isfinite(C.at[dt, s])]
            if use_filter:
                cand = [s for s in cand
                        if s in ML.columns and np.isfinite(ML.at[dt, s]) and ML.at[dt, s] < 20]
            cohort_sizes.append(len(cand))
            if cand:
                equity = cash + sum(p["sh"] * C.at[dt, p["sym"]] for p in book
                                    if np.isfinite(C.at[dt, p["sym"]]))
                sleeve = min(equity / SLEEVES, cash / (1 + COST))
                if sleeve > 0:
                    per = sleeve / len(cand)
                    exit_on = pd.Timestamp(dt.date() + relativedelta(months=HOLD_MONTHS))
                    for s in cand:
                        book.append({"sym": s, "sh": per / C.at[dt, s], "exit_on": exit_on})
                    cash -= sleeve * (1 + COST)

        mtm = 0.0
        for p in book:
            px = C.at[dt, p["sym"]]
            if not np.isfinite(px):
                srs = C[p["sym"]].loc[:dt].dropna()
                px = srs.iloc[-1] if len(srs) else 0.0
            mtm += p["sh"] * px
        eq.append(cash + mtm)
        n_names.append(len({p["sym"] for p in book}))

    eq = np.asarray(eq)
    yrs = (dates[-1] - dates[0]).days / 365.25
    peak = np.maximum.accumulate(eq)
    r = np.diff(eq) / eq[:-1]
    sd = r.std(ddof=1)
    return {"cagr": round(((eq[-1] / CAPITAL) ** (1 / yrs) - 1) * 100, 2),
            "total": round((eq[-1] / CAPITAL - 1) * 100, 2),
            "dd": round(((eq - peak) / peak).min() * 100, 2),
            "sharpe": round((r.mean() * 252 - RF) / (sd * np.sqrt(252)), 3) if sd > 0 else None,
            "names": round(float(np.mean(n_names)), 1),
            "cohort": round(float(np.mean(cohort_sizes)), 1) if cohort_sizes else 0.0,
            "yrs": round(yrs, 2)}


bench = pd.DataFrame([r for c in COLLS for r in
                      db[c].find({"symbol": "Nifty 50", "date": {"$gte": START, "$lte": END}},
                                 {"_id": 0, "date": 1, "close_price": 1})]).dropna()
bench["date"] = pd.to_datetime(bench["date"]).dt.normalize()
bench = bench.drop_duplicates("date").set_index("date").sort_index()["close_price"]

results = []
for uni in UNIVERSES:
    print(f"\n===== {uni} =====", flush=True)
    ivals = load_membership(db, uni, date.today())
    syms = sorted(union_over(ivals, START.date(), END.date()))
    print(f"  {len(syms)} ever-members; loading prices + mean_long ...", flush=True)
    C = load_prices(syms)
    ML = load_mean_long(syms).reindex(C.index)
    print(f"  prices {C.shape}, mean_long {ML.shape}", flush=True)
    for wname, lo, hi in WINDOWS:
        row = {"universe": uni, "window": wname}
        for tag, uf in (("ml20", True), ("control", False)):
            r = simulate(C, ML, ivals, lo, hi, uf)
            if r:
                row[tag] = r
        b = bench[(bench.index >= lo) & (bench.index <= hi)]
        if len(b) > 1:
            y = (b.index[-1] - b.index[0]).days / 365.25
            row["nifty50_cagr"] = round(((b.iloc[-1] / b.iloc[0]) ** (1 / y) - 1) * 100, 2)
        results.append(row)
        json.dump(results, open(OUT, "w"), indent=2)
        m, c = row.get("ml20"), row.get("control")
        if m and c:
            print(f"  {wname:<5} ml<20 CAGR {m['cagr']:>7.2f}%  Sharpe {str(m['sharpe']):>6}  "
                  f"DD {m['dd']:>7.2f}%  cohort {m['cohort']:>5.1f}  held {m['names']:>5.1f}   |   "
                  f"CONTROL {c['cagr']:>7.2f}%  Sharpe {str(c['sharpe']):>6}  DD {c['dd']:>7.2f}%   "
                  f"| edge {m['cagr'] - c['cagr']:+.2f}pp", flush=True)
print("\nDONE", flush=True)
