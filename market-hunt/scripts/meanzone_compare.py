"""Trend-state strategy on the smoothed RS score — mean_short vs mean_long, with the
equal-weight-all-N500 CONTROL that isolates whether the trend filter adds anything.

Rule per signal field F (mean_short or mean_long): long once F crosses UP through any
of {20,40,60,80,90}, out once it crosses DOWN through any. Monthly, hold ALL currently-
long PIT-N500 members equal-weight (no top-K), snapshot-to-snapshot returns.

CONTROL = equal-weight ALL PIT-N500 members each month (no filter), drawn from the SAME
investable set (members with the indicator + prices). If a filtered strategy doesn't beat
this control, its edge is just the equal-weight/size tilt, not the signal. Nifty 50 = the
cap-weighted index. All four on identical snapshot dates. No look-ahead.
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
SNAP_FROM = date(2019, 1, 1)          # warms up mean_long's 222-day SMA
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
FIELDS = ["mean_short", "mean_long"]
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


def trend_state(series: pd.Series) -> pd.Series:
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

state_at = {f: {} for f in FIELDS}     # field -> sym -> Series(state @ snaps)
close_at = {}                          # sym -> Series(close @ snaps)
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_short": 1, "data.mean_long": 1}))
    if not irows:
        continue
    idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
    close_at[sym] = px.reindex(snap_ts)
    for f in FIELDS:
        s = pd.Series({d: r["data"].get(f) for d, r in zip(idx, irows)}).sort_index().dropna()
        if not s.empty:
            state_at[f][sym] = trend_state(s).reindex(snap_ts, method="ffill")

nifty = close_series("Nifty 50").reindex(snap_ts)

# monthly backtest: strat per field, control (all), nifty
series = {f: [] for f in FIELDS}
control, nif = [], []
breadth = {f: [] for f in FIELDS}
prev_sets = {f: set() for f in FIELDS}
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    cand = []  # (sym, ret) for members with valid prices both ends
    for sym in members:
        c = close_at.get(sym)
        if c is None:
            continue
        c0, c1 = c.get(d0), c.get(d1)
        if c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0:
            cand.append((sym, c1 / c0 - 1))
    control.append(float(np.mean([r for _, r in cand])) if cand else 0.0)
    nif.append((nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan)
    for f in FIELDS:
        sel = [(s, r) for s, r in cand if state_at[f].get(s) is not None and state_at[f][s].get(d0, 0) == 1]
        n = len(sel)
        gross = float(np.mean([r for _, r in sel])) if sel else 0.0
        cur = {s for s, _ in sel}
        turn = (len(cur - prev_sets[f]) / n) if n else 0.0
        net = gross - turn * (BUY_BPS + SELL_BPS)
        series[f].append((gross, net))
        breadth[f].append(n)
        prev_sets[f] = cur

months = len(control)


def stats(rets):
    s = np.array([r for r in rets if np.isfinite(r)])
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12.0 / months) - 1
    sharpe = (s.mean() / s.std() * math.sqrt(12)) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return (eq[-1] - 1) * 100, cagr * 100, sharpe, mdd * 100


print(f"\n=== trend-state strategy: mean_short vs mean_long vs controls · PIT N500 · {snaps[0]}..{snaps[-1]} ===")
print(f"{'series':22} {'totRet%':>9} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
for f in FIELDS:
    for j, lab in [(0, "gross"), (1, "net")]:
        tr, cg, sh, dd = stats([x[j] for x in series[f]])
        print(f"{(f+' '+lab):22} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
tr, cg, sh, dd = stats(control)
print(f"{'EW-all-N500 (control)':22} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
tr, cg, sh, dd = stats(nif)
print(f"{'Nifty 50':22} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")

print(f"\n=== breadth (eligible count / month) ===")
for f in FIELDS:
    b = np.array(breadth[f])
    print(f"  {f:11}: min={b.min()} median={int(np.median(b))} max={b.max()} cash-months={int((b==0).sum())}")
print("\n[gross/net statutory cost; control = same universe, no filter; no look-ahead]")
