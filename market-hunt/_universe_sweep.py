"""Universe x variant sweep on the Alpha strategy — settle where the edge actually lives.

6 universes x 3 variants = 18 runs. Everything except universe and variant is held fixed:
Alpha (score>90 & short>80 & mid>60 & long>50), PIT, monthly/first, equal-weight,
statutory charges, base 100000, benchmark Nifty 50, 2022-01-01 -> 2026-08-28.

Variants
  V1 alpha-only      : no primary filter, no rank cut  (hold every eligible name)
  V2 +mom            : no primary filter, 22-day price ROC, top 5% of the universe
  V3 ATL20 + mom     : ATL 20% band (all), then 22-day ROC, top 5% of the universe

Top-N is 5% of each universe's member count, rounded half-up:
  N200 10 | LargeMid250 13 | Smallcap250 13 | N500 25 | Microcap250 13 | N500Micro750 37

Writes _universe_sweep_results.json after every cell so nothing is lost on a crash.
"""
import json
import time

import requests

BASE = "http://127.0.0.1:3001"
OUT = "_universe_sweep_results.json"
START, END = "2022-01-01", "2026-08-28"
YEARS = 4.657  # 2022-01-01 -> 2026-08-28

UNIVERSES = [
    ("NIFTY200", 10),
    ("NIFTYLARGEMIDCAP250", 13),
    ("NIFTYSMALLCAP250", 13),
    ("NIFTY500", 25),
    ("NIFTYMICROCAP250", 13),
    ("NIFTY500MICRO750", 37),
]

COMMON = {
    "strategy_id": "strategy_1781939795919",   # Alpha
    "start_date": START, "end_date": END,
    "portfolio_base_value": 100000,
    "rebalance_frequency": "monthly", "rebalance_date": "first",
    "rebalance_type": "equal_weight",
    "point_in_time_membership": True, "include_brokerage": True,
    "benchmark_symbol": "Nifty 50",
}


def variants(top_n):
    return [
        ("V1 alpha-only", {"momentum_ranking": "none", "max_holdings": None,
                           "primary_filter_take": "none"}),
        (f"V2 +22d/{top_n}", {"momentum_ranking": "price_roc_22d", "max_holdings": top_n,
                              "primary_filter_take": "none"}),
        (f"V3 ATL20+22d/{top_n}", {"momentum_ranking": "price_roc_22d", "max_holdings": top_n,
                                   "primary_filter_take": "all",
                                   "primary_filter_metric": "atl",
                                   "primary_filter_band": 20.0}),
    ]


def cagr(pct):
    return ((1 + pct / 100.0) ** (1 / YEARS) - 1) * 100


results = []
total = len(UNIVERSES) * 3
i = 0
for universe, top_n in UNIVERSES:
    for vlabel, extra in variants(top_n):
        i += 1
        print(f"[{i}/{total}] {universe} | {vlabel}", flush=True)
        body = {**COMMON, "universe": universe, **extra}
        t = time.time()
        try:
            r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=7200)
        except Exception as e:
            row = {"universe": universe, "variant": vlabel, "error": f"{type(e).__name__}: {e}"}
            results.append(row); json.dump(results, open(OUT, "w"), indent=2)
            print(f"        ERROR {row['error'][:120]}", flush=True); continue
        el = time.time() - t
        if r.status_code != 200:
            row = {"universe": universe, "variant": vlabel, "secs": round(el),
                   "error": f"HTTP {r.status_code}: {str(r.json().get('detail'))[:180]}"}
            results.append(row); json.dump(results, open(OUT, "w"), indent=2)
            print(f"        ERROR {row['error'][:140]}", flush=True); continue

        sim = r.json()["simulation"]
        s, days = sim["summary"], sim["results"]
        counts = [len(d.get("holdings") or []) for d in days]
        pf = sim["summary"].get("primary_filter") or {}
        row = {
            "universe": universe, "variant": vlabel, "top_n": top_n,
            "total_return": s["total_return"], "cagr": round(cagr(s["total_return"]), 2),
            "benchmark_return": s["benchmark_return"],
            "bench_cagr": round(cagr(s["benchmark_return"]), 2),
            "alpha": s["alpha"], "max_drawdown": s["max_drawdown"],
            "sharpe": s["sharpe_ratio"], "rebalances": s["total_trades"],
            "avg_held": round(sum(counts) / len(counts), 1) if counts else 0,
            "max_held": max(counts) if counts else 0,
            "cash_days": sum(1 for c in counts if c == 0), "sessions": len(counts),
            "pool_before": pf.get("avg_pool_before"), "pool_after": pf.get("avg_pool_after"),
            "days_emptied": pf.get("days_emptied"), "secs": round(el),
        }
        results.append(row)
        json.dump(results, open(OUT, "w"), indent=2)
        print(f"        CAGR {row['cagr']:>6.2f}%  Sharpe {row['sharpe']:>6.3f}  "
              f"DD {row['max_drawdown']:>7.2f}%  held {row['avg_held']:>5.1f}  "
              f"cash {row['cash_days']:>4}d  ({row['secs']}s)", flush=True)

print(f"DONE — {len(results)}/{total} cells written to {OUT}", flush=True)
