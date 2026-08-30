"""Buy when mean_short crosses UP through mean_mid while BOTH > 90; exit when
mean_short crosses below 90.

Event-driven state machine on (mean_short, mean_mid): a bullish short-over-mid crossover
in the top zone triggers a buy; falling out of the top zone (short < 90) closes it. Monthly,
hold all currently-in-position PIT-N500 members equal-weight. vs EW-all-N500 control + Nifty.
Very selective -> expect low breadth / cash months (reported prominently). No look-ahead.
"""
import os, sys, math
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2019, 1, 1)
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
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


def state_machine(ms: pd.Series, mm: pd.Series) -> pd.Series:
    """1=holding. Buy when short crosses up through mid AND both>90. Sell when short<90."""
    df = pd.concat([ms.rename("s"), mm.rename("m")], axis=1).dropna().sort_index()
    state, out = 0, {}
    ps = pm = None
    for d, row in df.iterrows():
        s, m = row["s"], row["m"]
        if ps is not None:
            if state == 0:
                if ps < pm and s >= m and s > 90 and m > 90:     # bullish short-over-mid cross in top zone
                    state = 1
            else:
                if ps >= 90 and s < 90:                          # short crossed below 90
                    state = 0
        out[d] = state
        ps, pm = s, m
    return pd.Series(out)


cal = close_series("RELIANCE")
snaps = [d.date() for d in pd.Series(cal.index).groupby([cal.index.year, cal.index.month]).min()]
snaps = [d for d in snaps if d >= SNAP_FROM]
snap_ts = [pd.Timestamp(d) for d in snaps]
iv500 = load_membership(db, "NIFTY500", anchor=date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snapshots={len(snaps)} ({snaps[0]}..{snaps[-1]})  symbols={len(syms)}")

state_at, close_at = {}, {}
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.mean_short": 1, "data.mean_mid": 1}))
    if not irows:
        continue
    idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
    ms = pd.Series({d: r["data"].get("mean_short") for d, r in zip(idx, irows)})
    mm = pd.Series({d: r["data"].get("mean_mid") for d, r in zip(idx, irows)})
    st = state_machine(ms, mm)
    if st.empty:
        continue
    state_at[sym] = st.reindex(snap_ts, method="ffill")
    close_at[sym] = px.reindex(snap_ts)

nifty = close_series("Nifty 50").reindex(snap_ts)

strat, control, nif, breadth = [], [], [], []
prev_set = set()
for t in range(len(snap_ts) - 1):
    d0, d1 = snap_ts[t], snap_ts[t + 1]
    members = members_as_of(iv500, snaps[t])
    cand, sel = [], []
    for sym in members:
        c = close_at.get(sym)
        if c is None:
            continue
        c0, c1 = c.get(d0), c.get(d1)
        if not (c0 and c1 and np.isfinite(c0) and np.isfinite(c1) and c0 > 0):
            continue
        ret = c1 / c0 - 1
        cand.append(ret)
        if state_at.get(sym) is not None and state_at[sym].get(d0) == 1:
            sel.append((sym, ret))
    control.append(float(np.mean(cand)) if cand else 0.0)
    nif.append((nifty.get(d1) / nifty.get(d0) - 1) if (nifty.get(d0) and nifty.get(d1)) else np.nan)
    n = len(sel)
    gross = float(np.mean([r for _, r in sel])) if sel else 0.0
    cur = {s for s, _ in sel}
    turn = (len(cur - prev_set) / n) if n else 0.0
    strat.append((gross, gross - turn * (BUY_BPS + SELL_BPS)))
    breadth.append(n)
    prev_set = cur

months = len(control)


def stats(rets):
    s = np.array([r for r in rets if np.isfinite(r)])
    eq = np.cumprod(1 + s)
    cagr = eq[-1] ** (12.0 / months) - 1
    sharpe = (s.mean() / s.std() * math.sqrt(12)) if s.std() > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return (eq[-1] - 1) * 100, cagr * 100, sharpe, mdd * 100


print(f"\n=== buy short>mid-cross w/ both>90, exit short<90 · PIT N500 · {snaps[0]}..{snaps[-1]} ===")
b = np.array(breadth)
print(f"breadth (held/month): min={b.min()} median={int(np.median(b))} max={b.max()} mean={b.mean():.1f} "
      f"cash-months={int((b==0).sum())} of {months}")
print(f"\n{'series':24} {'totRet%':>9} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
for j, lab in [(0, "strat gross"), (1, "strat net")]:
    tr, cg, sh, dd = stats([x[j] for x in strat])
    print(f"{lab:24} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
for lab, col in [("EW-all-N500 (control)", control), ("Nifty 50", nif)]:
    tr, cg, sh, dd = stats(col)
    print(f"{lab:24} {tr:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
print("\n[gross/net statutory cost; cash when 0 held; no look-ahead]")
