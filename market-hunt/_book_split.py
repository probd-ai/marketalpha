"""The book as it would actually be run: each sleeve split first-of-period / mid-period.

  A1 NIFTY200         monthly   first   Alpha + 22d top-7
  A2 NIFTY200         monthly   mid     Alpha + 22d top-7
  B1 Total750         quarterly first   Alpha + 22d top-20
  B2 Total750         quarterly mid     Alpha + 22d top-20

Two things this measures that a single-date run cannot:

  1. ENTRY-TIMING LUCK. A1 vs A2 differ in nothing but the day of the month they trade.
     The gap between them is a direct read on how much of any result is the calendar.
     If it is large, single-date backtests (everything in this study) are overstated.

  2. WHETHER SPLITTING HELPS. Blending the two halves should beat their average, and may
     beat either one, purely from averaging away that timing noise.

Blends are built from daily equity curves at equal weight, which is what the live book earns.
"""
import numpy as np
import requests

BASE = "http://127.0.0.1:3001"
YEARS, RF = 4.657, 0.06
COMMON = {"strategy_id": "strategy_1781939795919", "start_date": "2022-01-01",
          "end_date": "2026-08-28", "portfolio_base_value": 100000,
          "rebalance_type": "equal_weight", "point_in_time_membership": True,
          "include_brokerage": True, "benchmark_symbol": "Nifty 50",
          "primary_filter_take": "none", "momentum_ranking": "price_roc_22d"}

SLEEVES = [
    ("A1 N200 monthly first  top-7", {"universe": "NIFTY200", "rebalance_frequency": "monthly",
                                      "rebalance_date": "first", "max_holdings": 7}),
    ("A2 N200 monthly mid    top-7", {"universe": "NIFTY200", "rebalance_frequency": "monthly",
                                      "rebalance_date": "mid", "max_holdings": 7}),
    ("B1 T750 quarterly first top-20", {"universe": "NIFTY500MICRO750",
                                        "rebalance_frequency": "quarterly",
                                        "rebalance_date": "first", "max_holdings": 20}),
    ("B2 T750 quarterly mid   top-20", {"universe": "NIFTY500MICRO750",
                                        "rebalance_frequency": "quarterly",
                                        "rebalance_date": "mid", "max_holdings": 20}),
]


def stats(v):
    v = np.asarray(v, float)
    peak = np.maximum.accumulate(v)
    r = np.diff(v) / v[:-1]
    sh = ((r.mean() * 252 - RF) / (r.std(ddof=1) * np.sqrt(252))) if r.std(ddof=1) > 0 else 0.0
    return ((v[-1] / v[0] - 1) * 100, ((v[-1] / v[0]) ** (1 / YEARS) - 1) * 100,
            ((v - peak) / peak).min() * 100, sh)


runs = {}
for label, extra in SLEEVES:
    print(f"running {label} ...", flush=True)
    r = requests.post(f"{BASE}/api/simulation/run", json={**COMMON, **extra}, timeout=7200)
    if r.status_code != 200:
        print("  ERROR", r.status_code, str(r.json().get("detail"))[:200], flush=True); continue
    runs[label] = r.json()["simulation"]
    print("  ok", flush=True)

dates = sorted(set.intersection(*[{d["date"] for d in s["results"]} for s in runs.values()]))
cur = {l: np.array([v for v in ({d["date"]: d["portfolio_value"] for d in s["results"]}[x]
                                for x in dates)]) for l, s in runs.items()}
rets = {l: np.diff(v) / v[:-1] for l, v in cur.items()}


def blend(labels, weights=None):
    w = weights or [1 / len(labels)] * len(labels)
    r = sum(wi * rets[l] for wi, l in zip(w, labels))
    return np.concatenate([[100000.0], 100000.0 * np.cumprod(1 + r)])


print(f"\n{'book':<34}{'totRet%':>9}{'CAGR%':>8}{'maxDD%':>9}{'Sharpe':>8}")
print("-" * 68)
for l in cur:
    t, c, d, s = stats(cur[l]); print(f"{l:<34}{t:>9.2f}{c:>8.2f}{d:>9.2f}{s:>8.3f}")
print("-" * 68)
LA = [l for l in cur if l.startswith("A")]
LB = [l for l in cur if l.startswith("B")]
for name, ls in (("A1+A2  large-cap sleeve", LA), ("B1+B2  total-market sleeve", LB),
                 ("FULL BOOK  50/50 of the two", None)):
    v = blend(LA + LB, [.25, .25, .25, .25]) if ls is None else blend(ls)
    t, c, d, s = stats(v); print(f"{name:<34}{t:>9.2f}{c:>8.2f}{d:>9.2f}{s:>8.3f}")

print("\nENTRY-TIMING LUCK  (same strategy, only the trade date differs)")
for a, b, nm in ((LA[0], LA[1], "NIFTY200 monthly"), (LB[0], LB[1], "Total750 quarterly")):
    ca, cb = stats(cur[a])[1], stats(cur[b])[1]
    print(f"  {nm:<22} first {ca:6.2f}%   mid {cb:6.2f}%   spread {abs(ca-cb):5.2f}pp"
          f"   corr {np.corrcoef(rets[a], rets[b])[0,1]:.3f}")

print("\nUNIQUE NAMES HELD (combined book)")
hold = {l: {d["date"]: {h["symbol"] for h in d.get("holdings") or []}
            for d in runs[l]["results"]} for l in cur}
uni = [len(set().union(*[hold[l].get(d, set()) for l in cur])) for d in dates]
ua = [len(hold[LA[0]].get(d, set()) | hold[LA[1]].get(d, set())) for d in dates]
print(f"  large-cap sleeve : mean {np.mean(ua):.1f}  min {min(ua)}  max {max(ua)}")
print(f"  whole book       : mean {np.mean(uni):.1f}  min {min(uni)}  max {max(uni)}")
