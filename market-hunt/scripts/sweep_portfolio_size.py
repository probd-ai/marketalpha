"""In-sample portfolio-size sweep on point-in-time NIFTY500.

For each max_holdings, run the PIT sim and report compound-growth (CAGR) +
risk (Sharpe, max drawdown). CAGR is the geometric-growth rate — the
Kelly-relevant 'maximize terminal wealth' metric. IN-SAMPLE: the best N here is
descriptive, NOT a forward recommendation (needs walk-forward validation).
"""
import os
import sys
from datetime import datetime

import requests

API = "http://localhost:3011/api/simulation/run"
SIZES = [5, 10, 15, 20, 25, 30, 40, 50]
BASE = dict(
    strategy_id="strategy_1781939795919", portfolio_base_value=100000,
    rebalance_frequency="monthly", rebalance_date="first", rebalance_type="equal_weight",
    universe="NIFTY500", benchmark_symbol="Nifty 50",
    momentum_ranking="20_day_return", start_date="2020-01-01", end_date="2026-06-30",
    point_in_time_membership=True,
)

print(f"{'N':>3} {'total_ret%':>11} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8} {'alpha':>8}")
rows = []
for n in SIZES:
    sim = requests.post(API, json={**BASE, "max_holdings": n}, timeout=900).json()["simulation"]
    s, res = sim["summary"], sim["results"]
    d0 = datetime.strptime(res[0]["date"], "%Y-%m-%d")
    d1 = datetime.strptime(res[-1]["date"], "%Y-%m-%d")
    yrs = (d1 - d0).days / 365.25
    tr = s["total_return"]
    cagr = ((1 + tr / 100) ** (1 / yrs) - 1) * 100
    rows.append((n, tr, cagr, s["sharpe_ratio"], s["max_drawdown"], s["alpha"]))
    print(f"{n:>3} {tr:>11.1f} {cagr:>7.1f} {s['sharpe_ratio']:>7.2f} {s['max_drawdown']:>8.1f} {s['alpha']:>8.1f}",
          flush=True)

best_cagr = max(rows, key=lambda r: r[2])
best_sharpe = max(rows, key=lambda r: r[3])
print(f"\nmax CAGR:   N={best_cagr[0]} ({best_cagr[2]:.1f}% CAGR, Sharpe {best_cagr[3]:.2f}, maxDD {best_cagr[4]:.1f}%)")
print(f"max Sharpe: N={best_sharpe[0]} ({best_sharpe[3]:.2f} Sharpe, {best_sharpe[2]:.1f}% CAGR, maxDD {best_sharpe[4]:.1f}%)")
print("\n[IN-SAMPLE — descriptive only; validate with walk-forward before trusting any N]")
