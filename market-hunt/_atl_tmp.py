"""Targeted ATL cells on Alpha: 10% all and 20% all (band widened past the original grid).

Same setup as the main sweep: NIFTY500 PIT, monthly/first, equal-weight, statutory
charges, 2020-01-01 -> 2026-08-28, benchmark Nifty 50, momentum ranking = none.

Reports whether the band opens a real book at all (max_held / cash_days / trades)
before any return number is worth reading.
"""
import json
import time

import requests

BASE = "http://127.0.0.1:3001"
OUT = "_atl_results.json"
YEARS = 6.657

COMMON = {
    "strategy_id": "strategy_1781939795919",   # Alpha
    "universe": "NIFTY500",
    "start_date": "2020-01-01", "end_date": "2026-08-28",
    "portfolio_base_value": 100000,
    "rebalance_frequency": "monthly", "rebalance_date": "first",
    "rebalance_type": "equal_weight",
    "point_in_time_membership": True, "include_brokerage": True,
    "benchmark_symbol": "Nifty 50",
    "momentum_ranking": "none", "max_holdings": None,
}

CELLS = [
    ("ATL 10% all", {"primary_filter_take": "all", "primary_filter_metric": "atl",
                     "primary_filter_band": 10.0}),
    ("ATL 20% all", {"primary_filter_take": "all", "primary_filter_metric": "atl",
                     "primary_filter_band": 20.0}),
]

results = []
for label, pf in CELLS:
    print(f"running {label} ...", flush=True)
    t = time.time()
    r = requests.post(f"{BASE}/api/simulation/run", json={**COMMON, **pf}, timeout=5400)
    el = time.time() - t
    if r.status_code != 200:
        print(f"  ERROR HTTP {r.status_code}: {str(r.json().get('detail'))[:200]}", flush=True)
        continue
    sim = r.json()["simulation"]
    s, days = sim["summary"], sim["results"]
    counts = [len(d.get("holdings") or []) for d in days]
    pf_stat = s.get("primary_filter") or {}
    row = {
        "primary": label,
        "total_return": s["total_return"],
        "cagr": round(((1 + s["total_return"] / 100) ** (1 / YEARS) - 1) * 100, 2),
        "benchmark_return": s["benchmark_return"],
        "alpha": s["alpha"],
        "max_drawdown": s["max_drawdown"],
        "sharpe": s["sharpe_ratio"],
        "trades": s["total_trades"],
        "avg_held": round(sum(counts) / len(counts), 2),
        "max_held": max(counts) if counts else 0,
        "invested_days": sum(1 for c in counts if c > 0),
        "cash_days": sum(1 for c in counts if c == 0),
        "sessions": len(counts),
        "pool_before": pf_stat.get("avg_pool_before"),
        "pool_after": pf_stat.get("avg_pool_after"),
        "days_emptied": pf_stat.get("days_emptied"),
        "secs": round(el),
    }
    results.append(row)
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"  CAGR {row['cagr']:>6.2f}  Sharpe {row['sharpe']:>6.3f}  DD {row['max_drawdown']:>7.2f}  "
          f"pool {row['pool_before']}->{row['pool_after']}  avg held {row['avg_held']}  "
          f"max held {row['max_held']}  invested {row['invested_days']}/{row['sessions']}d  "
          f"({row['secs']}s)", flush=True)

print("DONE", flush=True)
