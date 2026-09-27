"""Head-to-head: STACKED vs ALPHA, both with 22d momentum top-5%, same windows.

  STACKED  mean_short > mean_mid > mean_long        (no tuned thresholds)
  ALPHA    truevx>90 & short>80 & mid>60 & long>50  (four tuned thresholds)

Both then ranked by price_roc_22d and cut to 5% of the universe, the same convention used
in the original universe sweep. Everything else identical: PIT, monthly/first, equal-weight,
statutory charges, Rs1L base, benchmark Nifty 50.

Three windows each. W1/W2 are disjoint and are the robustness test; `full` is the window
the rest of this study used, so the rows drop into the existing tables.

36 cells. Alpha is re-run rather than reused so both sides come from one identical setup.
"""
import json
import time

import requests

BASE = "http://127.0.0.1:3001"
OUT = "_stack_vs_alpha_results.json"
STACK = [[{"kind": "compare", "lhs_metric": "mean_short", "op": ">",
           "rhs": {"metric": "mean_mid"}},
          {"kind": "compare", "lhs_metric": "mean_mid", "op": ">",
           "rhs": {"metric": "mean_long"}}]]
UNIVERSES = [("NIFTY200", 10), ("NIFTYLARGEMIDCAP250", 13), ("NIFTYSMALLCAP250", 13),
             ("NIFTY500", 25), ("NIFTYMICROCAP250", 13), ("NIFTY500MICRO750", 37)]
WINDOWS = [("full", "2022-01-01", "2026-08-28", 4.657),
           ("W1", "2020-01-01", "2023-06-30", 3.496),
           ("W2", "2023-07-01", "2026-08-28", 3.163)]
COMMON = {"portfolio_base_value": 100000, "rebalance_frequency": "monthly",
          "rebalance_date": "first", "rebalance_type": "equal_weight",
          "point_in_time_membership": True, "include_brokerage": True,
          "benchmark_symbol": "Nifty 50", "momentum_ranking": "price_roc_22d",
          "primary_filter_take": "none"}
# stacked runs against the empty-rule strategy with an explicit filter override;
# alpha runs its own saved filter, so no override is passed.
VARIANTS = [("stacked", "strategy_1788028291660", STACK),
            ("alpha", "strategy_1781939795919", None)]

res, i = [], 0
todo = [(v, u, n, w) for v in VARIANTS for u, n in UNIVERSES for w in WINDOWS]
for (vname, sid, filt), u, n, (wname, s, e, yrs) in todo:
    i += 1
    print(f"[{i}/{len(todo)}] {vname:<8} {u:<20} {wname}", flush=True)
    body = {**COMMON, "strategy_id": sid, "universe": u, "max_holdings": n,
            "start_date": s, "end_date": e}
    if filt is not None:
        body["filter"] = filt
    t = time.time()
    try:
        r = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=7200)
    except Exception as ex:
        res.append({"v": vname, "u": u, "w": wname, "error": str(ex)[:120]})
        json.dump(res, open(OUT, "w"), indent=2)
        print(f"        ERROR {str(ex)[:100]}", flush=True); continue
    el = time.time() - t
    if r.status_code != 200:
        res.append({"v": vname, "u": u, "w": wname, "error": f"HTTP {r.status_code}"})
        json.dump(res, open(OUT, "w"), indent=2)
        print(f"        ERROR HTTP {r.status_code}", flush=True); continue
    sim = r.json()["simulation"]; sm = sim["summary"]
    held = [len(d.get("holdings") or []) for d in sim["results"]]
    row = {"v": vname, "u": u, "w": wname, "n": n, "yrs": yrs,
           "total_return": sm["total_return"],
           "cagr": round(((1 + sm["total_return"] / 100) ** (1 / yrs) - 1) * 100, 2),
           "bench_cagr": round(((1 + sm["benchmark_return"] / 100) ** (1 / yrs) - 1) * 100, 2),
           "dd": sm["max_drawdown"], "sharpe": sm["sharpe_ratio"],
           "trades": sm["total_trades"], "held": round(sum(held) / len(held), 1),
           "secs": round(el)}
    res.append(row); json.dump(res, open(OUT, "w"), indent=2)
    print(f"        CAGR {row['cagr']:>6.2f}%  Sharpe {row['sharpe']:>6.3f}  "
          f"DD {row['dd']:>7.2f}%  held {row['held']:>5.1f}  ({row['secs']}s)", flush=True)
print(f"DONE — {len(res)}/{len(todo)}", flush=True)
