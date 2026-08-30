#!/usr/bin/env python3
"""Mean-Long Level-Wave OOS split (Nifty 50 PIT): full / 2020-23 / 2023-26.
Same logic as meanlong_wave_backtest.py; loads data once, runs each window with fresh state."""
import sys, math, statistics
from datetime import datetime, date
from pymongo import MongoClient
sys.path.insert(0, ".")
from membership import load_membership, members_as_of, union_over

DB_URI = "mongodb://localhost:27017"; DB_NAME = "market_hunt"
DATA_START = datetime(2020, 1, 1); DATA_END = datetime(2026, 8, 30)
N0 = 1_000_000.0; TRANCHE = N0 / 20.0
BUY_LEVELS = {40, 50, 60}; ALL_LEVELS = {40, 50, 60, 70}
RF = 0.06; BENCH_SYM = "Nifty 50"

def parts(y0, y1): return sorted({f"prices_{(y//5)*5}_{(y//5)*5+4}" for y in range(y0, y1 + 1)})

def load_prices(db, syms):
    out = {s: {} for s in syms}
    for col in parts(DATA_START.year, DATA_END.year):
        for d in db[col].find({"symbol": {"$in": list(syms)}, "date": {"$gte": DATA_START, "$lte": DATA_END}},
                              {"symbol": 1, "date": 1, "close_price": 1}):
            out[d["symbol"]][d["date"].strftime("%Y-%m-%d")] = float(d["close_price"])
    return out

def load_ml(db, syms):
    out = {s: {} for s in syms}
    for d in db["indicators"].find({"indicator_type": "truevx", "symbol": {"$in": list(syms)},
                                     "date": {"$gte": DATA_START, "$lte": DATA_END}}, {"symbol": 1, "date": 1, "data": 1}):
        out[d["symbol"]][d["date"].strftime("%Y-%m-%d")] = (d.get("data") or {}).get("mean_long") or 0.0
    return out

def up_x(p, c): return {L for L in ALL_LEVELS if p < L <= c} if c > p else set()
def dn_x(p, c): return {L for L in ALL_LEVELS if c < L <= p} if c < p else set()

def run_window(intervals, prices, ml, bench, ever, start, end, label):
    s0, s1 = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    all_dates = set()
    for s in ever:
        all_dates |= (set(ml[s]) & set(prices[s]))
    dates = sorted(d for d in all_dates if s0 <= d <= s1)
    cash = N0; pos = {}; prev = {}; lastpx = {}
    eq = []; nbuy = 0; e70 = 0; ebrk = 0; held_samp = []; cash_samp = []; holds = []
    for ds in dates:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        members = members_as_of(intervals, d)
        for s in ever:
            if ds in prices[s]: lastpx[s] = prices[s][ds]
        for s in ever:
            if ds not in ml[s]: continue
            v = ml[s][ds]; p = prev.get(s)
            ups = up_x(p, v) if p is not None else set()
            dns = dn_x(p, v) if p is not None else set()
            prev[s] = v; px = prices[s].get(ds, lastpx.get(s))
            if s in pos:
                if 70 in ups or dns:
                    if px:
                        cash += pos[s]["sh"] * px
                        holds.append((d - datetime.strptime(pos[s]["od"], "%Y-%m-%d").date()).days)
                        e70 += 1 if 70 in ups else 0; ebrk += 1 if (70 not in ups and dns) else 0
                        del pos[s]
                    continue
                for L in sorted(ups & BUY_LEVELS):
                    if cash < 1 or not px: break
                    sp = min(TRANCHE, cash); pos[s]["sh"] += sp/px; cash -= sp; nbuy += 1
            else:
                if (ups & BUY_LEVELS) and 70 not in ups and s in members:
                    for L in sorted(ups & BUY_LEVELS):
                        if cash < 1 or not px: break
                        sp = min(TRANCHE, cash)
                        if s in pos: pos[s]["sh"] += sp/px
                        else: pos[s] = {"sh": sp/px, "od": ds}
                        cash -= sp; nbuy += 1
        mv = sum(pos[s]["sh"] * (prices[s].get(ds) or lastpx.get(s, 0)) for s in pos)
        e = cash + mv; eq.append((ds, e)); held_samp.append(len(pos)); cash_samp.append(100*cash/e if e>0 else 0)
    vals = [e for _, e in eq]
    yrs = (end - start).days / 365.25
    cagr = ((vals[-1]/N0)**(1/yrs)-1)*100
    rets = [vals[i]/vals[i-1]-1 for i in range(1, len(vals)) if vals[i-1] > 0]
    mu, sd = statistics.mean(rets), statistics.stdev(rets)
    sharpe = (((1+mu)**252-1) - RF) / (sd*math.sqrt(252)) if sd > 0 else 0
    peak = -1e18; mdd = 0
    for e in vals: peak = max(peak, e); mdd = min(mdd, e/peak-1)
    bd = [ds for ds, _ in eq if ds in bench]
    bcagr = ((bench[bd[-1]]/bench[bd[0]])**(1/yrs)-1)*100 if bd else 0
    return {"label": label, "cagr": cagr, "sharpe": sharpe, "mdd": mdd*100, "alpha": cagr-bcagr,
            "bcagr": bcagr, "deployed": 100-statistics.mean(cash_samp), "held": statistics.mean(held_samp),
            "nbuy": nbuy, "e70": e70, "ebrk": ebrk, "holds": (statistics.mean(holds) if holds else 0)}

def main():
    db = MongoClient(DB_URI)[DB_NAME]
    intervals = load_membership(db, "NIFTY50", date(2026, 6, 24))
    ever = sorted(union_over(intervals, DATA_START.date(), DATA_END.date()))
    prices = load_prices(db, ever); ml = load_ml(db, ever)
    bench = load_prices(db, [BENCH_SYM]).get(BENCH_SYM, {})
    wins = [("full", datetime(2020,1,1), datetime(2026,8,30)),
            ("P1_20_23", datetime(2020,1,1), datetime(2023,1,1)),
            ("P2_23_26", datetime(2023,1,1), datetime(2026,8,30))]
    print(f"Nifty50 PIT ever-members: {len(ever)}\n")
    print(f"{'window':10}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}{'alpha':>8}{'bench':>7}{'depl%':>7}{'held':>6}{'exits(70/brk)':>15}{'holdD':>7}")
    for lbl, a, b in wins:
        r = run_window(intervals, prices, ml, bench, ever, a, b, lbl)
        print(f"{r['label']:10}{r['cagr']:>7.1f}%{r['sharpe']:>8.2f}{r['mdd']:>7.1f}%{r['alpha']:>+7.1f}%{r['bcagr']:>6.1f}%{r['deployed']:>6.0f}%{r['held']:>6.1f}{(str(r['e70'])+'/'+str(r['ebrk'])):>15}{r['holds']:>6.0f}")

if __name__ == "__main__":
    main()
