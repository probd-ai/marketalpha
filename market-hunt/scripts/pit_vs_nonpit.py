"""Survivorship demonstration: same aligned-stack strategy, PIT-true vs PIT-false.

Matches the engine's two universe modes exactly:
  - PIT  ON : universe gated to members_as_of(date) each month (real historical constituents).
  - PIT OFF : today's current NIFTY500 snapshot applied to ALL history (the survivor set).

Four series so we can decompose the spike (universe survivorship vs strategy):
  aligned-stack PIT / aligned-stack non-PIT   (the strategy, both ways)
  EW-all PIT       / EW-all non-PIT           (the raw universe, both ways)

The question answered: PIT-off looks better NOT because those names were 'always in N500',
but because they are in N500 *today* -> using them backward = only ever holding the eventual
survivors/winners, never the names that were in the index then but later fell out/died.
Net of cost for the strategy; gross for the EW controls. PIT N500, monthly, no look-ahead.
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2019, 7, 1)
THRESH = {"mean_long": 50, "mean_mid": 60, "mean_short": 80, "truevx_score": 90}
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
COST = BUY_BPS + SELL_BPS
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
iv = load_membership(db, "NIFTY500", anchor=date.today())
current = members_as_of(iv, date.today())          # PIT-OFF universe (today's snapshot)
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})

close_at, elig_at = {}, {}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    close_at[sym] = px.reindex(snap_ts)
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


def ret_of(sym, d0, d1):
    c = close_at.get(sym)
    if c is None:
        return None
    c0, c1 = c.get(d0), c.get(d1)
    if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
        return None
    return c1 / c0 - 1


def is_elig(s, d0):
    e = elig_at.get(s)
    return e is not None and e.get(d0) == True   # noqa: E712


recs = []
prev = {"asp": set(), "asn": set()}
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    pit_members = members_as_of(iv, snaps[t])
    universe = pit_members | current
    ret = {s: r for s in universe if (r := ret_of(s, d0, d1)) is not None}

    def book(elig_universe, key):
        sel = [s for s in elig_universe if is_elig(s, d0) and s in ret]
        n = len(sel)
        g = float(np.mean([ret[s] for s in sel])) if sel else 0.0
        cur = set(sel)
        turn = (len(cur - prev[key]) / n) if n else 0.0
        prev[key] = cur
        return g - turn * COST, n

    asp, np_ = book(pit_members, "asp")          # aligned-stack PIT
    asn, nn_ = book(current, "asn")              # aligned-stack non-PIT
    ctl_pit = float(np.mean([ret[s] for s in pit_members if s in ret])) if pit_members else 0.0
    ctl_non = float(np.mean([ret[s] for s in current if s in ret])) if current else 0.0
    recs.append((snaps[t], asp, asn, ctl_pit, ctl_non, np_, nn_))

R = pd.DataFrame(recs, columns=["month", "as_pit", "as_non", "ctl_pit", "ctl_non", "nP", "nN"])
R["month"] = pd.to_datetime(R["month"])


def stats(s):
    s = np.asarray([x for x in s if np.isfinite(x)])
    if len(s) < 2:
        return (np.nan,) * 3
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12.0 / len(s)) - 1
    sh = s.mean() / s.std() * math.sqrt(12) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return cagr * 100, sh, mdd * 100


for lbl, frm in [("2020-01 -> 2026-06", "2020-01-01"), ("2023-01 -> 2026-06", "2023-01-01")]:
    w = R[R.month >= frm]
    print(f"\n=== {lbl} · {len(w)} months ===")
    print(f"{'series':32} {'avgN':>5} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
    for name, col, ncol in [("aligned-stack  PIT-ON  (real)", "as_pit", "nP"),
                            ("aligned-stack  PIT-OFF (survivor)", "as_non", "nN"),
                            ("EW-all  PIT-ON  (real universe)", "ctl_pit", None),
                            ("EW-all  PIT-OFF (survivor universe)", "ctl_non", None)]:
        cg, sh, dd = stats(w[col])
        n = f"{w[ncol].mean():.0f}" if ncol else "-"
        print(f"{name:32} {n:>5} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
    asp = stats(w["as_pit"])[0]; asn = stats(w["as_non"])[0]
    cp = stats(w["ctl_pit"])[0]; cn = stats(w["ctl_non"])[0]
    print(f"  -> survivorship spike: aligned-stack +{asn-asp:.1f} pts CAGR | EW-universe +{cn-cp:.1f} pts")
print("\n[PIT-OFF = today's current N500 applied to all dates (survivor set); PIT-ON = members_as_of each date.")
print(" aligned-stack net of cost; EW controls gross. The PIT-OFF - PIT-ON gap = survivorship bias.]")
