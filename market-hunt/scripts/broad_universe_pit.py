"""Broad-universe PIT backtest: does adding Microcap250 (catching risers EARLY, point-in-time
correct) add REAL edge over the N500-only champion?

Universe each month = members_as_of(NIFTY500, d) UNION members_as_of(NIFTYMICROCAP250, d) --
both reconstructed point-in-time, so a microcap is held only while it was actually a microcap
member (no look-ahead, no survivor cheat). Aligned-stack gate (long>50 & mid>60 & short>80 &
score>90), hold-all eligible, equal-weight, monthly, net of cost. Compared to:
  - N500-only PIT aligned-stack (the honest baseline: 26.3% / 18.7%)
  - broad / N500 EW-all controls
Microcap reconstitution covers 2022-03+, so trust 2022-06+ (earlier = frozen microcap list).
Reports per month how many eligible names are SKIPPED for missing price (delisted-microcap gap)
so the residual survivorship is bounded, not hidden.
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2021, 7, 1)
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
iv500 = load_membership(db, "NIFTY500", anchor=date.today())
ivmic = load_membership(db, "NIFTYMICROCAP250", anchor=date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snaps={len(snaps)} ({snaps[0]}..{snaps[-1]})  truevx symbols={len(syms)}")

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

nifty = close_series("Nifty 50").reindex(snap_ts)


def ret_of(s, d0, d1):
    c = close_at.get(s)
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
prev = {"b": set(), "n": set()}
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    n500 = members_as_of(iv500, snaps[t])
    micro = members_as_of(ivmic, snaps[t])
    broad = n500 | micro
    ret = {s: r for s in broad if (r := ret_of(s, d0, d1)) is not None}

    def book(universe, key):
        sel = [s for s in universe if is_elig(s, d0) and s in ret]
        n = len(sel)
        g = float(np.mean([ret[s] for s in sel])) if sel else 0.0
        cur = set(sel)
        turn = (len(cur - prev[key]) / n) if n else 0.0
        prev[key] = cur
        # how many eligible names skipped for missing price (the residual gap)
        skipped = sum(1 for s in universe if is_elig(s, d0) and s not in ret)
        return g - turn * COST, n, skipped

    b, nb, skip_b = book(broad, "b")
    n_, nn, _ = book(n500, "n")
    ctl_b = float(np.mean([ret[s] for s in broad if s in ret])) if broad else 0.0
    ctl_n = float(np.mean([ret[s] for s in n500 if s in ret])) if n500 else 0.0
    nif = (nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan
    recs.append((snaps[t], b, n_, ctl_b, ctl_n, nif, nb, nn, len(micro), skip_b))
    prev["b"], prev["n"] = {s for s in broad if is_elig(s, d0) and s in ret}, {s for s in n500 if is_elig(s, d0) and s in ret}

R = pd.DataFrame(recs, columns=["month", "broad", "n500", "ctl_b", "ctl_n", "nifty",
                                "nB", "nN", "nMicro", "skip"])
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


for lbl, frm in [("2022-06 -> 2026-06 (microcap PIT valid)", "2022-06-01"),
                 ("2023-01 -> 2026-06", "2023-01-01")]:
    w = R[R.month >= frm]
    print(f"\n=== {lbl} · {len(w)} months · net ===")
    print(f"{'strategy':34} {'avgN':>5} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
    for name, col, ncol in [("BROAD aligned-stack (N500+Micro)", "broad", "nB"),
                            ("N500-only aligned-stack (champ)", "n500", "nN"),
                            ("broad EW-all control", "ctl_b", None),
                            ("N500 EW-all control", "ctl_n", None),
                            ("Nifty 50", "nifty", None)]:
        cg, sh, dd = stats(w[col])
        n = f"{w[ncol].mean():.0f}" if ncol else "-"
        print(f"{name:34} {n:>5} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
    print(f"  avg microcap members/mo: {w['nMicro'].mean():.0f} | avg eligible names SKIPPED for missing price: {w['skip'].mean():.1f}")
print("\n[both universes point-in-time (members_as_of); aligned-stack hold-all EW, net cost;")
print(" 'skipped' = eligible-but-unpriced (delisted-microcap residual gap) -> bias bound]")
