"""Walk-forward the aligned-stack champion (the threshold overfit test).

The champion is parameter-free EXCEPT its four thresholds (long>50 & mid>60 & short>80 &
score>90). A real walk-forward tests whether those exact cuts are robust or fitted. Scheme:
rolling 24-month train / 12-month test folds; in each train window pick the threshold config
with the best NET Sharpe from a grid around the champion; apply it out-of-sample to the next
12 months; stitch the OOS test segments. Compare three OOS curves over the same months:

  TUNED-OOS   : each fold trades the config selected on its own train window (honest WF)
  FIXED champ : the fixed 50/60/80/90 champion, applied OOS continuously
  CONTROL     : EW-all-N500 (no filter)

Reads:
  - FIXED beats CONTROL out-of-sample? -> the champion's edge is genuinely OOS, not in-sample.
  - TUNED beats / loses to FIXED?      -> does picking thresholds on past data help, or overfit?
  - Selected config jumping fold-to-fold = instability (thresholds are noise, not signal).

PIT N500, monthly, net of statutory cost, no look-ahead (train window strictly precedes test).
"""
import os, sys, math
from datetime import date, datetime
from itertools import product
import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

START = datetime(2018, 1, 1)
SNAP_FROM = date(2019, 1, 1)
FIELDS = ["mean_long", "mean_mid", "mean_short", "truevx_score"]
CHAMP = (50, 60, 80, 90)
GRID = {"mean_long": [40, 50, 60], "mean_mid": [50, 60, 70],
        "mean_short": [70, 80, 85], "truevx_score": [85, 90, 95]}
TRAIN, TEST = 24, 12
MIN_BREADTH = 10
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
snaps = [d for d in snaps if d >= SNAP_FROM]
snap_ts = [pd.Timestamp(d) for d in snaps]
T = len(snaps) - 1                       # number of return periods (months)
iv500 = load_membership(db, "NIFTY500", anchor=date.today())
syms = db["indicators"].distinct("symbol", {"indicator_type": "truevx"})

# ---- build (T x nsym) matrices ----
sym_list, cols_ret, cols_f = [], [], {f: [] for f in FIELDS}
member_cols = []
for sym in syms:
    px = close_series(sym)
    if px is None or len(px) < 60:
        continue
    irows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, **{f"data.{f}": 1 for f in FIELDS}}))
    if not irows:
        continue
    idx = [pd.to_datetime(r["date"]).normalize() for r in irows]
    fseries = {f: pd.Series({d: r["data"].get(f) for d, r in zip(idx, irows)}).sort_index()
               .reindex(snap_ts, method="ffill") for f in FIELDS}
    c = px.reindex(snap_ts)
    ret = (c.shift(-1) / c - 1).values[:T]           # period t return d0->d1
    sym_list.append(sym)
    cols_ret.append(ret)
    for f in FIELDS:
        cols_f[f].append(fseries[f].values[:T])

RET = np.array(cols_ret).T                            # T x nsym
F = {f: np.array(cols_f[f]).T for f in FIELDS}        # each T x nsym
nsym = len(sym_list)
sidx = {s: i for i, s in enumerate(sym_list)}
MEMBER = np.zeros((T, nsym), dtype=bool)
for t in range(T):
    for s in members_as_of(iv500, snaps[t]):
        if s in sidx:
            MEMBER[t, sidx[s]] = True
VALID = np.isfinite(RET) & MEMBER
print(f"months={T} ({snaps[0]}..{snaps[T]})  syms={nsym}  grid={np.prod([len(v) for v in GRID.values()])} configs")


def elig_matrix(cfg):
    a, b, c, d = cfg
    return (F["mean_long"] > a) & (F["mean_mid"] > b) & (F["mean_short"] > c) & \
           (F["truevx_score"] > d) & VALID


CONFIGS = list(product(*[GRID[f] for f in FIELDS]))
E = {cfg: elig_matrix(cfg) for cfg in CONFIGS}
E_CTRL = VALID


def net_series(Emat, idx):
    """net monthly returns over ordered month indices (prev=previous in list; first prev=empty)."""
    out, prev = [], np.zeros(nsym, dtype=bool)
    for t in idx:
        cur = Emat[t]
        n = cur.sum()
        if n == 0:
            out.append(0.0); prev = cur; continue
        gross = np.nanmean(RET[t][cur])
        turn = (cur & ~prev).sum() / n
        out.append(gross - turn * COST)
        prev = cur
    return np.array(out)


def sharpe(r):
    r = r[np.isfinite(r)]
    return (r.mean() / r.std() * math.sqrt(12)) if len(r) > 1 and r.std() > 0 else -9.9


def med_breadth(Emat, idx):
    return np.median([Emat[t].sum() for t in idx])


# ---- rolling folds ----
folds = []
start = TRAIN
while start < T:
    tr = list(range(start - TRAIN, start))
    te = list(range(start, min(start + TEST, T)))
    if te:
        folds.append((tr, te))
    start += TEST

tuned_oos, sel_cfgs, fold_rows = [], [], []
for tr, te in folds:
    best, bestsh = None, -1e9
    for cfg in CONFIGS:
        if med_breadth(E[cfg], tr) < MIN_BREADTH:
            continue
        sh = sharpe(net_series(E[cfg], tr))
        if sh > bestsh:
            bestsh, best = sh, cfg
    sel_cfgs.append(best)
    r_tuned = net_series(E[best], te)
    r_fixed = net_series(E[CHAMP], te)
    r_ctrl = net_series(E_CTRL, te)
    tuned_oos.append(r_tuned)
    fold_rows.append((snaps[te[0]], snaps[te[-1]], best,
                      sharpe(r_tuned), sharpe(r_fixed), sharpe(r_ctrl),
                      (np.prod(1 + r_tuned) - 1) * 100, (np.prod(1 + r_fixed) - 1) * 100,
                      (np.prod(1 + r_ctrl) - 1) * 100))

oos_idx = list(range(folds[0][1][0], T))               # continuous OOS span
fixed = net_series(E[CHAMP], oos_idx)
ctrl = net_series(E_CTRL, oos_idx)
tuned = np.concatenate(tuned_oos)
mo = len(oos_idx)


def stats(r):
    r = np.array(r); eq = np.cumprod(1 + r)
    cagr = eq[-1] ** (12.0 / len(r)) - 1
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    return (eq[-1] - 1) * 100, cagr * 100, sharpe(r), mdd * 100


print(f"\n=== per-fold (train {TRAIN}m -> test {TEST}m) ===")
print(f"{'test window':23} {'selected cfg':>18} {'tunSh':>6} {'fixSh':>6} {'ctlSh':>6} "
      f"{'tun%':>6} {'fix%':>6} {'ctl%':>6}")
for ts0, ts1, cfg, tsh, fsh, csh, tr_, fr_, cr_ in fold_rows:
    print(f"{str(ts0)+'..'+str(ts1):23} {str(cfg):>18} {tsh:>6.2f} {fsh:>6.2f} {csh:>6.2f} "
          f"{tr_:>6.1f} {fr_:>6.1f} {cr_:>6.1f}")

print(f"\n=== stitched OUT-OF-SAMPLE ({snaps[oos_idx[0]]}..{snaps[T]}, {mo} months, net) ===")
print(f"{'curve':28} {'totRet%':>9} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8}")
for lab, r in [("FIXED champion (50/60/80/90)", fixed), ("TUNED (WF threshold select)", tuned),
               ("CONTROL (EW-all-N500)", ctrl)]:
    tr_, cg, sh, dd = stats(r)
    print(f"{lab:28} {tr_:>9.1f} {cg:>7.1f} {sh:>7.2f} {dd:>8.1f}")
uniq = len(set(sel_cfgs))
print(f"\nselected configs across {len(folds)} folds: {uniq} distinct "
      f"{'(stable)' if uniq <= 2 else '(UNSTABLE -> thresholds are noise)'}")
print("[walk-forward: train window strictly precedes test; no look-ahead]")
