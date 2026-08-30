"""Aligned-stack filter: long>50 AND mid>60 AND short>80 AND truevx_score>90.

All timeframes stacked bullish and the spot score near the top. Static threshold
filter (not a crossing state machine): eligible on any snapshot where all four hold.
Hold all eligible PIT-N500 members equal-weight, monthly, snapshot-to-snapshot.
Compared to EW-all-N500 control + Nifty 50. Expect low breadth (restrictive + over-
extended top). No look-ahead.
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2019, 1, 1)
THRESH = {"mean_long": 50, "mean_mid": 60, "mean_short": 80, "truevx_score": 90}
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


cal = close_series("RELIANCE")
snaps = [d.date() for d in pd.Series(cal.index).groupby([cal.index.year, cal.index.month]).min()]
snaps = [d for d in snaps if d >= SNAP_FROM]
snap_ts = [pd.Timestamp(d) for d in snaps]
iv500 = load_membership(db, "NIFTY500", anchor=date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snapshots={len(snaps)} ({snaps[0]}..{snaps[-1]})  symbols={len(syms)}")

elig_at, close_at = {}, {}   # sym -> Series(bool eligible @ snaps), Series(close @ snaps)
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_long": 1, "data.mean_mid": 1,
         "data.mean_short": 1, "data.truevx_score": 1}))
    if not irows:
        continue
    idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
    fields = {f: pd.Series({d: r["data"].get(f) for d, r in zip(idx, irows)}).sort_index()
              for f in THRESH}
    cond = None
    for f, thr in THRESH.items():
        c = fields[f] > thr
        cond = c if cond is None else (cond & c)
    elig_at[sym] = cond.reindex(snap_ts, method="ffill").fillna(False)
    close_at[sym] = px.reindex(snap_ts)

nifty = close_series("Nifty 50").reindex(snap_ts)

strat, control, nif, breadth = [], [], [], []
prev_set = set()
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    cand, sel = [], []
    for sym in members:
        c = close_at.get(sym)
        if c is None:
            continue
        c0, c1 = c.get(d0), c.get(d1)
        if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
            continue
        ret = c1 / c0 - 1
        cand.append(ret)
        if elig_at.get(sym) is not None and bool(elig_at[sym].get(d0, False)):
            sel.append((sym, ret))
    control.append(float(np.mean(cand)) if cand else 0.0)
    nif.append((nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan)
    n = len(sel)
    gross = float(np.mean([r for _, r in sel])) if sel else 0.0
    cur = {s for s, _ in sel}
    turn = (len(cur - prev_set) / n) if n else 0.0
    strat.append((gross, gross - turn * (BUY_BPS + SELL_BPS)))
    breadth.append(n)
    prev_set = cur

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


print(f"\n=== aligned stack: long>50 & mid>60 & short>80 & score>90 · PIT N500 · {snaps[0]}..{snaps[-1]} ===")
print(f"{'series':24} {'totRet%':>9} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
for j, lab in [(0, "stack gross"), (1, "stack net")]:
    tr, cg, sh, dd = stats([x[j] for x in strat])
    print(f"{lab:24} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
for lab, col in [("EW-all-N500 (control)", control), ("Nifty 50", nif)]:
    tr, cg, sh, dd = stats(col)
    print(f"{lab:24} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
b = np.array(breadth)
print(f"\nbreadth (held names/month): min={b.min()} median={int(np.median(b))} max={b.max()} "
      f"mean={b.mean():.0f} cash-months={int((b==0).sum())} of {months}")
print("\n[gross/net statutory cost; cash when 0 eligible; no look-ahead]")
