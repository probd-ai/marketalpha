"""Range-grid accumulation/distribution gated by mean_long ZONE-CROSSINGS (no mid/long wobble).

Regime state machine on mean_long alone, boundaries {10,20,40,60,80,90}:
  - mean_long crosses UP   through any boundary -> state = ACCUMULATE
  - mean_long crosses DOWN through any boundary -> state = DISTRIBUTE
  - state persists between crossings (mean_long is the 222d smooth -> few, sticky flips,
    fixing the mid/long whipsaw that forced ~10-18 regime changes and capped the trenders).

Execution (Renko range grid, band re-anchors on r% break):
  - ACCUMULATE: buy 1 lot on each price DOWN-step (accumulate dips); never sell.
  - DISTRIBUTE: sell the ENTIRE inventory on the FIRST price UP-break ("all at once").

Long-only. 1 lot=1 share. Judged on realized+unrealized P&L, max capital, ROIC, vs buy-&-hold.
Single-stock = mechanism/failure-mode study. No look-ahead (mean_long ffilled from data<=day).
"""
import os, sys
from datetime import datetime, date
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SIM_FROM = pd.Timestamp(2019, 1, 1)
R = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
ZONES = [10, 20, 40, 60, 80, 90]
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


def regime_meanlong(symbol, idx):
    """state from mean_long zone crossings: True=accumulate, False=distribute."""
    irows = list(db["indicators"].find(
        {"symbol": symbol, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_long": 1}))
    if not irows:
        return None
    d = [pd.to_datetime(r["date"]).normalize() for r in irows]
    ml = pd.Series([r["data"].get("mean_long") for r in irows], index=d).sort_index().dropna()
    state, prev, out = None, None, {}
    for dt, v in ml.items():
        if prev is not None:
            for b in ZONES:
                if prev < b <= v:        # crossed up
                    state = True
                elif prev > b >= v:      # crossed down
                    state = False
        out[dt] = state
        prev = v
    s = pd.Series(out).reindex(idx, method="ffill")
    return s.map(lambda x: bool(x) if x is not None and not (isinstance(x, float) and np.isnan(x)) else False)


def simulate(px, regime):
    px = px[px.index >= SIM_FROM]
    if len(px) < 50:
        return None
    closes = px.values
    reg = regime.reindex(px.index, method="ffill").fillna(False).values.astype(bool)
    anchor = closes[0]
    hi, lo = anchor * (1 + R / 100), anchor * (1 - R / 100)
    inv_shares, inv_cost, realized = 0, 0.0, 0.0
    n_buy = n_sell = n_shifts = 0
    buys, sells = [], []
    max_cap, max_inv, pnl_curve = 0.0, 0, []
    for i, (c, is_buy) in enumerate(zip(closes, reg)):
        if i > 0 and is_buy != reg[i - 1]:
            n_shifts += 1
        event = None
        if c > hi:
            event = "up"; anchor = c; hi, lo = c * (1 + R / 100), c * (1 - R / 100)
        elif c < lo:
            event = "down"; anchor = c; hi, lo = c * (1 + R / 100), c * (1 - R / 100)
        if event == "down" and is_buy:
            inv_shares += 1; inv_cost += c; n_buy += 1; buys.append(c)
        elif event == "up" and (not is_buy) and inv_shares > 0:     # distribute ALL
            realized += inv_shares * c - inv_cost
            n_sell += inv_shares; sells.extend([c] * inv_shares)
            inv_shares, inv_cost = 0, 0.0
        max_cap = max(max_cap, inv_cost); max_inv = max(max_inv, inv_shares)
        pnl_curve.append(realized + (inv_shares * c - inv_cost))
    last = closes[-1]
    total = realized + (inv_shares * last - inv_cost)
    roic = (total / max_cap * 100) if max_cap > 0 else 0.0
    pc = np.array(pnl_curve); peak, mdd = pc[0], 0.0
    for v in pc:
        peak = max(peak, v); mdd = min(mdd, v - peak)
    bh = (last / closes[0] - 1) * 100
    bh_m = (max_cap / closes[0]) * (last - closes[0]) if max_cap > 0 else 0.0
    return dict(n_buy=n_buy, n_sell=n_sell, end_inv=inv_shares, n_shifts=n_shifts,
                avg_buy=np.mean(buys) if buys else np.nan,
                avg_sell=np.mean(sells) if sells else np.nan,
                total=total, max_cap=max_cap, max_inv=max_inv, roic=roic, dd=mdd,
                bh=bh, bh_m=bh_m, buy_frac=reg.mean())


iv500 = load_membership(db, "NIFTY500", anchor=date.today())
members = members_as_of(iv500, date(2025, 6, 1))
rets, cache = {}, {}
for sym in members:
    px = close_series(sym)
    if px is None:
        continue
    p = px[px.index >= SIM_FROM]
    if len(p) < 200:
        continue
    cache[sym] = px; rets[sym] = p.iloc[-1] / p.iloc[0]
ranked = sorted(rets, key=lambda s: rets[s])
picks = {"WORST": ranked[0], "MEDIAN": ranked[len(ranked) // 2],
         "BEST": ranked[-1], "REFERENCE": "RELIANCE"}
print(f"range grid r={R}% | regime = mean_long zone-cross {ZONES} | distribute ALL | from {SIM_FROM.date()}")
print(f"\n{'role':9} {'symbol':11} {'mult':>6} {'shifts':>6} {'buyReg%':>7} {'#buy':>5} {'#sell':>6} "
      f"{'avgBuy':>8} {'avgSell':>8} {'maxCap':>9} {'totPnL':>9} {'ROIC%':>7} {'maxDD':>9} {'B&H%':>8} {'matchBH':>10}")
for role, sym in picks.items():
    px = cache[sym] if sym in cache else close_series(sym)
    reg = regime_meanlong(sym, px.index) if px is not None else None
    if reg is None:
        print(f"{role:9} {sym:11} (no data)"); continue
    r = simulate(px, reg)
    if r is None:
        print(f"{role:9} {sym:11} (short)"); continue
    print(f"{role:9} {sym:11} {rets.get(sym, float('nan')):>6.1f} {r['n_shifts']:>6} {r['buy_frac']*100:>7.0f} "
          f"{r['n_buy']:>5} {r['n_sell']:>6} {r['avg_buy']:>8.1f} {r['avg_sell']:>8.1f} {r['max_cap']:>9.0f} "
          f"{r['total']:>9.0f} {r['roic']:>7.1f} {r['dd']:>9.0f} {r['bh']:>8.1f} {r['bh_m']:>10.0f}")
print("\n[ROIC = totalPnL/maxCapital; matchBH = same max-capital bought day-1 & held; totPnL = realized+unrealized;")
print(" regime flips only on mean_long zone crossings -> far fewer 'shifts' than mid/long wobble]")
