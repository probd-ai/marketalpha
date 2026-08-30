"""mean_long leaders strategy: full metrics + a test of the 'multi-peeling' idea.

Part A: detailed metrics for the mean_long trend-state strategy (hold all eligible).
Part B: the recursive-filter premise. Among the eligible leaders each month, split into
terciles by mean_long LEVEL (a cheap proxy for 'even stronger within the strong' — what a
second RS layer would select). If the top tercile beats the full eligible set, peeling
again helps; if not, it won't. Compares top/mid/bottom tercile vs full-eligible vs control.

No look-ahead. PIT N500. Same window/cost model as meanzone_compare.py.
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

BOUNDS = [20, 40, 60, 80, 90]
START = datetime(2018, 1, 1)
SNAP_FROM = date(2019, 1, 1)
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]


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


def trend_state(series):
    state, prev, out = 0, None, {}
    for d, v in series.items():
        if prev is not None and not math.isnan(prev) and not math.isnan(v):
            if any(prev < b <= v for b in BOUNDS):
                state = 1
            elif any(prev > b >= v for b in BOUNDS):
                state = 0
        out[d] = state
        prev = v
    return pd.Series(out)


cal = close_series("RELIANCE")
snaps = [d.date() for d in pd.Series(cal.index).groupby([cal.index.year, cal.index.month]).min()]
snaps = [d for d in snaps if d >= SNAP_FROM]
snap_ts = [pd.Timestamp(d) for d in snaps]
iv500 = load_membership(db, "NIFTY500", anchor=date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snapshots={len(snaps)} ({snaps[0]}..{snaps[-1]})  symbols={len(syms)}")

state_at, level_at, close_at = {}, {}, {}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_long": 1}))
    if not irows:
        continue
    ml = pd.Series({pd.to_datetime(r["date"]).normalize(): r["data"].get("mean_long") for r in irows}).sort_index().dropna()
    if ml.empty:
        continue
    state_at[sym] = trend_state(ml).reindex(snap_ts, method="ffill")
    level_at[sym] = ml.reindex(snap_ts, method="ffill")
    close_at[sym] = px.reindex(snap_ts)

nifty = close_series("Nifty 50").reindex(snap_ts)

# series: full eligible, terciles by level, control, nifty
full, top, mid, bot, control, nif = [], [], [], [], [], []
holds, turn_list, beat_control = [], [], []
prev_set = set()
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    cand = []   # (sym, ret)
    elig = []   # (sym, level, ret)
    for sym in members:
        c = close_at.get(sym)
        if c is None:
            continue
        c0, c1 = c.get(d0), c.get(d1)
        if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
            continue
        ret = c1 / c0 - 1
        cand.append((sym, ret))
        if state_at.get(sym) is not None and state_at[sym].get(d0, 0) == 1:
            lv = level_at[sym].get(d0)
            if lv is not None and np.isfinite(lv):
                elig.append((sym, lv, ret))
    ctrl = float(np.mean([r for _, r in cand])) if cand else 0.0
    control.append(ctrl)
    nif.append((nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan)

    n = len(elig)
    holds.append(n)
    if n:
        rets = [r for _, _, r in elig]
        fr = float(np.mean(rets))
        cur = {s for s, _, _ in elig}
        turn_list.append(len(cur - prev_set) / n)
        prev_set = cur
        # terciles by level
        elig_sorted = sorted(elig, key=lambda x: x[1])      # ascending level
        k = max(1, n // 3)
        bot.append(float(np.mean([r for _, _, r in elig_sorted[:k]])))
        mid.append(float(np.mean([r for _, _, r in elig_sorted[k:2 * k]])) if n >= 2 else fr)
        top.append(float(np.mean([r for _, _, r in elig_sorted[-k:]])))
    else:
        fr = 0.0
        turn_list.append(0.0)
        bot.append(0.0); mid.append(0.0); top.append(0.0)
    full.append(fr)
    beat_control.append(1 if fr > ctrl else 0)

months = len(full)


def stats(rets):
    s = np.array([r for r in rets if np.isfinite(r)])
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12.0 / months) - 1
    sharpe = (s.mean() / s.std() * math.sqrt(12)) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return (eq[-1] - 1) * 100, cagr * 100, sharpe, mdd * 100


fa = np.array(full)
print("\n=== Part A: mean_long leaders strategy — metrics ===")
print(f"  holdings: avg={np.mean(holds):.0f} median={int(np.median(holds))} min={min(holds)} max={max(holds)}")
print(f"  avg monthly turnover: {np.mean(turn_list)*100:.0f}%   (implied avg holding ~{1/np.mean(turn_list):.1f} months)")
print(f"  monthly win rate: {(fa>0).mean()*100:.0f}%   avg monthly ret: {fa.mean()*100:.2f}%")
print(f"  best month: {fa.max()*100:+.1f}%   worst month: {fa.min()*100:+.1f}%")
print(f"  months beating control: {np.mean(beat_control)*100:.0f}%")
tr, cg, sh, dd = stats(full)
print(f"  gross: totRet={tr:.1f}%  CAGR={cg:.1f}%  Sharpe={sh:.2f}  maxDD={dd:.1f}%")

print("\n=== Part B: 'peel again' test — terciles by mean_long LEVEL within eligible ===")
print(f"{'group':24} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
for lab, col in [("top tercile (strongest)", top), ("mid tercile", mid),
                 ("bottom tercile (weakest)", bot), ("FULL eligible", full),
                 ("EW-all-N500 control", control), ("Nifty 50", nif)]:
    tr, cg, sh, dd = stats(col)
    print(f"{lab:24} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
print(f"\navg tercile size ~{np.mean(holds)/3:.0f} stocks")
print("\n[gross; terciles split the eligible leaders by mean_long level each month; no look-ahead]")
