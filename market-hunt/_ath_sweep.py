"""ATH 20% arms of the universe sweep — the two variants missing from the first pass.

Identical setup to _universe_sweep.py (Alpha, PIT, monthly/first, equal-weight,
statutory charges, base 100000, benchmark Nifty 50, 2022-01-01 -> 2026-08-28) so the
rows drop straight into the same table.

  V4 ATH20            : ATH 20% band (all), NO rank cut  -> compare against V1
  V5 ATH20 + 22d/5%   : ATH 20% band (all), then 22-day ROC top 5% -> compare against V2
"""
import json
import time

import requests

BASE = "http://127.0.0.1:3001"
OUT = "_ath_sweep_results.json"
YEARS = 4.657

UNIVERSES = [
    ("NIFTY200", 10),
    ("NIFTYLARGEMIDCAP250", 13),
    ("NIFTYSMALLCAP250", 13),
    ("NIFTY500", 25),
    ("NIFTYMICROCAP250", 13),
    ("NIFTY500MICRO750", 37),
]

COMMON = {
    "strategy_id": "strategy_1781939795919",
    "start_date": "2022-01-01", "end_date": "2026-08-28",
    "portfolio_base_value": 100000,
    "rebalance_frequency": "monthly", "rebalance_date": "first",
    "rebalance_type": "equal_weight",
    "point_in_time_membership": True, "include_brokerage": True,
    "benchmark_symbol": "Nifty 50",
    "primary_filter_take": "all", "primary_filter_metric": "ath",
    "primary_filter_band": 20.0,
}

results = []
total = len(UNIVERSES) * 2
i = 0
for universe, top_n in UNIVERSES:
    for vlabel, extra in [
        ("V4 ATH20", {"momentum_ranking": "none", "max_holdings": None}),
        (f"V5 ATH20+22d/{top_n}", {"momentum_ranking": "price_roc_22d", "max_holdings": top_n}),
    ]:
        i += 1
        print(f"[{i}/{total}] {universe} | {vlabel}", flush=True)
        t = time.time()
        try:
            r = requests.post(f"{BASE}/api/simulation/run",
                              json={**COMMON, "universe": universe, **extra}, timeout=7200)
        except Exception as e:
            results.append({"universe": universe, "variant": vlabel,
                            "error": f"{type(e).__name__}: {e}"})
            json.dump(results, open(OUT, "w"), indent=2)
            print(f"        ERROR {type(e).__name__}", flush=True); continue
        el = time.time() - t
        if r.status_code != 200:
            results.append({"universe": universe, "variant": vlabel, "secs": round(el),
                            "error": f"HTTP {r.status_code}: {str(r.json().get('detail'))[:180]}"})
            json.dump(results, open(OUT, "w"), indent=2)
            print(f"        ERROR HTTP {r.status_code}", flush=True); continue

        sim = r.json()["simulation"]; s = sim["summary"]; days = sim["results"]
        c = [len(d.get("holdings") or []) for d in days]
        pf = s.get("primary_filter") or {}
        row = {"universe": universe, "variant": vlabel, "top_n": top_n,
               "total_return": s["total_return"],
               "cagr": round(((1 + s["total_return"] / 100) ** (1 / YEARS) - 1) * 100, 2),
               "benchmark_return": s["benchmark_return"], "alpha": s["alpha"],
               "max_drawdown": s["max_drawdown"], "sharpe": s["sharpe_ratio"],
               "avg_held": round(sum(c) / len(c), 1) if c else 0, "max_held": max(c) if c else 0,
               "cash_days": sum(1 for x in c if x == 0), "sessions": len(c),
               "pool_before": pf.get("avg_pool_before"), "pool_after": pf.get("avg_pool_after"),
               "days_emptied": pf.get("days_emptied"), "secs": round(el)}
        results.append(row); json.dump(results, open(OUT, "w"), indent=2)
        print(f"        CAGR {row['cagr']:>6.2f}%  Sharpe {row['sharpe']:>6.3f}  "
              f"DD {row['max_drawdown']:>7.2f}%  held {row['avg_held']:>5.1f}  "
              f"pool {row['pool_before']}->{row['pool_after']}  cash {row['cash_days']:>4}d  "
              f"({row['secs']}s)", flush=True)

print(f"DONE — {len(results)}/{total} cells written to {OUT}", flush=True)
