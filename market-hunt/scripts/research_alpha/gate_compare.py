# Gate comparison on the micro portfolio at N=20 (recommended config), full window.
import copy
import json
import os
import sys

REPO = "D:/gs/workspace/tvagent/market-hunt"
sys.path.insert(0, REPO)
os.chdir(REPO)

import api_server  # noqa: E402
from api_server import _compute_portfolio_run_sync, mongo_conn  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))
mongo_conn.connect()
p0 = mongo_conn.db.portfolios.find_one({"id": "portfolio_1782153526190"})

VARIANTS = [
    ("no_gate",        {}),
    ("sma200",         {"regime_gate": True, "gate_mode": "sma", "gate_symbol": "Nifty 500", "gate_sma": 200}),
    ("fib222_66",      {"regime_gate": True, "gate_mode": "dynamic_fib", "gate_symbol": "Nifty 500",
                        "gate_fib_lookback": 222, "gate_fib_level": 0.66}),
    ("fib222_50",      {"regime_gate": True, "gate_mode": "dynamic_fib", "gate_symbol": "Nifty 500",
                        "gate_fib_lookback": 222, "gate_fib_level": 0.50}),
    ("pf_curve_fib",   {"portfolio_gate": True, "gate_fib_lookback": 222, "gate_fib_level": 0.66}),
    ("stock_fib",      {"stock_gate": True, "gate_fib_lookback": 222, "gate_fib_level": 0.66}),
]

for name, gates in VARIANTS:
    dest = os.path.join(OUT, f"gate_{name}.json")
    if os.path.exists(dest):
        print(f"[skip] {name}", flush=True)
        continue
    p = copy.deepcopy(p0)
    for s in p["sleeves"]:
        s["max_holdings"] = 20
        s.update(gates)
    res = _compute_portfolio_run_sync(p, "2022-01-01", "2026-07-05")
    with open(dest, "w") as f:
        json.dump({"summary": res["summary"],
                   "curve": [{"date": c["date"], "v": c["portfolio_value"],
                              "h": c.get("holdings_count")} for c in res["curve"]]}, f)
    s_ = res["summary"]
    print(f"[done] {name:14s}: ret {s_['total_return']:+7.1f}% alpha {s_['alpha']:+7.1f} "
          f"dd {s_['max_drawdown']:6.1f}% sharpe {s_['sharpe_ratio']:.2f}", flush=True)

print("ALL DONE", flush=True)
