"""Primary-filter sweep on the Alpha strategy (aligned stack: score>90 & short>80 & mid>60 & long>50).

NIFTY500, point-in-time, monthly/first, equal-weight, statutory charges,
2020-01-01 -> 2026-08-28 (last stored bar), benchmark = Nifty 50 (cap-weighted).

Baseline: primary filter OFF + 22-day price momentum, top 30.
Grid: metric {ath, atl} x band {5, 10} x take {all, top50} x momentum {none, price_roc_22d@30}.

Writes each result to _sweep_results.json as it completes so a crash loses nothing.
"""
import json
import time

import requests

BASE = "http://127.0.0.1:3001"
OUT = "_sweep_results.json"
START, END = "2020-01-01", "2026-08-28"
YEARS = 6.657  # 2020-01-01 -> 2026-08-28
TOP_N = 30

COMMON = {
    "strategy_id": "strategy_1781939795919",   # Alpha
    "universe": "NIFTY500",
    "start_date": START,
    "end_date": END,
    "portfolio_base_value": 100000,
    "rebalance_frequency": "monthly",
    "rebalance_date": "first",
    "rebalance_type": "equal_weight",
    "point_in_time_membership": True,
    "include_brokerage": True,
    "benchmark_symbol": "Nifty 50",
}

MOMENTUM = [
    ("none", {"momentum_ranking": "none", "max_holdings": None}),
    (f"22d/{TOP_N}", {"momentum_ranking": "price_roc_22d", "max_holdings": TOP_N}),
]

PRIMARY = [("off", {"primary_filter_take": "none"})]
for metric in ("ath", "atl"):
    for band in (5.0, 10.0):
        for take in ("all", "top50"):
            PRIMARY.append((
                f"{metric.upper()} {band:g}% {take}",
                {"primary_filter_take": take, "primary_filter_metric": metric,
                 "primary_filter_band": band},
            ))


def cagr(total_return_pct):
    return ((1 + total_return_pct / 100.0) ** (1 / YEARS) - 1) * 100


def run_one(pf_label, pf, mom_label, mom):
    body = {**COMMON, **pf, **mom}
    t = time.time()
    r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=5400)
    elapsed = time.time() - t
    if r.status_code != 200:
        return {"primary": pf_label, "momentum": mom_label, "error":
                f"HTTP {r.status_code}: {str(r.json().get('detail'))[:200]}", "secs": round(elapsed)}

    sim = r.json()["simulation"]
    s, days = sim["summary"], sim["results"]
    counts = [len(d.get("holdings") or []) for d in days]
    reb = [len(d.get("holdings") or []) for d in days
           if (d.get("new_added") or d.get("exited"))]
    pf_stat = s.get("primary_filter") or {}
    return {
        "primary": pf_label,
        "momentum": mom_label,
        "total_return": s["total_return"],
        "cagr": round(cagr(s["total_return"]), 2),
        "benchmark_return": s["benchmark_return"],
        "bench_cagr": round(cagr(s["benchmark_return"]), 2),
        "alpha": s["alpha"],
        "max_drawdown": s["max_drawdown"],
        "sharpe": s["sharpe_ratio"],
        "trades": s["total_trades"],
        "avg_held": round(sum(counts) / len(counts), 1) if counts else 0,
        "avg_held_rebal": round(sum(reb) / len(reb), 1) if reb else 0,
        "max_held": max(counts) if counts else 0,
        "cash_days": sum(1 for c in counts if c == 0),
        "sessions": len(counts),
        "pf_pool_before": pf_stat.get("avg_pool_before"),
        "pf_pool_after": pf_stat.get("avg_pool_after"),
        "pf_days_emptied": pf_stat.get("days_emptied"),
        "secs": round(elapsed),
    }


results = []
total = len(PRIMARY) * len(MOMENTUM)
i = 0
for pf_label, pf in PRIMARY:
    for mom_label, mom in MOMENTUM:
        i += 1
        print(f"[{i}/{total}] primary={pf_label:<16} momentum={mom_label} ...", flush=True)
        res = run_one(pf_label, pf, mom_label, mom)
        results.append(res)
        with open(OUT, "w") as fh:
            json.dump(results, fh, indent=2)
        if "error" in res:
            print(f"        ERROR {res['error']}", flush=True)
        else:
            print(f"        CAGR {res['cagr']:>6.2f}%  Sharpe {res['sharpe']:>6.3f}  "
                  f"DD {res['max_drawdown']:>7.2f}%  avg held {res['avg_held']:>5.1f}  "
                  f"({res['secs']}s)", flush=True)

print(f"\nDONE — {len(results)} runs written to {OUT}")
