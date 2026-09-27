"""mean_short > mean_long crossover — hold while short-term relative strength leads.

No price input at all. mean_short is SMA(composite,22) and mean_long is SMA(composite,222),
both time-averages of the TrueVX composite, so this is a 22/222 crossover of RELATIVE
strength versus Nifty 50. Buy and hold while short > long, exit when it crosses back.

Expressed as a rich filter (metric-vs-metric compare) so the production engine runs it:
    [[{kind: compare, lhs_metric: mean_short, op: '>', rhs: {metric: mean_long}}]]

Three windows per universe. The two disjoint ones are the robustness test that killed the
ATH band and every price-position measure: same sign in both, or it does not count.
"""
import json
import time

import requests

BASE = "http://127.0.0.1:3001"
OUT = "_stack_results.json"
FILTER = [[{"kind": "compare", "lhs_metric": "mean_short", "op": ">",
            "rhs": {"metric": "mean_mid"}},
           {"kind": "compare", "lhs_metric": "mean_mid", "op": ">",
            "rhs": {"metric": "mean_long"}}]]
UNIVERSES = ["NIFTY200", "NIFTYLARGEMIDCAP250", "NIFTYSMALLCAP250",
             "NIFTY500", "NIFTYMICROCAP250", "NIFTY500MICRO750"]
WINDOWS = [("full 2022-01..2026-08", "2022-01-01", "2026-08-28", 4.657),
           ("W1 2020-01..2023-06", "2020-01-01", "2023-06-30", 3.496),
           ("W2 2023-07..2026-08", "2023-07-01", "2026-08-28", 3.163)]
COMMON = {"strategy_id": "strategy_1788028291660", "portfolio_base_value": 100000,
          "rebalance_frequency": "monthly", "rebalance_date": "first",
          "rebalance_type": "equal_weight", "point_in_time_membership": True,
          "include_brokerage": True, "benchmark_symbol": "Nifty 50",
          "momentum_ranking": "none", "max_holdings": None,
          "primary_filter_take": "none", "filter": FILTER}

res, i = [], 0
todo = [(u, w) for u in UNIVERSES for w in WINDOWS]
for u, (wname, s, e, yrs) in todo:
    i += 1
    print(f"[{i}/{len(todo)}] {u} | {wname}", flush=True)
    t = time.time()
    try:
        r = requests.post(f"{BASE}/api/simulation/run",
                          json={**COMMON, "universe": u, "start_date": s, "end_date": e},
                          timeout=7200)
    except Exception as ex:
        res.append({"u": u, "w": wname, "error": str(ex)[:120]})
        json.dump(res, open(OUT, "w"), indent=2)
        print(f"        ERROR {str(ex)[:100]}", flush=True); continue
    el = time.time() - t
    if r.status_code != 200:
        res.append({"u": u, "w": wname, "error": f"HTTP {r.status_code}"})
        json.dump(res, open(OUT, "w"), indent=2)
        print(f"        ERROR HTTP {r.status_code}", flush=True); continue
    sim = r.json()["simulation"]; sm = sim["summary"]
    held = [len(d.get("holdings") or []) for d in sim["results"]]
    row = {"u": u, "w": wname, "yrs": yrs, "total_return": sm["total_return"],
           "cagr": round(((1 + sm["total_return"] / 100) ** (1 / yrs) - 1) * 100, 2),
           "bench": sm["benchmark_return"],
           "bench_cagr": round(((1 + sm["benchmark_return"] / 100) ** (1 / yrs) - 1) * 100, 2),
           "dd": sm["max_drawdown"], "sharpe": sm["sharpe_ratio"],
           "held": round(sum(held) / len(held), 1), "secs": round(el)}
    res.append(row); json.dump(res, open(OUT, "w"), indent=2)
    print(f"        CAGR {row['cagr']:>6.2f}%  (bench {row['bench_cagr']:>5.2f}%)  "
          f"Sharpe {row['sharpe']:>6.3f}  DD {row['dd']:>7.2f}%  held {row['held']:>6.1f}  "
          f"({row['secs']}s)", flush=True)
print(f"DONE — {len(res)}/{len(todo)}", flush=True)
