"""Turnaround strategy on mean_short with a top-trail / bottom-stop.

Rule (user-specified):
  - BUY when mean_short crosses UP through 10 (the ONLY entry).
  - SELL when mean_short crosses DOWN through 90 (TG: rode to the top, take the gain)
    OR crosses DOWN through 10 (SL: round-tripped to the floor, cut it). First trigger wins.
  - Long-only; monthly, hold ALL currently-in-position PIT-N500 members equal-weight.

Compared to the equal-weight-all-N500 control (same universe, no signal) and Nifty 50.
Buys beaten-down names emerging from the bottom decile of RS -> expect low breadth.
No look-ahead: state at a snapshot uses mean_short only up to that date; returns forward.
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


def buy10_sell90or10_state(ms: pd.Series) -> pd.Series:
    """1 = holding, 0 = flat. Buy on cross-up through 10; sell on cross-down through
    90 (TG) or 10 (SL)."""
    state, prev, out = 0, None, {}
    for d, v in ms.items():
        if prev is not None and not math.isnan(prev) and not math.isnan(v):
            if state == 0:
                if prev < 10 <= v:                       # cross up through 10 -> buy
                    state = 1
            else:
                if (prev > 90 >= v) or (prev > 10 >= v):  # cross down 90 (TG) or 10 (SL) -> sell
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

state_at, close_at = {}, {}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_short": 1}))
    if not irows:
        continue
    ms = pd.Series({pd.to_datetime(r["date"]).normalize(): r["data"].get("mean_short") for r in irows}).sort_index().dropna()
    if ms.empty:
        continue
    state_at[sym] = buy10_sell90or10_state(ms).reindex(snap_ts, method="ffill")
    close_at[sym] = px.reindex(snap_ts)

nifty = close_series("Nifty 50").reindex(snap_ts)

strat, control, nif, breadth = [], [], [], []
prev_set = set()
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    cand = []
    for sym in members:
        c = close_at.get(sym)
        if c is None:
            continue
        c0, c1 = c.get(d0), c.get(d1)
        if c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0:
            cand.append((sym, c1 / c0 - 1))
    control.append(float(np.mean([r for _, r in cand])) if cand else 0.0)
    nif.append((nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan)
    sel = [(s, r) for s, r in cand if state_at.get(s) is not None and state_at[s].get(d0, 0) == 1]
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


print(f"\n=== buy@cross-up-10, sell@cross-down-90(TG)/10(SL) · mean_short · PIT N500 · {snaps[0]}..{snaps[-1]} ===")
print(f"{'series':24} {'totRet%':>9} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
for j, lab in [(0, "strat gross"), (1, "strat net")]:
    tr, cg, sh, dd = stats([x[j] for x in strat])
    print(f"{lab:24} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
for lab, col in [("EW-all-N500 (control)", control), ("Nifty 50", nif)]:
    tr, cg, sh, dd = stats(col)
    print(f"{lab:24} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")

b = np.array(breadth)
print(f"\nbreadth (held names/month): min={b.min()} median={int(np.median(b))} max={b.max()} "
      f"cash-months={int((b==0).sum())} of {months}")
print("\n[gross/net statutory cost; cash when 0 held; no look-ahead]")
