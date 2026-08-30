#!/usr/bin/env python3
"""
Mean-Long Level-Wave backtest — per-stock, Nifty 50 (point-in-time).

Spec (user-approved 2026-06-24): pure mean_long level-crossing (no truevx/short in logic).
  ACCUMULATE (event, checked daily): when mean_long crosses UP through 40, 50, or 60
      -> add one tranche (N/20) per level crossed up that day. Scales in 40->50->60.
      Entry blocked if the same move also crosses up through 70 (already past take-profit).
  EXIT (full liquidation at once): when holding and mean_long either
      (i) crosses UP through 70  (take profit at strong-zone climax), OR
      (ii) crosses DOWN through ANY of {40,50,60,70} (trend rolled over, stop).
  Re-entry allowed: once flat, buy again on the next up-cross of 40/50/60 (recurring waves).
  Start cash N; exits recycle cash; idle cash 0%. Gross of costs. Benchmark = Nifty 50.

Run: .venv/Scripts/python.exe meanlong_wave_backtest.py
"""
import sys, math, statistics, csv
from datetime import datetime, date
from pymongo import MongoClient

sys.path.insert(0, ".")
from membership import load_membership, members_as_of, union_over

DB_URI = "mongodb://localhost:27017"; DB_NAME = "market_hunt"
START = datetime(2020, 1, 1); END = datetime(2026, 8, 30)
N0 = 1_000_000.0; TRANCHE = N0 / 20.0
BUY_LEVELS = {40, 50, 60}
ALL_LEVELS = {40, 50, 60, 70}
RF = 0.06; BENCH_SYM = "Nifty 50"

def partitions_for(y0, y1):
    return sorted({f"prices_{(y//5)*5}_{(y//5)*5+4}" for y in range(y0, y1 + 1)})

def load_prices(db, symbols):
    out = {s: {} for s in symbols}
    for col in partitions_for(START.year, END.year):
        for d in db[col].find({"symbol": {"$in": list(symbols)}, "date": {"$gte": START, "$lte": END}},
                              {"symbol": 1, "date": 1, "close_price": 1}):
            out[d["symbol"]][d["date"].strftime("%Y-%m-%d")] = float(d["close_price"])
    return out

def load_indicators(db, symbols):
    out = {s: {} for s in symbols}
    for d in db["indicators"].find(
        {"indicator_type": "truevx", "symbol": {"$in": list(symbols)}, "date": {"$gte": START, "$lte": END}},
        {"symbol": 1, "date": 1, "data": 1}):
        out[d["symbol"]][d["date"].strftime("%Y-%m-%d")] = (d.get("data") or {}).get("mean_long") or 0.0
    return out

def up_crosses(prev, cur):
    return {L for L in ALL_LEVELS if prev < L <= cur} if cur > prev else set()

def down_crosses(prev, cur):
    return {L for L in ALL_LEVELS if cur < L <= prev} if cur < prev else set()

def main():
    db = MongoClient(DB_URI)[DB_NAME]
    intervals = load_membership(db, "NIFTY50", date(2026, 6, 24))
    ever = sorted(union_over(intervals, START.date(), END.date()))
    print(f"Nifty50 PIT ever-members: {len(ever)}")

    prices = load_prices(db, ever)
    ml = load_indicators(db, ever)
    bench = load_prices(db, [BENCH_SYM]).get(BENCH_SYM, {})

    all_dates = set()
    for s in ever:
        all_dates |= (set(ml[s].keys()) & set(prices[s].keys()))
    dates = sorted(all_dates)
    if not dates:
        print("No data."); return

    cash = N0
    pos = {}              # symbol -> {shares, cost, open_date}
    prev_ml = {}; last_close = {}
    eq_curve = []
    n_buys = 0; exit_up70 = 0; exit_break = 0; reentries = 0; ever_held = set()
    hold_days = []; names_samp = []; cash_samp = []

    for ds in dates:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        members = members_as_of(intervals, d)
        for s in ever:
            if ds in prices[s]:
                last_close[s] = prices[s][ds]

        for s in ever:
            if ds not in ml[s]:
                continue
            cur = ml[s]
            mlv = cur[ds] if isinstance(cur, dict) else ml[s][ds]
            p = prev_ml.get(s)
            ups = up_crosses(p, mlv) if p is not None else set()
            downs = down_crosses(p, mlv) if p is not None else set()
            prev_ml[s] = mlv
            px = prices[s].get(ds, last_close.get(s))

            if s in pos:
                # EXIT checks (full liquidation)
                if 70 in ups or downs:
                    if px:
                        cash += pos[s]["shares"] * px
                        hold_days.append((d - datetime.strptime(pos[s]["open_date"], "%Y-%m-%d").date()).days)
                        exit_up70 += 1 if (70 in ups) else 0
                        exit_break += 1 if (70 not in ups and downs) else 0
                        del pos[s]
                    continue
                # ACCUMULATE more on further up-crosses of 40/50/60
                for L in sorted(ups & BUY_LEVELS):
                    if cash < 1.0 or not px:
                        break
                    spend = min(TRANCHE, cash); sh = spend / px
                    pos[s]["shares"] += sh; pos[s]["cost"] += spend; cash -= spend; n_buys += 1
            else:
                # ENTRY: up-cross of 40/50/60, only if PIT member and not blown past 70
                if (ups & BUY_LEVELS) and (70 not in ups) and (s in members):
                    for L in sorted(ups & BUY_LEVELS):
                        if cash < 1.0 or not px:
                            break
                        spend = min(TRANCHE, cash); sh = spend / px
                        if s in pos:
                            pos[s]["shares"] += sh; pos[s]["cost"] += spend
                        else:
                            pos[s] = {"shares": sh, "cost": spend, "open_date": ds}
                            if s in ever_held:
                                reentries += 1
                            ever_held.add(s)
                        cash -= spend; n_buys += 1

        mv = sum(pos[s]["shares"] * (prices[s].get(ds) or last_close.get(s, 0)) for s in pos)
        equity = cash + mv
        eq_curve.append((ds, equity))
        names_samp.append(len(pos)); cash_samp.append(100.0 * cash / equity if equity > 0 else 0)

    eq = [e for _, e in eq_curve]
    total_return = (eq[-1] / N0 - 1) * 100
    yrs = (END - START).days / 365.25
    cagr = ((eq[-1] / N0) ** (1 / yrs) - 1) * 100
    rets = [eq[i] / eq[i-1] - 1 for i in range(1, len(eq)) if eq[i-1] > 0]
    mu, sd = statistics.mean(rets), statistics.stdev(rets)
    ann_ret = (1 + mu) ** 252 - 1; ann_vol = sd * math.sqrt(252)
    sharpe = (ann_ret - RF) / ann_vol if ann_vol > 0 else 0
    peak = -1e18; mdd = 0
    for e in eq:
        peak = max(peak, e); mdd = min(mdd, e / peak - 1)
    mdd *= 100
    bd = [ds for ds, _ in eq_curve if ds in bench]
    bench_cagr = ((bench[bd[-1]] / bench[bd[0]]) ** (1 / yrs) - 1) * 100 if bd else 0

    print(f"\n=== MEAN-LONG LEVEL-WAVE - Nifty50 PIT, {dates[0]}..{dates[-1]} (N=Rs{N0:,.0f}, tranche=Rs{TRANCHE:,.0f}) ===")
    print(f"  total_return = {total_return:8.1f}%   CAGR = {cagr:6.2f}%   Sharpe = {sharpe:5.2f}   maxDD = {mdd:6.1f}%")
    print(f"  Nifty 50 b&h CAGR = {bench_cagr:.2f}%   -> alpha = {cagr-bench_cagr:+.2f}% CAGR")
    print(f"\n  Diagnostics:")
    print(f"   accumulation buys (tranches): {n_buys}")
    print(f"   exits: {exit_up70+exit_break}  (up-cross 70 take-profit: {exit_up70}, breakdown stop: {exit_break})")
    print(f"   distinct entries / re-entries: {len(ever_held)} names / {reentries} re-entries")
    print(f"   avg names held: {statistics.mean(names_samp):.1f} (max {max(names_samp)})")
    print(f"   avg cash idle: {statistics.mean(cash_samp):.1f}%")
    if hold_days:
        print(f"   avg wave holding period: {statistics.mean(hold_days):.0f} days (median {statistics.median(hold_days):.0f})")
    print(f"   final equity Rs{eq[-1]:,.0f}  (cash Rs{cash:,.0f}, {100*cash/eq[-1]:.0f}% idle, {len(pos)} open)")

    out = r"C:\Users\ARPITH~1\AppData\Local\Temp\claude\D--gs-workspace-tvagent\f453812e-f4cf-4eae-881e-e9de19eb132d\scratchpad\meanlong_wave_equity.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "equity"]); w.writerows(eq_curve)
    print(f"\n  equity curve -> {out}")

if __name__ == "__main__":
    main()
