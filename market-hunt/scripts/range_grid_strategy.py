"""Range-grid accumulation/distribution, gated by the slow RS trend (mid vs long).

Thesis (user): drop the noisy fast TrueVX pieces; keep ONLY mean_mid vs mean_long as a
regime gate, and execute with a Renko-style range grid:
  - regime BUY  (mean_mid > mean_long): buy 1 lot every time price STEPS DOWN r% (accumulate dips);
    NEVER sell in this regime -- hold.
  - regime SELL (mean_mid < mean_long): distribute on up-breaks. Two sell modes tested:
       'pieces' : sell 1 lot per up-step
       'all'    : sell the ENTIRE inventory on the FIRST up-break after the regime flips
Long-only (never sell more than held). 1 lot = 1 share (rupee-cost-avg variant left for later).

The range grid (from the Pine "Dynamic Fib Range-Based"): a band [A*(1-r), A*(1+r)] around an
anchor A; when close breaks the band, re-anchor A := close (a step). Each step is one event.

PATH-DEPENDENT, OPEN-CAPITAL -> judged on realized+unrealized P&L, max capital deployed, max
inventory, return-on-capital, vs buy-&-hold. SINGLE-STOCK = mechanism/failure-mode study, not
edge evidence: best/median/worst performers shown. No look-ahead (regime ffilled from data<=day).
Also reports n_regime_shifts (mid/long crossings) -- the churn driver.
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
R = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0     # range width %
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


def regime_series(symbol, idx):
    irows = list(db["indicators"].find(
        {"symbol": symbol, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_mid": 1, "data.mean_long": 1}))
    if not irows:
        return None
    d = [pd.to_datetime(r["date"]).normalize() for r in irows]
    mm = pd.Series([r["data"].get("mean_mid") for r in irows], index=d).sort_index()
    ml = pd.Series([r["data"].get("mean_long") for r in irows], index=d).sort_index()
    return (mm > ml).reindex(idx, method="ffill")     # True = accumulate regime


def simulate(px, buy_regime, sell_mode):
    px = px[px.index >= SIM_FROM]
    if len(px) < 50:
        return None
    closes = px.values
    reg = buy_regime.reindex(px.index, method="ffill").fillna(False).values.astype(bool)

    anchor = closes[0]
    hi, lo = anchor * (1 + R / 100), anchor * (1 - R / 100)
    inv_shares, inv_cost, realized = 0, 0.0, 0.0
    n_buy = n_sell = 0
    buys, sells = [], []
    max_cap, max_inv = 0.0, 0
    pnl_curve = []
    n_shifts = 0

    for i, (c, is_buy) in enumerate(zip(closes, reg)):
        if i > 0 and is_buy != reg[i - 1]:
            n_shifts += 1
        event = None
        if c > hi:
            event = "up"; anchor = c; hi, lo = c * (1 + R / 100), c * (1 - R / 100)
        elif c < lo:
            event = "down"; anchor = c; hi, lo = c * (1 + R / 100), c * (1 - R / 100)

        if event == "down" and is_buy:                          # accumulate
            inv_shares += 1; inv_cost += c; n_buy += 1; buys.append(c)
        elif event == "up" and (not is_buy) and inv_shares > 0:  # distribute
            if sell_mode == "all":
                realized += inv_shares * c - inv_cost
                n_sell += inv_shares; sells.extend([c] * inv_shares)
                inv_shares, inv_cost = 0, 0.0
            else:                                                # pieces
                avg = inv_cost / inv_shares
                realized += c - avg; inv_cost -= avg; inv_shares -= 1
                n_sell += 1; sells.append(c)

        max_cap = max(max_cap, inv_cost); max_inv = max(max_inv, inv_shares)
        pnl_curve.append(realized + (inv_shares * c - inv_cost))

    last = closes[-1]
    total = realized + (inv_shares * last - inv_cost)
    roic = (total / max_cap * 100) if max_cap > 0 else 0.0
    pc = np.array(pnl_curve); peak, mdd = pc[0], 0.0
    for v in pc:
        peak = max(peak, v); mdd = min(mdd, v - peak)
    bh_ret = (last / closes[0] - 1) * 100
    bh_matched = (max_cap / closes[0]) * (last - closes[0]) if max_cap > 0 else 0.0
    return dict(n_buy=n_buy, n_sell=n_sell, end_inv=inv_shares,
                avg_buy=np.mean(buys) if buys else np.nan,
                avg_sell=np.mean(sells) if sells else np.nan,
                total=total, max_cap=max_cap, max_inv=max_inv, roic=roic, dd=mdd,
                bh_ret=bh_ret, bh_matched=bh_matched, n_shifts=n_shifts,
                buy_frac=reg.mean())


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

print(f"range grid r={R}% | regime mid>long | 1 lot=1 share | from {SIM_FROM.date()} | scanned {len(rets)}")
print(f"\n{'role':9} {'symbol':11} {'mult':>5} {'shifts':>6} {'mode':>7} {'#buy':>5} {'#sell':>6} "
      f"{'avgBuy':>8} {'avgSell':>8} {'maxCap':>9} {'totPnL':>9} {'ROIC%':>7} {'maxDD':>9} {'B&H%':>8} {'matchBH':>10}")
for role, sym in picks.items():
    px = cache[sym] if sym in cache else close_series(sym)
    reg = regime_series(sym, px.index) if px is not None else None
    if reg is None:
        print(f"{role:9} {sym:11} (no data)"); continue
    for mode in ("pieces", "all"):
        r = simulate(px, reg, mode)
        if r is None:
            continue
        tag = f"{role:9} {sym:11} {rets.get(sym, float('nan')):>5.1f} {r['n_shifts']:>6}" if mode == "pieces" \
            else f"{'':9} {'':11} {'':5} {'':6}"
        print(f"{tag} {mode:>7} {r['n_buy']:>5} {r['n_sell']:>6} {r['avg_buy']:>8.1f} {r['avg_sell']:>8.1f} "
              f"{r['max_cap']:>9.0f} {r['total']:>9.0f} {r['roic']:>7.1f} {r['dd']:>9.0f} "
              f"{r['bh_ret']:>8.1f} {r['bh_matched']:>10.0f}")
print("\n[ROIC = totalP&L/maxCapital; matchBH = same max-capital bought day-1 & held; totPnL = realized+unrealized;")
print(" 'all' = dump whole inventory on first up-break after regime flips to sell; 'pieces' = one lot per up-step]")
