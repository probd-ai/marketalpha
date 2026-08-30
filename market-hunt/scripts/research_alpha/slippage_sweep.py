# Slippage sensitivity sweep on portfolio_1783106608552 (in-process, no DB writes).
import copy
import json
import os
import sys

REPO = "D:/gs/workspace/tvagent/market-hunt"
sys.path.insert(0, REPO)
os.chdir(REPO)

import api_server  # noqa: E402
from api_server import _compute_portfolio_run_sync, mongo_conn  # noqa: E402

mongo_conn.connect()
p0 = mongo_conn.db.portfolios.find_one({"id": "portfolio_1783106608552"})
inception, as_of = "2022-01-01", "2026-07-04"

results = {}
for bps in (0, 15, 25, 40):
    p = copy.deepcopy(p0)
    for s in p["sleeves"]:
        s["slippage_bps"] = float(bps)
    res = _compute_portfolio_run_sync(p, inception, as_of)
    s_ = res["summary"]
    results[bps] = {k: s_[k] for k in ("total_return", "benchmark_return", "alpha",
                                       "max_drawdown", "sharpe_ratio")}
    print(f"[done] {bps:>3} bps: ret {s_['total_return']:+8.1f}%  alpha {s_['alpha']:+7.1f}  "
          f"dd {s_['max_drawdown']:6.1f}%  sharpe {s_['sharpe_ratio']:.2f}", flush=True)

print("\nRESULTS_JSON=" + json.dumps(results))
print("ALL DONE", flush=True)
