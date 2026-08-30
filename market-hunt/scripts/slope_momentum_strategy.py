"""Trade the trend's ACCELERATION (ml_slope), two ways, vs the aligned-stack champion.

Background (exp #14): ml_slope = mean_long - mean_long[3m] is persistent (3m rank-IC 0.50),
its return edge lasts ~2 quarters then dies/reverses past 9m, and a rising trend rolls over
~10-12m. So: enter on positive/rising slope, hold ~quarterly, exit on rollover.

Two builds, both equal-weight, monthly return granularity, net of statutory cost:

  ENTRY-ALPHA  -- does acceleration beat the level filter?
    * slope_q5   : top-quintile by ml_slope (slope>0), reformed QUARTERLY, held in between.
    * slope_top80: top-80 by ml_slope (slope>0), breadth-matched to the champion (~79).

  EXIT-OVERLAY -- does slope-rollover fix the champion's missing exit?
    * champion   : aligned-stack (long>50&mid>60&short>80&score>90), stateless monthly  [reproduces #9 ~23.8%]
    * champ+slope: SAME entry (aligned-stack), but exit only when ml_slope turns negative
                   (rollover) -- holds the trend through alignment wobble, exits decisively.

Benchmarks: EW-all-N500 control + Nifty 50. PIT N500, no look-ahead (signals use data<=snap).
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2017, 1, 1)
TRADE_FROM = date(2019, 1, 1)
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
snap_ts = [pd.Timestamp(d) for d in snaps]
iv500 = load_membership(db, "NIFTY500", anchor=date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snaps={len(snaps)} ({snaps[0]}..{snaps[-1]})  symbols={len(syms)}")

close_at, elig_at, slope_at = {}, {}, {}
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
    close_at[sym] = px.reindex(snap_ts)
    elig_at[sym] = cond.reindex(snap_ts, method="ffill").fillna(False)
    ml_at = fields["mean_long"].reindex(snap_ts, method="ffill")
    slope_at[sym] = ml_at - ml_at.shift(3)            # 3-snapshot (≈3m) acceleration

nifty = close_series("Nifty 50").reindex(snap_ts)
trade = [t for t in range(len(snap_ts) - 1) if snaps[t] >= TRADE_FROM]

# series accumulators
S = {k: [] for k in ("control", "nifty", "champion", "champ_slope", "slope_q5", "slope_top80")}
B = {k: [] for k in ("champion", "champ_slope", "slope_q5", "slope_top80")}
prev = {k: set() for k in B}
book = {"slope_q5": set(), "slope_top80": set()}     # quarterly-held books
ostate = {}                                          # overlay per-symbol state (0 flat / 1 held)


def ret_of(sym, d0, d1):
    c = close_at.get(sym)
    if c is None:
        return None
    c0, c1 = c.get(d0), c.get(d1)
    if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
        return None
    return c1 / c0 - 1


def finalize(key, cur, rets):
    cur = {s for s in cur if s in rets}
    n = len(cur)
    gross = float(np.mean([rets[s] for s in cur])) if cur else 0.0
    turn = (len(cur - prev[key]) / n) if n else 0.0
    S[key].append(gross - turn * COST)
    B[key].append(n)
    prev[key] = cur


for j, t in enumerate(trade):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    rets = {s: r for s in members if (r := ret_of(s, d0, d1)) is not None}
    S["control"].append(float(np.mean(list(rets.values()))) if rets else 0.0)
    S["nifty"].append((nifty.get(d1) / nifty.get(d0) - 1)
                      if (nifty.get(d0) and nifty.get(d1)) else np.nan)

    def is_elig(s):
        e = elig_at.get(s)
        return bool(e.get(d0, False)) if e is not None else False

    # --- champion: stateless aligned-stack (reproduces #9) ---
    finalize("champion", {s for s in members if is_elig(s)}, rets)

    # --- champion + slope-rollover exit overlay (state machine) ---
    held = set()
    for s in members:
        st = ostate.get(s, 0)
        sl = slope_at.get(s, pd.Series()).get(d0)
        if st == 0:
            if is_elig(s):
                st = 1
        else:
            if sl is not None and np.isfinite(sl) and sl < 0:   # rollover -> exit
                st = 0
        ostate[s] = st
        if st == 1:
            held.add(s)
    finalize("champ_slope", held, rets)

    # --- slope books: reform quarterly, hold between ---
    if j % 3 == 0:
        ranked = sorted(((s, slope_at[s].get(d0)) for s in members
                         if s in slope_at and np.isfinite(slope_at[s].get(d0, np.nan))
                         and slope_at[s].get(d0) > 0),
                        key=lambda x: x[1], reverse=True)
        nq = max(1, len(members) // 5)
        book["slope_q5"] = {s for s, _ in ranked[:nq]}
        book["slope_top80"] = {s for s, _ in ranked[:80]}
    finalize("slope_q5", set(book["slope_q5"]), rets)
    finalize("slope_top80", set(book["slope_top80"]), rets)

months = len(S["control"])


def stats(rets):
    s = np.array([r for r in rets if np.isfinite(r)])
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12.0 / months) - 1
    sharpe = (s.mean() / s.std() * math.sqrt(12)) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return (eq[-1] - 1) * 100, cagr * 100, sharpe, mdd * 100


print(f"\n=== slope-momentum vs aligned-stack champion · PIT N500 · {snaps[trade[0]]}..{snaps[trade[-1]+1]} "
      f"· {months} months (net) ===")
print(f"{'series':22} {'avgN':>5} {'totRet%':>9} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
order = [("control", "EW-all-N500 (control)"), ("nifty", "Nifty 50"),
         ("champion", "champion (aligned, #9)"), ("champ_slope", "champ + slope-exit"),
         ("slope_q5", "slope top-quintile"), ("slope_top80", "slope top-80")]
for k, lab in order:
    tr, cg, sh, dd = stats(S[k])
    n = f"{np.mean(B[k]):.0f}" if k in B else "-"
    print(f"{lab:22} {n:>5} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
print("\n[quarterly reform for slope books; champ+slope holds through alignment wobble, exits on "
      "rollover; gross-net statutory cost; no look-ahead]")
