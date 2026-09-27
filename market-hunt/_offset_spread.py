"""ENTRY-DAY SPREAD TEST — same strategy, only the day it trades.

Baseline is the run at
  /analytics/simulator/run?strategyId=strategy_1781939795919&...&rebalanceDate=first

`rebalance_date="first"` fires on the FIRST loaded trading session of each period.
`rebalance_offset_sessions=N` shifts every rebalance N TRADING SESSIONS later.

  monthly  --offsets 0,1,2,3,4      -> 1st..5th trading day of the month
  quarterly --offsets 0,5,10,15     -> quarter open, +1wk, +2wk, +3wk

Sessions, not calendar days: 5 sessions is one full trading week, so with a mid-week
holiday "+1 week" lands 6 calendar days out, not 7. That is the honest unit here —
the engine can only trade on a session it has prices for.

Nothing else changes: same universe, same filter, same ranking, same costs. The gap
between the runs is therefore pure ENTRY-TIMING LUCK — the part of the headline number
the calendar handed us rather than the edge. A wide spread means a single-date backtest
of this strategy is not a reliable point estimate.

Also reports the equal-weight BLEND (built from daily returns, which is what a book split
across the entry dates would actually earn) — averaging away timing noise should lift
risk-adjusted return above the mean of the parts.

usage:
  python _offset_spread.py                                    # monthly, 1st..5th day
  python _offset_spread.py --frequency quarterly --offsets 0,5,10,15
"""
import argparse
import json
from datetime import datetime

import numpy as np
import requests

BASE = "http://127.0.0.1:3001"
RF = 0.06

# Verbatim from the run URL.
COMMON = {
    "strategy_id": "strategy_1781939795919",
    "portfolio_base_value": 1000000,
    "rebalance_date": "first",
    "rebalance_type": "equal_weight",
    "universe": "NIFTY500",
    "benchmark_symbol": "Nifty 50",
    "max_holdings": 25,
    "momentum_ranking": "price_roc_22d",
    "primary_filter_take": "none",
    "primary_filter_metric": "ath",
    "primary_filter_band": 5,
    "start_date": "2020-01-01",
    "end_date": "2026-08-30",
    "point_in_time_membership": True,
    "regime_gate": False,
    "gate_symbol": "Nifty 500",
    "gate_sma": 200,
}

ap = argparse.ArgumentParser()
ap.add_argument("--frequency", default="monthly", choices=["monthly", "quarterly", "weekly"])
ap.add_argument("--offsets", default="0,1,2,3,4",
                help="comma-separated offsets in TRADING SESSIONS")
ap.add_argument("--out", default=None, help="results json path (default: by frequency)")
args = ap.parse_args()

FREQ = args.frequency
OFFSETS = [int(x) for x in args.offsets.split(",")]
OUT = args.out or f"_offset_spread_{FREQ}.json"


def label_for(off):
    """Name an offset the way a human would say it."""
    if off == 0:
        return "open"
    if FREQ == "monthly" and off < 5:
        return f"day {off + 1}"
    wk, rem = divmod(off, 5)
    if rem == 0:
        return f"+{wk}wk"
    return f"+{off}sess"


def stats(v, years):
    v = np.asarray(v, float)
    peak = np.maximum.accumulate(v)
    r = np.diff(v) / v[:-1]
    sd = r.std(ddof=1)
    sh = ((r.mean() * 252 - RF) / (sd * np.sqrt(252))) if sd > 0 else 0.0
    return ((v[-1] / v[0] - 1) * 100,
            ((v[-1] / v[0]) ** (1 / years) - 1) * 100,
            ((v - peak) / peak).min() * 100,
            sh)


runs = {}
for off in OFFSETS:
    label = label_for(off)
    print(f"running {FREQ} {label} (offset_sessions={off}) ...", flush=True)
    r = requests.post(f"{BASE}/api/simulation/run",
                      json={**COMMON, "rebalance_frequency": FREQ,
                            "rebalance_offset_sessions": off}, timeout=7200)
    if r.status_code != 200:
        print("  ERROR", r.status_code, str(r.text)[:300], flush=True)
        continue
    runs[label] = r.json()["simulation"]
    print("  ok", flush=True)

if len(runs) < 2:
    raise SystemExit("need at least two successful runs to measure a spread")

# Common calendar so the curves are directly comparable and blendable.
dates = sorted(set.intersection(*[{d["date"] for d in s["results"]} for s in runs.values()]))
cur = {l: np.array([{d["date"]: d["portfolio_value"] for d in s["results"]}[x] for x in dates])
       for l, s in runs.items()}
rets = {l: np.diff(v) / v[:-1] for l, v in cur.items()}

d0 = datetime.strptime(dates[0], "%Y-%m-%d").date()
d1 = datetime.strptime(dates[-1], "%Y-%m-%d").date()
YEARS = (d1 - d0).days / 365.25
print(f"\n{FREQ} rebalance, entry offsets {OFFSETS} sessions")
print(f"window {dates[0]} -> {dates[-1]}  ({YEARS:.2f}y, {len(dates)} sessions)")

print(f"\n{'entry':<22}{'totRet%':>10}{'CAGR%':>9}{'maxDD%':>9}{'Sharpe':>8}")
print("-" * 58)
rows = {}
for l in cur:
    rows[l] = stats(cur[l], YEARS)
    t, c, d, s = rows[l]
    print(f"{l:<22}{t:>10.2f}{c:>9.2f}{d:>9.2f}{s:>8.3f}")

print("-" * 58)
w = [1 / len(cur)] * len(cur)
blend_r = sum(wi * rets[l] for wi, l in zip(w, cur))
blend = np.concatenate([[1.0], np.cumprod(1 + blend_r)])
bt, bc, bd, bs = stats(blend, YEARS)
print(f"{'BLEND (equal 1/' + str(len(cur)) + ')':<22}{bt:>10.2f}{bc:>9.2f}{bd:>9.2f}{bs:>8.3f}")

cagrs = np.array([rows[l][1] for l in cur])
sharpes = np.array([rows[l][3] for l in cur])
dds = np.array([rows[l][2] for l in cur])
print(f"{'mean of the parts':<22}{np.mean([rows[l][0] for l in cur]):>10.2f}"
      f"{cagrs.mean():>9.2f}{dds.mean():>9.2f}{sharpes.mean():>8.3f}")

print("\nENTRY-TIMING LUCK (same strategy, only the trade date differs)")
print(f"  CAGR   best {cagrs.max():6.2f}%  worst {cagrs.min():6.2f}%  "
      f"spread {cagrs.max() - cagrs.min():5.2f}pp  sd {cagrs.std(ddof=1):5.2f}pp")
print(f"  Sharpe best {sharpes.max():6.3f}   worst {sharpes.min():6.3f}   "
      f"spread {sharpes.max() - sharpes.min():5.3f}")
print(f"  maxDD  best {dds.max():6.2f}%  worst {dds.min():6.2f}%  "
      f"spread {abs(dds.max() - dds.min()):5.2f}pp")
print(f"  blend Sharpe {bs:.3f} vs mean of parts {sharpes.mean():.3f} "
      f"({bs - sharpes.mean():+.3f})")

labels = list(cur)
print("\n  pairwise daily-return correlation")
print("        " + "".join(f"{l:>9}" for l in labels))
for a in labels:
    print(f"  {a:<6}" + "".join(
        f"{np.corrcoef(rets[a], rets[b])[0, 1]:>9.3f}" for b in labels))

json.dump({l: {"total_return_pct": rows[l][0], "cagr_pct": rows[l][1],
               "max_dd_pct": rows[l][2], "sharpe": rows[l][3]} for l in cur}
          | {"blend": {"total_return_pct": bt, "cagr_pct": bc,
                       "max_dd_pct": bd, "sharpe": bs},
             "frequency": FREQ, "offsets": OFFSETS,
             "window": {"start": dates[0], "end": dates[-1], "years": YEARS}},
          open(OUT, "w"), indent=1)
print(f"\nwrote {OUT}")
