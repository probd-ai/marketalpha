"""Run the URL simulation on NIFTY500 (PIT, to date) across ROC-ranking variants
via the live API — highest vs lowest 22d/222d ROC, max_holdings=10.

Writes per-variant summaries to scripts/output/roc_variants_n500.json and the
daily curves to scripts/output/roc_variants_n500_curves.csv.
"""
import json
import os
import sys

import requests

BASE = {
    "strategy_id": "strategy_1782331814378",
    "portfolio_base_value": 100000,
    "rebalance_frequency": "monthly",
    "rebalance_date": "first",
    "rebalance_type": "equal_weight",
    "universe": "NIFTY500",
    "benchmark_symbol": "Nifty 500",
    "max_holdings": 10,
    "start_date": "2020-01-01",
    "end_date": "2026-07-18",
    "include_brokerage": True,
    "point_in_time_membership": True,
    "regime_gate": False,
}
VARIANTS = [
    ("hi_20d", "20_day_return"),
    ("hi_222d", "price_roc_222d"),
    ("low_22d", "price_roc_22d_low"),
    ("low_222d", "price_roc_222d_low"),
]

out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(out_dir, exist_ok=True)
summaries, curves = {}, {}

for label, method in VARIANTS:
    print(f"→ {label} ({method}) …", flush=True)
    r = requests.post("http://127.0.0.1:3001/api/simulation/run",
                      json={**BASE, "momentum_ranking": method}, timeout=3600)
    if r.status_code != 200:
        print(f"  FAILED {r.status_code}: {r.text[:300]}", flush=True)
        summaries[label] = {"error": r.text[:300], "method": method}
        continue
    sim = r.json()["simulation"]
    s = sim["summary"]
    cov = sim.get("point_in_time_coverage") or {}
    days = sim["results"]
    summaries[label] = {
        "method": method,
        "total_return": s["total_return"], "benchmark_return": s["benchmark_return"],
        "alpha": s["alpha"], "max_drawdown": s["max_drawdown"],
        "sharpe_ratio": s["sharpe_ratio"], "total_trades": s["total_trades"],
        "charge_impact": s.get("charge_impact"),
        "first_day": days[0]["date"], "last_day": days[-1]["date"],
        "final_value": days[-1]["portfolio_value"],
        "pit_missing": len(cov.get("missing", [])),
    }
    curves[label] = {d["date"]: (d["portfolio_value"], d["benchmark_value"]) for d in days}
    print(f"  ✓ return={s['total_return']}% alpha={s['alpha']}% "
          f"sharpe={s['sharpe_ratio']} maxDD={s['max_drawdown']}%", flush=True)

with open(os.path.join(out_dir, "roc_variants_n500.json"), "w") as f:
    json.dump(summaries, f, indent=1)

# merged daily curve CSV (portfolio value per variant + benchmark)
all_dates = sorted(set().union(*[set(c) for c in curves.values()])) if curves else []
with open(os.path.join(out_dir, "roc_variants_n500_curves.csv"), "w") as f:
    f.write("date," + ",".join(curves) + ",benchmark\n")
    for d in all_dates:
        row = [d] + [f"{curves[l][d][0]:.2f}" if d in curves[l] else "" for l in curves]
        bench = next((f"{curves[l][d][1]:.2f}" for l in curves if d in curves[l]), "")
        f.write(",".join(row) + f",{bench}\n")
print("done — scripts/output/roc_variants_n500.json")
