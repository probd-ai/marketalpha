"""Absolute mean_long gate vs cross-sectional percentile gate, on PIT NIFTY100.

The two arms differ in ONE thing only: how the gate decides who is "strong enough".

  ABSOLUTE    hold every PIT member with mean_long > 50
  PERCENTILE  hold the top 50% of PIT members by mean_long that day

An absolute cut on a distribution whose cross-sectional mean slides 43 -> 68 admits
a wildly different share of the universe each regime (measured: 34.9% of names in
2019, 77.1% in 2024). The percentile arm holds exactly half, always. This isolates
that drift and nothing else -- same universe, same equal weighting, same monthly
cadence, same cost model, same eligible pool.

Controls: EW over the whole eligible pool, plus the Nifty 100 and Nifty 50 indices.
Run over all three rebalance days (first/mid/last trading day of the month) because
a single cell has produced an 8.5pp illusion in this project before.

    .venv/Scripts/python.exe scripts/gate_absolute_vs_percentile.py
"""
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2019, 1, 1)          # ~1y of warm-up so mean_long (222d) exists
# The gate drift this tests is a broad-universe effect: inside NIFTY100 the members
# ARE close to the Nifty 50 benchmark, so their mean_long barely drifts and there is
# little for a percentile gate to fix. Pass an index to test where drift is large.
INDEX = sys.argv[1] if len(sys.argv) > 1 else "NIFTY100"
ABS_THRESHOLD = 50.0
PCT_KEEP = 0.50                       # top half by mean_long

# same charge schedule the other research scripts use; applied to BOTH sides here
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
ROUND_TRIP = BUY_BPS + SELL_BPS

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
PRICE_COLLS = ("prices_2015_2019", "prices_2020_2024", "prices_2025_2029")


def close_series(symbol):
    rows = []
    for c in PRICE_COLLS:
        rows += list(db[c].find({"symbol": symbol, "date": {"$gte": START}},
                                {"_id": 0, "date": 1, "close_price": 1}))
    if not rows:
        return None
    s = pd.DataFrame(rows)
    s["date"] = pd.to_datetime(s["date"]).dt.normalize()
    s = s.dropna(subset=["close_price"]).drop_duplicates("date").sort_values("date")
    return s.set_index("date")["close_price"].astype(float)


def month_snapshots(cal_index, which):
    """First / middle / last available trading day of each month."""
    g = pd.Series(cal_index).groupby([cal_index.year, cal_index.month])
    if which == "first":
        days = g.min()
    elif which == "last":
        days = g.max()
    else:
        days = g.apply(lambda s: s.iloc[len(s) // 2])
    return [d for d in sorted(pd.to_datetime(days).tolist()) if d.date() >= SNAP_FROM]


def perf(curve, snaps):
    """CAGR / Sharpe / maxDD from an equity curve sampled at `snaps`."""
    eq = np.asarray(curve, dtype=float)
    yrs = (snaps[-1] - snaps[0]).days / 365.25
    cagr = (eq[-1] / eq[0]) ** (1 / yrs) - 1
    r = np.diff(eq) / eq[:-1]
    sharpe = (r.mean() * 12) / (r.std(ddof=1) * np.sqrt(12)) if r.std(ddof=1) > 0 else float("nan")
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return cagr, sharpe, dd


# ---------------------------------------------------------------- load once
cal = close_series("RELIANCE")
intervals = load_membership(db, INDEX, anchor=date.today())
ever = sorted({iv.symbol for iv in intervals})
latest = db.indicators.find_one({"indicator_type": "truevx"}, sort=[("date", -1)])["date"]
print(f"{INDEX}: {len(ever)} ever-members  |  indicators -> {latest.date()}")

closes, mls = {}, {}
for sym in ever:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    rows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_long": 1}))
    if not rows:
        continue
    ser = pd.Series({pd.to_datetime(r["date"]).normalize(): r["data"].get("mean_long")
                     for r in rows}).sort_index().astype(float)
    closes[sym] = px
    mls[sym] = ser.dropna()
print(f"loaded prices+mean_long for {len(closes)} symbols\n")


def run(which_day):
    snaps = month_snapshots(cal.index, which_day)
    # as-of series: value known ON or BEFORE the snapshot (no look-ahead)
    ml_at = {s: mls[s].reindex(snaps, method="ffill") for s in closes}
    px_at = {s: closes[s].reindex(snaps, method="ffill") for s in closes}

    arms = {"ABSOLUTE": [1.0], "PERCENTILE": [1.0], "CONTROL_EW": [1.0]}
    prev = {k: {} for k in arms}
    counts = {k: [] for k in arms}
    turns = {k: [] for k in arms}
    breadth_log = []

    for i in range(len(snaps) - 1):
        d0 = snaps[i]
        members = members_as_of(intervals, d0.date())
        pool = [s for s in members
                if s in closes
                and not pd.isna(ml_at[s].iloc[i]) and not pd.isna(px_at[s].iloc[i])
                and not pd.isna(px_at[s].iloc[i + 1]) and px_at[s].iloc[i] > 0]
        if len(pool) < 10:
            for k in arms:
                arms[k].append(arms[k][-1])
            continue

        vals = {s: ml_at[s].iloc[i] for s in pool}
        ranked = sorted(pool, key=lambda s: vals[s], reverse=True)
        keep_n = max(1, int(round(len(pool) * PCT_KEEP)))

        sel = {
            "ABSOLUTE": [s for s in pool if vals[s] > ABS_THRESHOLD],
            "PERCENTILE": ranked[:keep_n],
            "CONTROL_EW": pool,
        }
        breadth_log.append((d0, len(pool), len(sel["ABSOLUTE"]), len(sel["PERCENTILE"])))

        for k, names in sel.items():
            if not names:                       # gate admitted nobody -> sit in cash
                arms[k].append(arms[k][-1])
                counts[k].append(0)
                turns[k].append(0.0)
                prev[k] = {}
                continue
            w = 1.0 / len(names)
            new_w = {s: w for s in names}
            turnover = sum(abs(new_w.get(s, 0) - prev[k].get(s, 0))
                           for s in set(new_w) | set(prev[k])) / 2
            gross = float(np.mean([px_at[s].iloc[i + 1] / px_at[s].iloc[i] - 1 for s in names]))
            arms[k].append(arms[k][-1] * (1 + gross) * (1 - turnover * ROUND_TRIP))
            counts[k].append(len(names))
            turns[k].append(turnover)
            prev[k] = new_w

    return snaps, arms, counts, turns, breadth_log


# ---------------------------------------------------------------- run sweep
results = {}
for which in ("first", "mid", "last"):
    snaps, arms, counts, turns, breadth = run(which)
    results[which] = (snaps, arms, counts, turns, breadth)

    nif100 = close_series("Nifty 100").reindex(snaps, method="ffill")
    nif50 = close_series("Nifty 50").reindex(snaps, method="ffill")

    print(f"=== rebalance day = {which.upper()}  ({snaps[0].date()} .. {snaps[-1].date()}, "
          f"{len(snaps)} snapshots) ===")
    print(f"{'arm':<12} {'CAGR':>8} {'Sharpe':>8} {'maxDD':>8} {'avg N':>7} {'turnover':>9}")
    for k in ("ABSOLUTE", "PERCENTILE", "CONTROL_EW"):
        c, sh, dd = perf(arms[k], snaps)
        print(f"{k:<12} {c:>7.2%} {sh:>8.2f} {dd:>7.1%} "
              f"{np.mean(counts[k]):>7.1f} {np.mean(turns[k]):>8.1%}")
    for nm, ser in (("Nifty 100", nif100), ("Nifty 50", nif50)):
        c, sh, dd = perf(ser.values, snaps)
        print(f"{nm:<12} {c:>7.2%} {sh:>8.2f} {dd:>7.1%} {'-':>7} {'-':>9}")
    print()

# ------------------------------------------------- the mechanism, once
snaps, arms, counts, turns, breadth = results["last"]
print("=== breadth: how many names each gate admits (rebalance=last) ===")
print(f"{'year':>6} {'pool':>6} {'ABSOLUTE':>18} {'PERCENTILE':>15}")
byyear = {}
for d0, npool, nabs, npct in breadth:
    byyear.setdefault(d0.year, []).append((npool, nabs, npct))
for y in sorted(byyear):
    rows = byyear[y]
    p = np.mean([r[0] for r in rows])
    a = np.mean([r[1] for r in rows])
    q = np.mean([r[2] for r in rows])
    print(f"{y:>6} {p:>6.0f} {a:>10.0f} ({a / p:>5.1%}) {q:>9.0f} ({q / p:>5.1%})")

print("\n=== average across the three rebalance days ===")
print(f"{'arm':<12} {'CAGR':>8} {'Sharpe':>8} {'maxDD':>8}")
for k in ("ABSOLUTE", "PERCENTILE", "CONTROL_EW"):
    cs, ss, ds = [], [], []
    for which in ("first", "mid", "last"):
        sn, ar, _, _, _ = results[which]
        c, sh, dd = perf(ar[k], sn)
        cs.append(c)
        ss.append(sh)
        ds.append(dd)
    print(f"{k:<12} {np.mean(cs):>7.2%} {np.mean(ss):>8.2f} {np.mean(ds):>7.1%}"
          f"   (CAGR spread {max(cs) - min(cs):.2%})")
