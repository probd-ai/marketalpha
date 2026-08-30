"""Proper factor test: does TrueVX velocity predict returns, net of costs?

Two complementary tests on the PIT NIFTY500 monthly panel (no look-ahead):

(1) Fama-MacBeth: each month run cross-sectional OLS
        fwd_1m ~ z(level) + z(velocity)
    over the FULL universe; average the slopes, t-stat across months. This is
    the clean "does velocity add anything CONTROLLING FOR level" test.
    beta_vel > 0 & sig -> rising predicts (continuation);
    beta_vel < 0 & sig -> fading predicts (reversal); ~0 -> velocity is noise.

(2) Quintile sort WITHIN high-RS (level>=60) — the user's "high-RS + fading"
    bet. Sort members by velocity into Q1(most fading)..Q5(most rising) each
    month, equal-weight 1m forward return. Long-only Q1 net of costs vs the
    high-RS cohort; academic Q1-Q5 long-short gross & net (flagged: single-stock
    shorting is not retail-implementable in India). Costs = real monthly
    name-turnover x statutory one-way charges.
"""
import sys, os
from collections import defaultdict
from datetime import date, datetime
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2020, 1, 1)
LEVEL_HI = 60.0
H = 21  # 1-month forward
# statutory one-way costs (NSE delivery, zero brokerage) — matches simulator
BUY_BPS = (0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001))
SELL_BPS = (0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001))
SLIP = 0.0015  # optional extra one-way slippage/impact scenario

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]

def load_close(symbol):
    rows = []
    for c in ("prices_2020_2024", "prices_2025_2029"):
        rows += list(db[c].find({"symbol": symbol, "date": {"$gte": START}},
                                {"_id": 0, "date": 1, "close_price": 1}))
    if not rows:
        return None
    s = pd.DataFrame(rows)
    s["date"] = pd.to_datetime(s["date"]).dt.normalize()
    s = s.dropna(subset=["close_price"]).drop_duplicates("date").sort_values("date")
    return s.set_index("date")["close_price"].astype(float)

cal = load_close("RELIANCE")
snap = pd.Series(cal.index).groupby([cal.index.year, cal.index.month]).min().tolist()
snap_set = set(pd.Timestamp(d) for d in snap)
iv500 = load_membership(db, "NIFTY500", anchor=date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})
print(f"snapshots={len(snap)}  indicator symbols={len(syms)}  "
      f"one-way cost: buy={BUY_BPS*1e4:.1f}bps sell={SELL_BPS*1e4:.1f}bps")

panel = []   # rows: date, symbol, level, vel, fwd
for sym in syms:
    px = load_close(sym)
    if px is None or len(px) < 80:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data.truevx_score": 1, "data.mean_short": 1}))
    if not irows:
        continue
    idf = pd.DataFrame([{"date": pd.to_datetime(r["date"]).normalize(),
                         "level": r["data"].get("truevx_score"),
                         "ms": r["data"].get("mean_short")} for r in irows])
    idf = idf.dropna(subset=["level", "ms"]).drop_duplicates("date").set_index("date")
    fwd = px.shift(-H) / px - 1.0
    for ts in idf.index:
        if ts not in snap_set or ts not in px.index:
            continue
        d = ts.date()
        if sym not in members_as_of(iv500, d):
            continue
        r = fwd.get(ts)
        if r is not None and np.isfinite(r):
            panel.append((ts, sym, float(idf.at[ts, "level"]),
                          float(idf.at[ts, "level"] - idf.at[ts, "ms"]), float(r)))

P = pd.DataFrame(panel, columns=["date", "sym", "level", "vel", "fwd"])
print(f"panel rows={len(P)}  months={P['date'].nunique()}  "
      f"avg names/month={len(P)/P['date'].nunique():.0f}")

# ---------- (1) Fama-MacBeth ------------------------------------------------
print("\n=== (1) Fama-MacBeth: fwd_1m ~ z(level) + z(velocity), full universe ===")
b_lvl, b_vel = [], []
for d, g in P.groupby("date"):
    if len(g) < 30:
        continue
    zl = (g["level"] - g["level"].mean()) / g["level"].std()
    zv = (g["vel"] - g["vel"].mean()) / g["vel"].std()
    X = np.column_stack([np.ones(len(g)), zl.to_numpy(), zv.to_numpy()])
    beta, *_ = np.linalg.lstsq(X, g["fwd"].to_numpy(), rcond=None)
    b_lvl.append(beta[1]); b_vel.append(beta[2])
b_lvl, b_vel = np.array(b_lvl), np.array(b_vel)
def tstat(a): return a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))
print(f"  level    : mean slope = {b_lvl.mean()*100:+.3f}% per +1SD  "
      f"t = {tstat(b_lvl):+.2f}  ({'SIG' if abs(tstat(b_lvl))>2 else 'ns'})")
print(f"  velocity : mean slope = {b_vel.mean()*100:+.3f}% per +1SD  "
      f"t = {tstat(b_vel):+.2f}  ({'SIG' if abs(tstat(b_vel))>2 else 'ns'})")
print("  (slope = extra monthly return per +1 standard deviation of the factor)")

# ---------- (2) velocity quintiles within high-RS ---------------------------
print(f"\n=== (2) velocity quintiles within high-RS (level>={LEVEL_HI:.0f}) ===")
HI = P[P["level"] >= LEVEL_HI].copy()
months = sorted(HI["date"].unique())
qmembers = {q: {} for q in range(5)}   # quintile -> {date: set(syms)}
qret = {q: {} for q in range(5)}       # quintile -> {date: mean fwd}
cohort = {}
for d, g in HI.groupby("date"):
    if len(g) < 25:
        continue
    g = g.sort_values("vel")
    g["q"] = pd.qcut(g["vel"].rank(method="first"), 5, labels=False)
    cohort[d] = g["fwd"].mean()
    for q in range(5):
        sub = g[g["q"] == q]
        qret[q][d] = sub["fwd"].mean()
        qmembers[q][d] = set(sub["sym"])

def turnover_series(memb):
    ds = sorted(memb.keys()); out = {ds[0]: 1.0}
    for prev, cur in zip(ds, ds[1:]):
        a, b = memb[prev], memb[cur]
        out[cur] = (len(b - a) / len(b)) if b else 0.0
    return out

print(f"  {'quintile':22} {'gross%/mo':>10} {'turnover':>9} {'net%/mo':>9} {'netCAGR%':>9}")
labels = ["Q1 most-fading", "Q2", "Q3 mid", "Q4", "Q5 most-rising"]
for q in range(5):
    ds = sorted(qret[q].keys())
    gross = np.array([qret[q][d] for d in ds])
    tov = turnover_series(qmembers[q])
    cost = np.array([tov[d] * (BUY_BPS + SELL_BPS) for d in ds])  # replace = sell+buy
    net = gross - cost
    cagr = (np.prod(1 + net) ** (12 / len(net)) - 1) * 100
    print(f"  {labels[q]:22} {gross.mean()*100:>10.2f} {tov and np.mean(list(tov.values())):>9.2f} "
          f"{net.mean()*100:>9.2f} {cagr:>9.1f}")

# long-only Q1 vs cohort; academic L/S Q1-Q5
common = [d for d in months if d in qret[0] and d in qret[4] and d in cohort]
q1 = np.array([qret[0][d] for d in common])
q5 = np.array([qret[4][d] for d in common])
coh = np.array([cohort[d] for d in common])
t1 = turnover_series({d: qmembers[0][d] for d in common})
t5 = turnover_series({d: qmembers[4][d] for d in common})
c1 = np.array([t1[d] * (BUY_BPS + SELL_BPS) for d in common])
c5 = np.array([t5[d] * (BUY_BPS + SELL_BPS) for d in common])

print(f"\n  Q1(fading) - cohort avg: gross {(q1-coh).mean()*100:+.2f}%/mo  "
      f"net {((q1-c1)-coh).mean()*100:+.2f}%/mo  t(net)={tstat((q1-c1)-coh):+.2f}")
ls_g = q1 - q5
ls_n = (q1 - c1) - (q5 - c5)
print(f"  L/S Q1-Q5 (fading-rising): gross {ls_g.mean()*100:+.2f}%/mo t={tstat(ls_g):+.2f}  |  "
      f"net {ls_n.mean()*100:+.2f}%/mo t={tstat(ls_n):+.2f}")
# higher-cost scenario (+15bps/side slippage)
c1s = np.array([t1[d]*(BUY_BPS+SELL_BPS+2*SLIP) for d in common])
print(f"  Q1(fading) - cohort, +15bps/side slippage: net {((q1-c1s)-coh).mean()*100:+.2f}%/mo "
      f"t={tstat((q1-c1s)-coh):+.2f}")
print("\n[net = gross - (name turnover x statutory one-way charges); single-stock "
      "shorting not retail-implementable in India -> L/S is academic only]")
