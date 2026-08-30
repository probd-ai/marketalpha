"""Where does the PIT-OFF excess come from? Decompose the survivor-universe book.

In PIT-OFF the strategy picks from today's N500 applied to all history. Split each month's
selected names into:
  - INCUMBENT     : was actually an index member as-of that date (legit; also in PIT-ON)
  - FUTURE-ENTRANT: NOT yet a member then, but is in N500 today (held only via look-ahead)
Hypothesis: the excess return comes from FUTURE-ENTRANTS — sub-index 'risers' that climbed
INTO N500 later, held during their pre-inclusion ascent. They are NOT new IPOs: to be selected
they must already have TrueVX indicator history (needs ~1yr of data). Confirm both.
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
WIN_FROM = pd.Timestamp(2020, 1, 1)
THRESH = {"mean_long": 50, "mean_mid": 60, "mean_short": 80, "truevx_score": 90}
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
current = members_as_of(iv, date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})

close_at, elig_at, ind_start = {}, {}, {}
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
        ind_start[sym] = min(idx)
        cond = None
        for f, thr in THRESH.items():
            c = pd.Series({d: r["data"].get(f) for d, r in zip(idx, irows)}).sort_index() > thr
            cond = c if cond is None else (cond & c)
        elig_at[sym] = cond.reindex(snap_ts, method="ffill")


# --- 1. universe split as of 2020-01 ---
pit_2020 = members_as_of(iv, date(2020, 1, 1))
entrants = current - pit_2020                       # in N500 now, not in Jan-2020
ent_with_data = {s for s in entrants if s in ind_start and ind_start[s] <= datetime(2020, 1, 1)}
ent_no_data = entrants - ent_with_data
print("=== current N500 split as of 2020-01 ===")
print(f"current members: {len(current)}")
print(f"  already members in Jan-2020 (incumbents): {len(current & pit_2020)}")
print(f"  joined N500 AFTER Jan-2020 (future-entrants): {len(entrants)}")
print(f"     of which had TrueVX data before 2020 (SELECTABLE risers): {len(ent_with_data)}")
print(f"     of which no early data (new/IPO -> NOT selectable early): {len(ent_no_data)}")


def is_elig(s, d0):
    e = elig_at.get(s)
    return e is not None and e.get(d0) == True   # noqa: E712


def ret_of(s, d0, d1):
    c = close_at.get(s)
    if c is None:
        return None
    c0, c1 = c.get(d0), c.get(d1)
    if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
        return None
    return c1 / c0 - 1


# --- 2. monthly decomposition of the PIT-OFF book ---
inc_rets, ent_rets, inc_n, ent_n = [], [], [], []
for t in range(len(snap_ts) - 1):
    if snap_ts[t] < WIN_FROM:
        continue
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    pit_t = members_as_of(iv, snaps[t])
    inc, ent = [], []
    for s in current:
        if not is_elig(s, d0):
            continue
        r = ret_of(s, d0, d1)
        if r is None:
            continue
        (inc if s in pit_t else ent).append(r)
    inc_rets.append(np.mean(inc) if inc else np.nan)
    ent_rets.append(np.mean(ent) if ent else np.nan)
    inc_n.append(len(inc)); ent_n.append(len(ent))


def stats(s):
    s = np.asarray([x for x in s if np.isfinite(x)])
    if len(s) < 2:
        return (np.nan, np.nan)
    eq = np.cumprod(1 + s)
    return eq[-1] ** (12.0 / len(s)) - 1, s.mean() / s.std() * math.sqrt(12) if s.std() > 0 else float("nan")


ic, ish = stats(inc_rets)
ec, esh = stats(ent_rets)
print(f"\n=== PIT-OFF aligned-stack book decomposed (2020-01 -> 2026-06) ===")
print(f"{'subgroup':36} {'avgN/mo':>8} {'CAGR%':>7} {'Sharpe':>7}")
print(f"{'INCUMBENT (real member then)':36} {np.mean(inc_n):>8.0f} {ic*100:>7.1f} {ish:>7.2f}")
print(f"{'FUTURE-ENTRANT (held via look-ahead)':36} {np.mean(ent_n):>8.0f} {ec*100:>7.1f} {esh:>7.2f}")
print(f"\nfuture-entrant share of book: {np.mean(ent_n)/(np.mean(inc_n)+np.mean(ent_n))*100:.0f}% of names")
print(f"entrant minus incumbent CAGR: +{(ec-ic)*100:.1f} pts  <- the survivorship excess source")
print("\n[FUTURE-ENTRANTS are selectable only because they already had indicator history -> NOT IPOs;")
print(" they are sub-index 'risers' held during their climb INTO the index = pure hindsight.]")
