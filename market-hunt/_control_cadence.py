"""Two gaps in the study so far.

1) THE MISSING CONTROL. Every cell has been scored against Nifty 50 — cap-weighted,
   large-cap only. Over 2022-2026 Indian mid/small caps massively outperformed it, so
   "beats Nifty 50" may be an equal-weight + down-cap tilt rather than stock selection.
   The honest control is the SAME universe, equal-weighted, with NO strategy filter.
   `No Filter` (strategy_1788028291660) has empty rules, so it qualifies everything.

2) CADENCE. Everything so far is monthly. Quarterly is untested in this session.

Cells: 6 EW controls (monthly) + 12 Alpha quarterly (V1 no-cut, V2 22d top-5%).
Compared against the monthly Alpha cells already measured.
"""
import json
import time

import requests

BASE = "http://127.0.0.1:3001"
OUT = "_control_cadence_results.json"
YEARS = 4.657
ALPHA = "strategy_1781939795919"
NOFILTER = "strategy_1788028291660"

UNIVERSES = [("NIFTY200", 10), ("NIFTYLARGEMIDCAP250", 13), ("NIFTYSMALLCAP250", 13),
             ("NIFTY500", 25), ("NIFTYMICROCAP250", 13), ("NIFTY500MICRO750", 37)]

COMMON = {"start_date": "2022-01-01", "end_date": "2026-08-28",
          "portfolio_base_value": 100000, "rebalance_date": "first",
          "rebalance_type": "equal_weight", "point_in_time_membership": True,
          "include_brokerage": True, "benchmark_symbol": "Nifty 50",
          "primary_filter_take": "none"}


def cells():
    for u, n in UNIVERSES:
        # the missing control: whole universe, equal weight, no selection at all
        yield (u, "EW-universe control (monthly)",
               {"strategy_id": NOFILTER, "universe": u, "rebalance_frequency": "monthly",
                "momentum_ranking": "none", "max_holdings": None})
    for u, n in UNIVERSES:
        yield (u, "Alpha quarterly, no cut",
               {"strategy_id": ALPHA, "universe": u, "rebalance_frequency": "quarterly",
                "momentum_ranking": "none", "max_holdings": None})
        yield (u, f"Alpha quarterly +22d/{n}",
               {"strategy_id": ALPHA, "universe": u, "rebalance_frequency": "quarterly",
                "momentum_ranking": "price_roc_22d", "max_holdings": n})


res, i = [], 0
todo = list(cells())
for u, label, extra in todo:
    i += 1
    print(f"[{i}/{len(todo)}] {u} | {label}", flush=True)
    t = time.time()
    try:
        r = requests.post(f"{BASE}/api/simulation/run", json={**COMMON, **extra}, timeout=7200)
    except Exception as e:
        res.append({"universe": u, "variant": label, "error": str(e)[:150]})
        json.dump(res, open(OUT, "w"), indent=2)
        print(f"        ERROR {str(e)[:110]}", flush=True); continue
    el = time.time() - t
    if r.status_code != 200:
        res.append({"universe": u, "variant": label, "error": f"HTTP {r.status_code}: "
                                                              f"{str(r.json().get('detail'))[:160]}"})
        json.dump(res, open(OUT, "w"), indent=2)
        print(f"        ERROR HTTP {r.status_code}", flush=True); continue
    sim = r.json()["simulation"]; s = sim["summary"]
    c = [len(d.get("holdings") or []) for d in sim["results"]]
    row = {"universe": u, "variant": label, "total_return": s["total_return"],
           "cagr": round(((1 + s["total_return"] / 100) ** (1 / YEARS) - 1) * 100, 2),
           "benchmark_return": s["benchmark_return"], "alpha": s["alpha"],
           "max_drawdown": s["max_drawdown"], "sharpe": s["sharpe_ratio"],
           "rebalances": s["total_trades"], "avg_held": round(sum(c) / len(c), 1),
           "cash_days": sum(1 for x in c if x == 0), "secs": round(el)}
    res.append(row); json.dump(res, open(OUT, "w"), indent=2)
    print(f"        CAGR {row['cagr']:>6.2f}%  Sharpe {row['sharpe']:>6.3f}  "
          f"DD {row['max_drawdown']:>7.2f}%  held {row['avg_held']:>6.1f}  "
          f"rebal {row['rebalances']:>3}  ({row['secs']}s)", flush=True)

print(f"DONE — {len(res)}/{len(todo)}", flush=True)
