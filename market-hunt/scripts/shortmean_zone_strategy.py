"""Trend-state strategy on the smoothed RS score (mean_short).

Rule (user-specified):
  - A stock is LONG once mean_short crosses UP through any of {20,40,60,80,90};
    it stays long until mean_short crosses DOWN through any of them.
  - Each month (first trading day), hold ALL currently-long PIT-NIFTY500 members,
    equal-weight (no top-K). Return = snapshot-to-snapshot (true monthly hold).
  - If zero eligible in a month -> sit in cash (0% that month) — the built-in
    defensive feature.

Reports strategy gross & net (statutory NSE cost) vs Nifty 50, plus the breadth
timeline (how many names are 'rising'/eligible each month). No look-ahead: state at
a snapshot uses only mean_short up to that date; returns are strictly forward.
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
SNAP_FROM = date(2018, 7, 1)          # 6mo warmup so an initial cross can register
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


def trend_state(ms: pd.Series) -> pd.Series:
    """1 = long (last boundary cross was up), 0 = out. Starts out until first up-cross."""
    state, prev, out = 0, None, {}
    for d, v in ms.items():
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

# per symbol: state + close sampled at snapshot dates
state_at = {}   # sym -> {snap_ts: 0/1}
close_at = {}   # sym -> {snap_ts: close}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_short": 1}))
    if not irows:
        continue
    ms = pd.Series({pd.to_datetime(r["date"]).normalize(): r["data"].get("mean_short") for r in irows}).sort_index()
    ms = ms.dropna()
    if ms.empty:
        continue
    st = trend_state(ms)
    st_snap = st.reindex(snap_ts, method="ffill")     # state as-of each snapshot
    px_snap = px.reindex(snap_ts)                      # exact close on snapshot day
    state_at[sym] = st_snap
    close_at[sym] = px_snap

nifty = close_series("Nifty 50").reindex(snap_ts)

# monthly snapshot-to-snapshot backtest
rows, breadth = [], []
prev_eligible = set()
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    elig, rets = [], []
    for sym in members:
        if sym not in state_at:
            continue
        if state_at[sym].get(d0, 0) != 1:
            continue
        c0, c1 = close_at[sym].get(d0), close_at[sym].get(d1)
        if c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0:
            elig.append(sym)
            rets.append(c1 / c0 - 1)
    n = len(elig)
    gross = float(np.mean(rets)) if rets else 0.0
    cur = set(elig)
    turn = (len(cur - prev_eligible) / n) if n else 0.0      # one-way fraction replaced
    cost = turn * (BUY_BPS + SELL_BPS)
    net = gross - cost
    nret = (nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan
    rows.append((snaps[t], n, gross, net, nret))
    breadth.append(n)
    prev_eligible = cur

R = pd.DataFrame(rows, columns=["month", "n_elig", "gross", "net", "nifty"])
months = len(R)


def stats(series):
    s = series.dropna().to_numpy()
    eq = np.cumprod(1 + s)
    yrs = months / 12.0
    cagr = eq[-1] ** (1 / yrs) - 1
    sharpe = (s.mean() / s.std() * math.sqrt(12)) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return eq[-1] - 1, cagr, sharpe, mdd


print(f"\n=== trend-state on mean_short, hold ALL eligible, monthly, PIT N500 ===")
print(f"{'series':12} {'totRet%':>9} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
for label, col in [("strat gross", "gross"), ("strat net", "net"), ("Nifty 50", "nifty")]:
    tr, cg, sh, dd = stats(R[col])
    print(f"{label:12} {tr*100:>9.1f} {cg*100:>7.1f} {sh:>7.2f} {dd*100:>8.1f}")

print(f"\n=== breadth (eligible 'rising' count / month) ===")
b = np.array(breadth)
print(f"  min={b.min()}  median={int(np.median(b))}  max={b.max()}  "
      f"months fully in cash (0 eligible)={int((b == 0).sum())}")
print("  yearly avg eligible:")
R["yr"] = R["month"].apply(lambda d: d.year)
for yr, g in R.groupby("yr"):
    print(f"    {yr}: avg_elig={g['n_elig'].mean():5.0f}  strat_net={(np.prod(1+g['net'])-1)*100:6.1f}%  "
          f"nifty={(np.prod(1+g['nifty'].dropna())-1)*100:6.1f}%")
print("\n[gross/net of statutory cost; cash when 0 eligible; no look-ahead]")
