"""User's chosen portfolio: 10 Large (N100) + 10 Mid (Midcap150) + 15 Small&Micro
(Smallcap250 ∪ Microcap250), each sleeve = aligned-stack-eligible (90/80/60/50) top-K by
20-day ROC, monthly rebalance, PIT, 2022-01→2026-06. Measured as ONE book.
Two weightings: equal-weight all ~35 names, and equal-capital per sleeve (1/3 each).
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2022, 1, 1)
ROC_DAYS = 20
THRESH = {"mean_long": 50, "mean_mid": 60, "mean_short": 80, "truevx_score": 90}
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
COST = BUY_BPS + SELL_BPS
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
SLEEVES = [("Large", ["NIFTY100"], 10), ("Mid", ["NIFTYMIDCAP150"], 10),
           ("Small&Micro", ["NIFTYSMALLCAP250", "NIFTYMICROCAP250"], 15)]


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
ixs = sorted({i for _, l, _ in SLEEVES for i in l})
mem = {i: load_membership(db, i, anchor=date.today()) for i in ixs}
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snaps={len(snaps)} ({snaps[0]}..{snaps[-1]})  ROC={ROC_DAYS}d  gate=90/80/60/50")

close_at, elig_at, roc_at = {}, {}, {}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    close_at[sym] = px.reindex(snap_ts)
    roc_at[sym] = (px / px.shift(ROC_DAYS) - 1).reindex(snap_ts, method="ffill")
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_long": 1, "data.mean_mid": 1,
         "data.mean_short": 1, "data.truevx_score": 1}))
    if irows:
        idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
        cond = None
        for f, thr in THRESH.items():
            c = pd.Series({d: r["data"].get(f) for d, r in zip(idx, irows)}).sort_index() > thr
            cond = c if cond is None else (cond & c)
        elig_at[sym] = cond.reindex(snap_ts, method="ffill")

nifty = close_series("Nifty 50").reindex(snap_ts)


def is_elig(s, d0):
    e = elig_at.get(s)
    return e is not None and e.get(d0) == True   # noqa: E712


def rocv(s, d0):
    v = roc_at[s].get(d0, np.nan)
    return v if np.isfinite(v) else -9e9


def ret_of(s, d0, d1):
    c = close_at.get(s)
    if c is None:
        return None
    c0, c1 = c.get(d0), c.get(d1)
    return (c1 / c0 - 1) if (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0) else None


ew_all, ew_sleeve, breadth, comp = [], [], [], {"Large": [], "Mid": [], "Small&Micro": []}
prev = set()
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    sleeve_sel, allnames = {}, []
    for name, idxlist, K in SLEEVES:
        uni = set()
        for i in idxlist:
            uni |= members_as_of(mem[i], snaps[t])
        elig = [s for s in uni if is_elig(s, d0) and ret_of(s, d0, d1) is not None]
        elig.sort(key=lambda s: rocv(s, d0), reverse=True)
        sel = elig[:K]
        sleeve_sel[name] = sel
        allnames += sel
        comp[name].append(len(sel))
    rets = {s: ret_of(s, d0, d1) for s in allnames}
    # equal-weight all names
    g_all = float(np.mean(list(rets.values()))) if rets else 0.0
    # equal-capital per sleeve (1/3 each; within sleeve EW)
    sret = [np.mean([rets[s] for s in sleeve_sel[nm]]) for nm in ("Large", "Mid", "Small&Micro") if sleeve_sel[nm]]
    g_sl = float(np.mean(sret)) if sret else 0.0
    cur = set(allnames)
    turn = (len(cur - prev) / len(cur)) if cur else 0.0
    ew_all.append(g_all - turn * COST)
    ew_sleeve.append(g_sl - turn * COST)
    breadth.append(len(cur))
    prev = cur

months = len(ew_all)


def stats(s):
    s = np.asarray([x for x in s if np.isfinite(x)])
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12 / len(s)) - 1
    sh = s.mean() / s.std() * math.sqrt(12) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    tot = (eq[-1] - 1) * 100
    win = (s > 0).mean() * 100
    return tot, cagr * 100, sh, mdd * 100, win


print(f"\n=== YOUR PORTFOLIO: 10 Large + 10 Mid + 15 Small&Micro (net, {months}mo, 2022-01→2026-06) ===")
print(f"{'weighting':24}{'avgN':>5}{'totRet%':>9}{'CAGR%':>7}{'Sharpe':>7}{'maxDD%':>8}{'win%':>6}")
for lbl, ser in [("EW all ~35 names", ew_all), ("equal-capital per sleeve", ew_sleeve)]:
    tr, cg, sh, dd, win = stats(ser)
    print(f"{lbl:24}{np.mean(breadth):>5.0f}{tr:>9.0f}{cg:>7.1f}{sh:>7.2f}{dd:>8.1f}{win:>6.0f}")
nf = stats([(nifty.get(snap_ts[t + 1]) / nifty.get(snap_ts[t]) - 1)
            if (nifty.get(snap_ts[t]) and nifty.get(snap_ts[t + 1])) else np.nan for t in range(months)])
print(f"{'Nifty 50':24}{'-':>5}{nf[0]:>9.0f}{nf[1]:>7.1f}{nf[2]:>7.2f}{nf[3]:>8.1f}{nf[4]:>6.0f}")

print(f"\navg sleeve breadth: Large {np.mean(comp['Large']):.0f} | Mid {np.mean(comp['Mid']):.0f} | Small&Micro {np.mean(comp['Small&Micro']):.0f}")
print("\nper-year (EW all):")
ser = pd.Series(ew_all, index=[pd.Timestamp(snaps[t]) for t in range(months)])
for yr in sorted({d.year for d in ser.index}):
    s = ser[[d.year == yr for d in ser.index]].values
    eq = np.cumprod(1 + s)
    print(f"  {yr}: {(eq[-1]-1)*100:+6.1f}%   ({len(s)}mo)")
print(f"\n[aligned-stack gate; top-K per sleeve by {ROC_DAYS}d ROC; monthly EW; PIT; net statutory cost]")
