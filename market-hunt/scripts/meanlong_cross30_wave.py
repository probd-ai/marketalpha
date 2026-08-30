"""Buy mean_long crossing UP through 30, hold until it crosses back below.

The idea under test: a stock's relative strength is a wave, so catch it coming off
the bottom rather than buying it already extended.

Careful -- a SINGLE-threshold state machine is algebraically the static level filter:
"in after an up-cross of 30, out after a down-cross of 30" means in exactly when
mean_long > 30. The only thing that separates them is initialisation. So we run:

  STATIC_30    hold every PIT member with mean_long > 30          (the level filter)
  CROSS_30     state machine, initialised OUT: a name is only ever bought after an
               OBSERVED up-cross, so permanent leaders never enter    (recovery book)
  HYST_30_20   enter on up-cross 30, exit only below 20 -- holds through dips, so
               this is the only arm that is a genuine wave, not a level
  FRESH_30_12M CROSS_30 but held only 12 months after entry           (fresh turns)
  ABSOLUTE_50  mean_long > 50, the gate from the previous experiment  (comparison)
  CONTROL_EW   every PIT member, equal weight                         (control)

If CROSS_30 and STATIC_30 converge, the state framing adds nothing and the finding
is about the LEVEL 30, not about waves.

    .venv/Scripts/python.exe scripts/meanlong_cross30_wave.py [INDEX]
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
SNAP_FROM = date(2019, 1, 1)
INDEX = sys.argv[1] if len(sys.argv) > 1 else "NIFTY500"
ENTER, EXIT_HYST = 30.0, 20.0
FRESH_MONTHS = 12

BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
ROUND_TRIP = BUY_BPS + SELL_BPS

ARMS = ["STATIC_30", "CROSS_30", "HYST_30_20", "FRESH_30_12M", "ABSOLUTE_50", "CONTROL_EW"]

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
    g = pd.Series(cal_index).groupby([cal_index.year, cal_index.month])
    days = g.min() if which == "first" else (g.max() if which == "last"
                                             else g.apply(lambda s: s.iloc[len(s) // 2]))
    return [d for d in sorted(pd.to_datetime(days).tolist()) if d.date() >= SNAP_FROM]


def state_machines(ml):
    """Daily in/out state + entry date, from the DAILY mean_long series.

    cross: enter on an observed up-cross of ENTER, exit on a down-cross of ENTER.
    hyst:  enter the same way, but only exit once the value falls under EXIT_HYST,
           so an ordinary dip does not knock you out -- this rides the wave.
    """
    v = ml.values
    cross = np.zeros(len(v), dtype=bool)
    hyst = np.zeros(len(v), dtype=bool)
    entry_idx = np.full(len(v), -1, dtype=int)
    in_c = in_h = False
    e_i = -1
    for i in range(1, len(v)):
        up = v[i - 1] <= ENTER < v[i]
        if not in_c and up:
            in_c, e_i = True, i
        elif in_c and v[i] < ENTER:
            in_c = False
        if not in_h and up:
            in_h = True
        elif in_h and v[i] < EXIT_HYST:
            in_h = False
        cross[i], hyst[i], entry_idx[i] = in_c, in_h, (e_i if in_c else -1)
    ent = pd.Series([ml.index[j] if j >= 0 else pd.NaT for j in entry_idx], index=ml.index)
    return (pd.Series(cross, index=ml.index), pd.Series(hyst, index=ml.index), ent)


def perf(curve, snaps):
    eq = np.asarray(curve, dtype=float)
    yrs = (snaps[-1] - snaps[0]).days / 365.25
    cagr = (eq[-1] / eq[0]) ** (1 / yrs) - 1
    r = np.diff(eq) / eq[:-1]
    sharpe = (r.mean() * 12) / (r.std(ddof=1) * np.sqrt(12)) if r.std(ddof=1) > 0 else float("nan")
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return cagr, sharpe, dd


# ------------------------------------------------------------------ load
cal = close_series("RELIANCE")
intervals = load_membership(db, INDEX, anchor=date.today())
ever = sorted({iv.symbol for iv in intervals})
print(f"{INDEX}: {len(ever)} ever-members   enter>{ENTER:.0f} "
      f"exit<{ENTER:.0f} (hyst exit<{EXIT_HYST:.0f})")

closes, mls, states = {}, {}, {}
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
                     for r in rows}).sort_index().astype(float).dropna()
    if ser.empty:
        continue
    closes[sym], mls[sym] = px, ser
    states[sym] = state_machines(ser)
print(f"loaded {len(closes)} symbols\n")


def run(which_day):
    snaps = month_snapshots(cal.index, which_day)
    ml_at = {s: mls[s].reindex(snaps, method="ffill") for s in closes}
    px_at = {s: closes[s].reindex(snaps, method="ffill") for s in closes}
    cr_at = {s: states[s][0].reindex(snaps).ffill().fillna(False).astype(bool) for s in closes}
    hy_at = {s: states[s][1].reindex(snaps).ffill().fillna(False).astype(bool) for s in closes}
    en_at = {s: states[s][2].reindex(snaps, method="ffill") for s in closes}

    eq = {k: [1.0] for k in ARMS}
    prev = {k: {} for k in ARMS}
    counts = {k: [] for k in ARMS}
    turns = {k: [] for k in ARMS}

    for i in range(len(snaps) - 1):
        d0 = snaps[i]
        members = members_as_of(intervals, d0.date())
        pool = [s for s in members
                if s in closes
                and not pd.isna(ml_at[s].iloc[i]) and not pd.isna(px_at[s].iloc[i])
                and not pd.isna(px_at[s].iloc[i + 1]) and px_at[s].iloc[i] > 0]
        if len(pool) < 10:
            for k in ARMS:
                eq[k].append(eq[k][-1])
            continue

        fresh = []
        for s in pool:
            if cr_at[s].iloc[i]:
                e = en_at[s].iloc[i]
                if pd.notna(e) and (d0 - e).days <= FRESH_MONTHS * 31:
                    fresh.append(s)

        sel = {
            "STATIC_30": [s for s in pool if ml_at[s].iloc[i] > ENTER],
            "CROSS_30": [s for s in pool if cr_at[s].iloc[i]],
            "HYST_30_20": [s for s in pool if hy_at[s].iloc[i]],
            "FRESH_30_12M": fresh,
            "ABSOLUTE_50": [s for s in pool if ml_at[s].iloc[i] > 50.0],
            "CONTROL_EW": pool,
        }

        for k, names in sel.items():
            if not names:
                eq[k].append(eq[k][-1])
                counts[k].append(0)
                turns[k].append(0.0)
                prev[k] = {}
                continue
            w = 1.0 / len(names)
            new_w = {s: w for s in names}
            turnover = sum(abs(new_w.get(s, 0) - prev[k].get(s, 0))
                           for s in set(new_w) | set(prev[k])) / 2
            gross = float(np.mean([px_at[s].iloc[i + 1] / px_at[s].iloc[i] - 1 for s in names]))
            eq[k].append(eq[k][-1] * (1 + gross) * (1 - turnover * ROUND_TRIP))
            counts[k].append(len(names))
            turns[k].append(turnover)
            prev[k] = new_w

    return snaps, eq, counts, turns


results = {}
for which in ("first", "mid", "last"):
    snaps, eq, counts, turns = run(which)
    results[which] = (snaps, eq, counts, turns)
    nif = close_series("Nifty 100").reindex(snaps, method="ffill")
    print(f"=== rebalance = {which.upper()}  ({snaps[0].date()}..{snaps[-1].date()}) ===")
    print(f"{'arm':<14} {'CAGR':>8} {'Sharpe':>8} {'maxDD':>8} {'avg N':>7} {'turnover':>9}")
    for k in ARMS:
        c, sh, dd = perf(eq[k], snaps)
        print(f"{k:<14} {c:>7.2%} {sh:>8.2f} {dd:>7.1%} "
              f"{np.mean(counts[k]):>7.1f} {np.mean(turns[k]):>8.1%}")
    c, sh, dd = perf(nif.values, snaps)
    print(f"{'Nifty 100':<14} {c:>7.2%} {sh:>8.2f} {dd:>7.1%}\n")

print("=== average across the three rebalance days ===")
print(f"{'arm':<14} {'CAGR':>8} {'Sharpe':>8} {'maxDD':>8} {'avg N':>7}")
for k in ARMS:
    cs, ss, ds, ns = [], [], [], []
    for which in ("first", "mid", "last"):
        sn, e, cn, _ = results[which]
        c, sh, dd = perf(e[k], sn)
        cs.append(c)
        ss.append(sh)
        ds.append(dd)
        ns.append(np.mean(cn[k]))
    print(f"{k:<14} {np.mean(cs):>7.2%} {np.mean(ss):>8.2f} {np.mean(ds):>7.1%} "
          f"{np.mean(ns):>7.1f}   (spread {max(cs) - min(cs):.2%})")

# does the state machine actually differ from the level filter?
sn, e, cn, _ = results["last"]
a = np.array(e["STATIC_30"])
b = np.array(e["CROSS_30"])
print(f"\nCROSS_30 vs STATIC_30 -- book overlap check: "
      f"avg N {np.mean(cn['CROSS_30']):.1f} vs {np.mean(cn['STATIC_30']):.1f}, "
      f"terminal wealth {b[-1]:.3f} vs {a[-1]:.3f}")
