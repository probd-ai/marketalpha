"""Cap the microcap SLEEVE WEIGHT without changing which stocks get picked.

Selection is untouched: Alpha filter -> rank by 20-session return -> top 20. Only the
ALLOCATION changes. If the microcap names would exceed `cap` of capital, their combined
weight is held at `cap` and the freed capital is spread across the non-microcap names
in the same book. Same stocks, different sizing -- so the strategy's behaviour stays
stable and this measures the sizing decision alone.

    microcaps      each get  cap / n_micro
    non-microcaps  each get  (1 - cap) / n_other

If a book happens to be ALL microcap the cap cannot be absorbed by anything, so the
excess sits in cash; the run reports how often that occurs.

Tier labels are POINT-IN-TIME. NIFTYMICROCAP250 reconstitution only starts 2022-03-31,
so the run starts 2022-04-01 -- before that, microcap membership is survivorship-biased
and the cap would be applied to the wrong names.

    .venv/Scripts/python.exe scripts/microcap_weight_cap.py
"""
import os
import sys
from collections import defaultdict
from datetime import date, datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

UNIVERSE = "NIFTY500MICRO750"
START = datetime(2021, 1, 1)          # warm-up for the 20-session ranker
SNAP_FROM = date(2022, 4, 1)          # microcap PIT honest from here
MAX_HOLDINGS = 20
RANK_SESSIONS = 20
THRESH = {"truevx_score": 90.0, "mean_short": 80.0, "mean_mid": 60.0, "mean_long": 50.0}
CAPS = [None, 0.60, 0.45, 0.30, 0.15]
TIERS = [("NIFTY50", "Nifty 50"), ("NIFTY100", "Nifty 100"),
         ("NIFTYMIDCAP150", "Midcap 150"), ("NIFTYSMALLCAP250", "Smallcap 250"),
         ("NIFTYMICROCAP250", "Microcap 250")]

BUY = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
PRICE_COLLS = ("prices_2020_2024", "prices_2025_2029")


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


print(f"loading {UNIVERSE} ...", flush=True)
intervals = load_membership(db, UNIVERSE, anchor=date.today())
ever = sorted({iv.symbol for iv in intervals})
tier_iv = {}
for idx, _lbl in TIERS:
    try:
        tier_iv[idx] = load_membership(db, idx, anchor=date.today())
    except ValueError:
        pass

closes, inds = {}, {}
for sym in ever:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    rows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data": 1}))
    if not rows:
        continue
    idx = [pd.to_datetime(r["date"]).normalize() for r in rows]
    inds[sym] = pd.DataFrame([{k: r["data"].get(k) for k in THRESH} for r in rows],
                             index=idx).sort_index()
    closes[sym] = px
print(f"  {len(closes)} symbols with prices + indicators", flush=True)

cal = close_series("RELIANCE")
_tier_cache = {}


def tier_of(sym, on):
    key = (sym, on)
    if key not in _tier_cache:
        lbl = "other"
        for idx, name in TIERS:
            iv = tier_iv.get(idx)
            if iv and sym in members_as_of(iv, on):
                lbl = name
                break
        _tier_cache[key] = lbl
    return _tier_cache[key]


def quarter_snaps(which):
    """First / mid trading day of each quarter — the portfolio's two sleeves."""
    g = pd.Series(cal.index).groupby([cal.index.year, cal.index.quarter])
    days = g.min() if which == "first" else g.apply(lambda s: s.iloc[len(s) // 2])
    return [d for d in sorted(pd.to_datetime(days).tolist()) if d.date() >= SNAP_FROM]


def weights_for(names, on, cap):
    """Equal weight, unless the microcap block breaches `cap` — then hold it at cap
    and spread the freed capital across the rest of the SAME book."""
    if cap is None:
        return {s: 1.0 / len(names) for s in names}, False
    micro = [s for s in names if tier_of(s, on) == "Microcap 250"]
    other = [s for s in names if s not in micro]
    if not micro or len(micro) / len(names) <= cap:
        return {s: 1.0 / len(names) for s in names}, False
    w = {s: cap / len(micro) for s in micro}
    if other:                                   # freed capital goes to the rest
        w.update({s: (1.0 - cap) / len(other) for s in other})
    return w, True                              # (no `other` -> remainder is cash)


def run_sleeve(which, cap):
    snaps = quarter_snaps(which)
    px_at = {s: closes[s].reindex(snaps, method="ffill") for s in closes}
    px_rank = {s: closes[s] for s in closes}
    ind_at = {s: inds[s].reindex(snaps, method="ffill") for s in closes}

    eq, prev = [1.0], {}
    micro_w, binds, turns = [], 0, []
    for i in range(len(snaps) - 1):
        d0 = snaps[i]
        on = d0.date()
        pool = []
        for s in members_as_of(intervals, on):
            if s not in closes:
                continue
            r = ind_at[s].iloc[i]
            if any(pd.isna(r.get(k)) or r.get(k) is None or r[k] <= v
                   for k, v in THRESH.items()):
                continue
            p0, p1 = px_at[s].iloc[i], px_at[s].iloc[i + 1]
            if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                continue
            h = px_rank[s][px_rank[s].index <= d0]
            if len(h) <= RANK_SESSIONS:
                continue
            pool.append((s, h.iloc[-1] / h.iloc[-1 - RANK_SESSIONS] - 1))
        if len(pool) < 5:
            eq.append(eq[-1])
            continue
        pool.sort(key=lambda x: -x[1])
        names = [s for s, _ in pool[:MAX_HOLDINGS]]
        w, bound = weights_for(names, on, cap)
        binds += bound
        micro_w.append(sum(v for s, v in w.items() if tier_of(s, on) == "Microcap 250"))

        turnover = sum(abs(w.get(s, 0) - prev.get(s, 0))
                       for s in set(w) | set(prev)) / 2
        turns.append(turnover)
        gross = sum(v * (px_at[s].iloc[i + 1] / px_at[s].iloc[i] - 1) for s, v in w.items())
        eq.append(eq[-1] * (1 + gross) * (1 - turnover * (BUY + SELL)))
        prev = w
    return snaps, eq, micro_w, binds, turns


def perf(eq, snaps):
    a = np.asarray(eq, float)
    yrs = (snaps[len(a) - 1] - snaps[0]).days / 365.25
    cagr = a[-1] ** (1 / yrs) - 1
    r = np.diff(a) / a[:-1]
    sh = (r.mean() * 4) / (r.std(ddof=1) * 2) if r.std(ddof=1) > 0 else float("nan")
    return cagr, sh, (a / np.maximum.accumulate(a) - 1).min()


print(f"\n{'cap':>6} {'CAGR':>7} {'Sharpe':>7} {'maxDD':>8} {'avg micro w':>12} "
      f"{'cap binds':>10} {'turnover':>9}", flush=True)
for cap in CAPS:
    curves, mws, bind, tn, sn = [], [], 0, [], None
    for which in ("first", "mid"):
        snaps, eq, mw, b, t = run_sleeve(which, cap)
        curves.append(np.asarray(eq, float))
        mws += mw
        bind += b
        tn += t
        sn = snaps
    n = min(len(c) for c in curves)
    blend = (curves[0][:n] + curves[1][:n]) / 2      # 50/50 sleeves, one book
    c, s, d = perf(blend, sn)
    lbl = "none" if cap is None else f"{cap:.0%}"
    print(f"{lbl:>6} {c:>6.2%} {s:>7.2f} {d:>7.1%} {np.mean(mws):>11.1%} "
          f"{bind/max(1,len(mws)):>9.0%} {np.mean(tn):>8.1%}", flush=True)
