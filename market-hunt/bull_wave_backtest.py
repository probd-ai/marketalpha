#!/usr/bin/env python3
"""
Bull-Wave backtest — per-stock accumulate->liquidate state machine on Nifty 50 (point-in-time).

Spec (user-approved 2026-06-24):
  Per stock, independent:
    BUY (monthly, first trading day):  mean_long>50 AND truevx_score<=mean_long AND mean_short<=mean_long
        -> add a fixed tranche = N/20 (DCA; keeps accumulating each qualifying month), cash permitting.
        Only buy names that are PIT index members as-of that month.
    EXIT (checked daily): mean_long's most-recent crossing of grid {50,60,70,80,90} was DOWNWARD
        AND truevx_score>mean_long AND mean_short>mean_long  -> sell the ENTIRE position at once.
  Start cash N; exits recycle cash; idle cash earns 0%. Gross of costs (v1). Benchmark = Nifty 50.

Run: .venv/Scripts/python.exe bull_wave_backtest.py
"""
import sys, math, statistics, csv
from datetime import datetime, date
from pymongo import MongoClient

sys.path.insert(0, ".")
from membership import load_membership, members_as_of, union_over

DB_URI = "mongodb://localhost:27017"
DB_NAME = "market_hunt"
START = datetime(2020, 1, 1)
END = datetime(2026, 8, 30)
N0 = 1_000_000.0
TRANCHE = N0 / 20.0            # fixed rupee tranche per qualifying name per buy-month
GRID = [50, 60, 70, 80, 90]
RF = 0.06
BENCH_SYM = "Nifty 50"

def partitions_for(y0, y1):
    cols = set()
    for y in range(y0, y1 + 1):
        s = (y // 5) * 5
        cols.add(f"prices_{s}_{s+4}")
    return sorted(cols)

def load_prices(db, symbols):
    """symbol -> {date_str: close}."""
    out = {s: {} for s in symbols}
    for col in partitions_for(START.year, END.year):
        cur = db[col].find({"symbol": {"$in": list(symbols)}, "date": {"$gte": START, "$lte": END}},
                           {"symbol": 1, "date": 1, "close_price": 1})
        for d in cur:
            out[d["symbol"]][d["date"].strftime("%Y-%m-%d")] = float(d["close_price"])
    return out

def load_indicators(db, symbols):
    """symbol -> {date_str: (ml, ms, tvx)}."""
    out = {s: {} for s in symbols}
    cur = db["indicators"].find(
        {"indicator_type": "truevx", "symbol": {"$in": list(symbols)}, "date": {"$gte": START, "$lte": END}},
        {"symbol": 1, "date": 1, "data": 1})
    for d in cur:
        dd = d.get("data") or {}
        out[d["symbol"]][d["date"].strftime("%Y-%m-%d")] = (
            dd.get("mean_long") or 0.0, dd.get("mean_short") or 0.0, dd.get("truevx_score") or 0.0)
    return out

def crossed_down(prev_ml, cur_ml):
    """True if mean_long fell THROUGH at least one grid level since prev (downward cross)."""
    if cur_ml >= prev_ml:
        return False
    return any(cur_ml < L <= prev_ml for L in GRID)

def crossed_up(prev_ml, cur_ml):
    if cur_ml <= prev_ml:
        return False
    return any(prev_ml < L <= cur_ml for L in GRID)

def main():
    db = MongoClient(DB_URI)[DB_NAME]
    anchor = date(2026, 6, 24)
    intervals = load_membership(db, "NIFTY50", anchor)
    ever = sorted(union_over(intervals, START.date(), END.date()))
    print(f"Nifty50 PIT ever-members in window: {len(ever)}")

    prices = load_prices(db, ever)
    inds = load_indicators(db, ever)
    bench = load_prices(db, [BENCH_SYM]).get(BENCH_SYM, {})

    # global trading calendar = dates where ANY ever-member has both indicator and price
    all_dates = set()
    for s in ever:
        all_dates |= (set(inds[s].keys()) & set(prices[s].keys()))
    dates = sorted(all_dates)
    if not dates:
        print("No data."); return
    # monthly buy dates = first trading day of each calendar month
    buy_dates = set()
    seen_month = set()
    for ds in dates:
        ym = ds[:7]
        if ym not in seen_month:
            seen_month.add(ym); buy_dates.add(ds)

    # state
    cash = N0
    pos = {}            # symbol -> {"shares": float, "cost": float, "open_date": str, "tranches": int}
    last_cross_dir = {} # symbol -> 'up'/'down'/None  (most recent grid cross of mean_long)
    prev_ml = {}        # symbol -> previous day's mean_long (for cross detection)
    last_close = {}     # symbol -> last seen close (for valuation on gap days)

    eq_curve = []       # (date, equity)
    n_buys = 0; n_liquidations = 0; hold_days = []; names_held_samples = []; cash_pct_samples = []

    for ds in dates:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        members = members_as_of(intervals, d)

        # update last close for valuation
        for s in ever:
            if ds in prices[s]:
                last_close[s] = prices[s][ds]

        # --- update mean_long cross state (daily) ---
        for s in ever:
            if ds in inds[s]:
                ml = inds[s][ds][0]
                if s in prev_ml:
                    if crossed_down(prev_ml[s], ml):
                        last_cross_dir[s] = "down"
                    elif crossed_up(prev_ml[s], ml):
                        last_cross_dir[s] = "up"
                prev_ml[s] = ml

        # --- EXIT check (daily): liquidate full position on trigger ---
        for s in list(pos.keys()):
            if ds in inds[s]:
                ml, ms, tvx = inds[s][ds]
                if last_cross_dir.get(s) == "down" and tvx > ml and ms > ml:
                    px = prices[s].get(ds, last_close.get(s))
                    if px:
                        cash += pos[s]["shares"] * px
                        hold_days.append((d - datetime.strptime(pos[s]["open_date"], "%Y-%m-%d").date()).days)
                        n_liquidations += 1
                        del pos[s]

        # --- BUY (monthly): add a tranche to each qualifying PIT member, cash permitting ---
        if ds in buy_dates:
            for s in ever:
                if s not in members:            # only buy current index members
                    continue
                if ds not in inds[s] or ds not in prices[s]:
                    continue
                ml, ms, tvx = inds[s][ds]
                if ml > 50 and tvx <= ml and ms <= ml:   # BUY condition B
                    if cash < 1.0:
                        break
                    spend = min(TRANCHE, cash)
                    px = prices[s][ds]
                    sh = spend / px
                    if s in pos:
                        pos[s]["shares"] += sh; pos[s]["cost"] += spend; pos[s]["tranches"] += 1
                    else:
                        pos[s] = {"shares": sh, "cost": spend, "open_date": ds, "tranches": 1}
                    cash -= spend; n_buys += 1

        # --- mark-to-market equity ---
        mv = sum(pos[s]["shares"] * (prices[s].get(ds) or last_close.get(s, 0)) for s in pos)
        equity = cash + mv
        eq_curve.append((ds, equity))
        names_held_samples.append(len(pos))
        cash_pct_samples.append(100.0 * cash / equity if equity > 0 else 0)

    # --- metrics ---
    eq = [e for _, e in eq_curve]
    total_return = (eq[-1] / N0 - 1) * 100
    yrs = (END - START).days / 365.25
    cagr = ((eq[-1] / N0) ** (1 / yrs) - 1) * 100
    rets = [eq[i] / eq[i-1] - 1 for i in range(1, len(eq)) if eq[i-1] > 0]
    if rets:
        mu, sd = statistics.mean(rets), (statistics.stdev(rets) if len(rets) > 1 else 0)
        ann_ret = (1 + mu) ** 252 - 1
        ann_vol = sd * math.sqrt(252)
        sharpe = (ann_ret - RF) / ann_vol if ann_vol > 0 else 0
    else:
        sharpe = 0
    peak = -1e18; mdd = 0
    for e in eq:
        peak = max(peak, e); mdd = min(mdd, e / peak - 1)
    mdd *= 100

    # benchmark buy&hold
    bdates = [ds for ds, _ in eq_curve if ds in bench]
    if bdates:
        b0, b1 = bench[bdates[0]], bench[bdates[-1]]
        bench_ret = (b1 / b0 - 1) * 100
        bench_cagr = ((b1 / b0) ** (1 / yrs) - 1) * 100
    else:
        bench_ret = bench_cagr = 0

    print(f"\n=== BULL-WAVE — Nifty50 PIT, {dates[0]}..{dates[-1]} (N=Rs{N0:,.0f}, tranche=Rs{TRANCHE:,.0f}) ===")
    print(f"  total_return = {total_return:8.1f}%   CAGR = {cagr:6.2f}%   Sharpe = {sharpe:5.2f}   maxDD = {mdd:6.1f}%")
    print(f"  Nifty 50 b&h = {bench_ret:8.1f}%   CAGR = {bench_cagr:6.2f}%   -> alpha = {cagr-bench_cagr:+.2f}% CAGR")
    print(f"\n  Diagnostics:")
    print(f"   accumulation buys (tranches): {n_buys}")
    print(f"   full liquidations:            {n_liquidations}")
    print(f"   avg names held:               {statistics.mean(names_held_samples):.1f}  (max {max(names_held_samples)})")
    print(f"   avg cash % (undeployed):      {statistics.mean(cash_pct_samples):.1f}%")
    if hold_days:
        print(f"   avg wave holding period:      {statistics.mean(hold_days):.0f} days  (median {statistics.median(hold_days):.0f})")
    print(f"   final: equity Rs{eq[-1]:,.0f}  (cash Rs{cash:,.0f}, {100*cash/eq[-1]:.0f}% idle)  open positions {len(pos)}")

    out = r"C:\Users\ARPITH~1\AppData\Local\Temp\claude\D--gs-workspace-tvagent\f453812e-f4cf-4eae-881e-e9de19eb132d\scratchpad\bull_wave_equity.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "equity"]); w.writerows(eq_curve)
    print(f"\n  equity curve -> {out}")

if __name__ == "__main__":
    main()
