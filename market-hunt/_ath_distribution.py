"""Static ATH-distance profile of Alpha's picks — measured, not inferred from a P&L curve.

At every monthly rebalance date we take Alpha's qualifying set (PIT), and for each name
record how far below its ALL-TIME-HIGH close it is trading, plus its forward 1-month
return. Two populations:

  A) Alpha only          — every qualifying name (no gate, no rank cut)
  B) Alpha + 22d top-25% — the top quartile of that pool by 22-day price ROC

This answers directly, without a simulation:
  1. Does Alpha already select near-ATH names? (the collinearity claim)
  2. Do near-ATH names actually earn more? (whether the ATH filter CAN help)
  3. Does momentum ranking shift the ATH profile? (are they the same signal?)

Point-in-time throughout: ATH is the running max of close over all history up to the
rebalance date, seeded from bars before the window. Forward return is the outcome, so it
looks ahead by design — that is the thing being predicted, never an input.

Note on "top 25%": taken as 25% of the QUALIFYING POOL, not of the universe. 25% of
NIFTY500 is 125 names against a pool of ~87, so a universe-based cap would never bind.
"""
import sys
from datetime import date, datetime

import numpy as np
from pymongo import MongoClient

sys.path.insert(0, r"D:\gs\workspace\tvagent\market-hunt")
from settings import MONGO_URI, DB_NAME
from membership import load_membership, members_as_of, union_over
from engine.stateless import (
    fetch_pre_window_extremes, build_extreme_series,
    build_price_series, build_indicator_series, calculate_stock_momentum_fast,
)

START, END = datetime(2022, 1, 1), datetime(2026, 8, 28)
UNIVERSES = ["NIFTY500", "NIFTYMICROCAP250"]
# Alpha: truevx_score>90 & mean_short>80 & mean_mid>60 & mean_long>50
GATE = [("truevx_score", 90.0), ("mean_short", 80.0), ("mean_mid", 60.0), ("mean_long", 50.0)]

# buckets on (1 - close/ATH) * 100  == percent below the all-time high
EDGES = [0, 2, 5, 10, 20, 35, 50, 1e9]
LABELS = ["at high (0-2%)", "2-5% below", "5-10% below", "10-20% below",
          "20-35% below", "35-50% below", ">50% below"]

db = MongoClient(MONGO_URI)[DB_NAME]


def load(universe):
    iv = load_membership(db, universe, date.today())
    syms = sorted(union_over(iv, START.date(), END.date()))

    ind = {}
    for d in db.indicators.find(
            {"indicator_type": "truevx", "symbol": {"$in": syms},
             "date": {"$gte": START, "$lte": END}}):
        ds = d["date"].strftime("%Y-%m-%d")
        ind.setdefault(ds, {})[d["symbol"]] = {
            k: (d["data"].get(k) or 0)
            for k in ("truevx_score", "mean_short", "mean_mid", "mean_long")}

    px = {}
    for c in sorted(x for x in db.list_collection_names() if x.startswith("prices_")):
        for d in db[c].find({"symbol": {"$in": syms}, "date": {"$gte": START, "$lte": END}},
                            {"_id": 0, "symbol": 1, "date": 1, "close_price": 1}):
            if d.get("close_price") is None:
                continue
            px.setdefault(d["date"].strftime("%Y-%m-%d"), {})[d["symbol"]] = {
                "close_price": float(d["close_price"])}
    return iv, syms, ind, px


def analyse(universe):
    iv, syms, ind, px = load(universe)
    dates = sorted(set(ind) & set(px))
    seed = fetch_pre_window_extremes(db, syms, START)
    ext = build_extreme_series(px, seed)
    pser = build_price_series(px)

    # first trading day of each month = the rebalance dates
    rebal, seen = [], set()
    for ds in dates:
        m = ds[:7]
        if m not in seen:
            seen.add(m); rebal.append(ds)

    rows_all, rows_mom = [], []
    for i, ds in enumerate(rebal[:-1]):
        nxt = rebal[i + 1]
        members = members_as_of(iv, datetime.strptime(ds, "%Y-%m-%d").date())
        day_i, day_p, day_e = ind.get(ds, {}), px.get(ds, {}), ext.get(ds, {})
        dobj = datetime.strptime(ds, "%Y-%m-%d")

        cand = []
        for s, v in day_i.items():
            if s not in members or s not in day_p:
                continue
            if not all(v.get(k, 0) > t for k, t in GATE):
                continue
            e = day_e.get(s)
            if not e or e[0] <= 0:
                continue
            c0 = day_p[s]["close_price"]
            below = (1 - c0 / e[0]) * 100          # % below all-time high
            fwd = px.get(nxt, {}).get(s)
            if fwd is None:
                continue                            # no forward price -> can't score it
            ret = (fwd["close_price"] / c0 - 1) * 100
            roc = calculate_stock_momentum_fast(pser.get(s), None, dobj, "price_roc_22d")
            cand.append((below, ret, roc, s))

        if not cand:
            continue
        rows_all += cand
        k = max(1, round(len(cand) * 0.25))
        rows_mom += sorted(cand, key=lambda t: (-t[2], t[3]))[:k]

    return rebal, rows_all, rows_mom


def report(name, rows):
    below = np.array([r[0] for r in rows]); ret = np.array([r[1] for r in rows])
    print(f"\n  {name}   n = {len(rows):,} stock-months")
    print(f"    distance below ATH:  mean {below.mean():6.2f}%   median {np.median(below):6.2f}%"
          f"   p10 {np.percentile(below,10):5.2f}%   p90 {np.percentile(below,90):6.2f}%")
    print(f"    forward 1M return :  mean {ret.mean():6.2f}%   median {np.median(ret):6.2f}%")
    print(f"    {'bucket':<16}{'n':>7}{'share':>8}{'fwd 1M ret':>12}{'vs pool':>9}")
    print("    " + "-" * 52)
    base = ret.mean()
    for lo, hi, lab in zip(EDGES[:-1], EDGES[1:], LABELS):
        m = (below >= lo) & (below < hi)
        if not m.any():
            print(f"    {lab:<16}{0:>7}{'0.0%':>8}{'-':>12}{'-':>9}"); continue
        print(f"    {lab:<16}{m.sum():>7}{100*m.mean():>7.1f}%{ret[m].mean():>11.2f}%"
              f"{ret[m].mean()-base:>+9.2f}")


for u in UNIVERSES:
    print("=" * 74)
    print(f"{u}   PIT   {START.date()} .. {END.date()}   Alpha gate 90/80/60/50")
    print("=" * 74)
    rebal, rows_all, rows_mom = analyse(u)
    print(f"  {len(rebal)} monthly rebalance dates")
    report("A) Alpha only (all eligible)", rows_all)
    report("B) Alpha + top 25% by 22d ROC", rows_mom)


# ── significance addendum ────────────────────────────────────────────────────
print("\n" + "=" * 74)
print("SIGNIFICANCE — bucket mean vs rest-of-pool, Welch t (monthly returns are noisy)")
print("=" * 74)
for u in UNIVERSES:
    rebal, rows_all, rows_mom = analyse(u)
    for nm, rows in (("Alpha only", rows_all), ("Alpha + top25% ROC", rows_mom)):
        below = np.array([r[0] for r in rows]); ret = np.array([r[1] for r in rows])
        print(f"\n{u} — {nm}   (n={len(rows):,}, pool mean {ret.mean():.2f}%)")
        print(f"  {'bucket':<16}{'n':>6}{'mean':>8}{'sd':>7}{'se':>7}{'t vs rest':>11}{'':>4}")
        for lo, hi, lab in zip(EDGES[:-1], EDGES[1:], LABELS):
            m = (below >= lo) & (below < hi)
            if m.sum() < 2:
                print(f"  {lab:<16}{int(m.sum()):>6}{'-':>8}"); continue
            a, b = ret[m], ret[~m]
            se = np.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
            t = (a.mean() - b.mean()) / se if se > 0 else 0.0
            star = "***" if abs(t) > 2.6 else "**" if abs(t) > 1.96 else "*" if abs(t) > 1.64 else ""
            print(f"  {lab:<16}{len(a):>6}{a.mean():>7.2f}%{a.std(ddof=1):>7.1f}{se:>7.2f}{t:>11.2f}{star:>4}")

    # monotonic trend test: correlation of (% below ATH) with forward return
    for nm, rows in (("Alpha only", rows_all), ("Alpha + top25% ROC", rows_mom)):
        below = np.array([r[0] for r in rows]); ret = np.array([r[1] for r in rows])
        r = np.corrcoef(below, ret)[0, 1]
        n = len(below)
        t = r * np.sqrt((n - 2) / max(1e-12, 1 - r * r))
        print(f"  trend  {u} {nm}: corr(%below ATH, fwd 1M ret) = {r:+.4f}   t = {t:+.2f}   n = {n:,}")
