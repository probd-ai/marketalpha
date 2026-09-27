"""The proposed two-sleeve book, measured — plus the blend and the overlap.

  Sleeve A : NIFTY200          monthly    Alpha + 22d ROC, top 7
  Sleeve B : NIFTY500MICRO750  quarterly  Alpha + 22d ROC, top 20

Neither N was in the sweeps (we ran 10 and 37), so both are measured here rather than
interpolated. The 50/50 blend is built from the daily equity curves, which is what a
two-sleeve portfolio actually earns — and it is the diversification claim being tested:
a blend can post a higher Sharpe than either sleeve if the two are not tightly coupled.

Also reports how many names the two sleeves hold IN COMMON at each rebalance. Sleeve B's
universe contains sleeve A's, so overlap would quietly undo the diversification.
"""
import json

import numpy as np
import requests

BASE = "http://127.0.0.1:3001"
YEARS = 4.657
RF = 0.06
ALPHA = "strategy_1781939795919"

COMMON = {"strategy_id": ALPHA, "start_date": "2022-01-01", "end_date": "2026-08-28",
          "portfolio_base_value": 100000, "rebalance_date": "first",
          "rebalance_type": "equal_weight", "point_in_time_membership": True,
          "include_brokerage": True, "benchmark_symbol": "Nifty 50",
          "primary_filter_take": "none", "momentum_ranking": "price_roc_22d"}

SLEEVES = [
    ("A NIFTY200 monthly top-7", {"universe": "NIFTY200",
                                  "rebalance_frequency": "monthly", "max_holdings": 7}),
    ("B Total750 quarterly top-20", {"universe": "NIFTY500MICRO750",
                                     "rebalance_frequency": "quarterly", "max_holdings": 20}),
]


def stats(curve):
    v = np.array(curve, dtype=float)
    tot = (v[-1] / v[0] - 1) * 100
    cagr = ((v[-1] / v[0]) ** (1 / YEARS) - 1) * 100
    dd = ((v - np.maximum.accumulate(v)) / np.maximum.accumulate(v)).min() * 100
    r = np.diff(v) / v[:-1]
    sh = ((r.mean() * 252 - RF) / (r.std(ddof=1) * np.sqrt(252))) if r.std(ddof=1) > 0 else 0.0
    return tot, cagr, dd, sh


runs = {}
for label, extra in SLEEVES:
    print(f"running {label} ...", flush=True)
    r = requests.post(f"{BASE}/api/simulation/run", json={**COMMON, **extra}, timeout=7200)
    if r.status_code != 200:
        print("  ERROR", r.status_code, str(r.json().get("detail"))[:200], flush=True)
        raise SystemExit(1)
    sim = r.json()["simulation"]
    runs[label] = sim
    s = sim["summary"]
    held = [len(d.get("holdings") or []) for d in sim["results"]]
    print(f"  totRet {s['total_return']:.2f}%  CAGR {((1+s['total_return']/100)**(1/YEARS)-1)*100:.2f}%"
          f"  Sharpe {s['sharpe_ratio']:.3f}  DD {s['max_drawdown']:.2f}%"
          f"  avg held {np.mean(held):.1f}", flush=True)

# align the two daily curves on common dates and blend 50/50, rebalanced daily
A, B = [runs[l] for l, _ in SLEEVES]
ca = {d["date"]: d["portfolio_value"] for d in A["results"]}
cb = {d["date"]: d["portfolio_value"] for d in B["results"]}
dates = sorted(set(ca) & set(cb))
va = np.array([ca[d] for d in dates]); vb = np.array([cb[d] for d in dates])
ra = np.diff(va) / va[:-1]; rb = np.diff(vb) / vb[:-1]
rblend = 0.5 * ra + 0.5 * rb
blend = np.concatenate([[100000.0], 100000.0 * np.cumprod(1 + rblend)])

print(f"\n{'book':<32}{'totRet%':>9}{'CAGR%':>8}{'maxDD%':>9}{'Sharpe':>8}")
print("-" * 66)
for label, _ in SLEEVES:
    v = [d["portfolio_value"] for d in runs[label]["results"] if d["date"] in set(dates)]
    t, c, d, s = stats(v)
    print(f"{label:<32}{t:>9.2f}{c:>8.2f}{d:>9.2f}{s:>8.3f}")
t, c, d, s = stats(blend)
print(f"{'50/50 BLEND (daily rebal)':<32}{t:>9.2f}{c:>8.2f}{d:>9.2f}{s:>8.3f}")
print(f"\ncorrelation of daily returns A vs B: {np.corrcoef(ra, rb)[0,1]:.3f}")

# name overlap between the two books
oa = {d["date"]: {h["symbol"] for h in d.get("holdings") or []} for d in A["results"]}
ob = {d["date"]: {h["symbol"] for h in d.get("holdings") or []} for d in B["results"]}
ov = [(len(oa[d] & ob[d]), len(oa[d]), len(ob[d])) for d in dates if oa.get(d) and ob.get(d)]
if ov:
    sh_, na, nb = np.array(ov).T
    print(f"name overlap: mean {sh_.mean():.1f} shared of {na.mean():.1f}+{nb.mean():.1f} "
          f"({100*sh_.mean()/max(1e-9,na.mean()):.0f}% of sleeve A) | max {sh_.max()}")
    print(f"unique names in the combined book: {(na+nb-sh_).mean():.1f} average")
json.dump({"dates": dates, "blend": blend.tolist()}, open("_book_blend.json", "w"))
