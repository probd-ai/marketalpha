# Run controlled simulator variants of strategy_1781939795919 ("Alpha") through the live engine.
import json, time, sys, os
import requests

BASE = "http://127.0.0.1:3001"
OUT = os.path.dirname(os.path.abspath(__file__))

COMMON = dict(
    strategy_id="strategy_1781939795919",
    portfolio_base_value=100000,
    rebalance_frequency="monthly",
    rebalance_type="equal_weight",
    benchmark_symbol="Nifty 50",
    momentum_ranking="20_day_return",
    start_date="2020-01-01",
    end_date="2026-08-30",
    point_in_time_membership=True,
    side="long",
    annual_borrow_rate=0,
    regime_gate=False,
)

VARIANTS = [
    ("base750_mid",   dict(universe="NIFTY500MICRO750", rebalance_date="mid",   max_holdings=25)),
    ("base750_first", dict(universe="NIFTY500MICRO750", rebalance_date="first", max_holdings=25)),
    ("n500_mid",      dict(universe="NIFTY500",         rebalance_date="mid",   max_holdings=25)),
    ("n200_mid",      dict(universe="NIFTY200",         rebalance_date="mid",   max_holdings=25)),
    ("micro_mid",     dict(universe="NIFTYMICROCAP250", rebalance_date="mid",   max_holdings=25)),
    ("all750_mid",    dict(universe="NIFTY500MICRO750", rebalance_date="mid",   max_holdings=None)),
    ("top50_750_mid", dict(universe="NIFTY500MICRO750", rebalance_date="mid",   max_holdings=50)),
]

only = sys.argv[1:] if len(sys.argv) > 1 else None

for name, overrides in VARIANTS:
    if only and name not in only:
        continue
    dest = os.path.join(OUT, f"sim_{name}.json")
    if os.path.exists(dest):
        print(f"[skip] {name} (already exists)", flush=True)
        continue
    params = {**COMMON, **overrides}
    t0 = time.time()
    print(f"[run ] {name} ...", flush=True)
    try:
        r = requests.post(f"{BASE}/api/simulation/run", json=params, timeout=3600)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[FAIL] {name}: {e}", flush=True)
        continue
    with open(dest, "w") as f:
        json.dump(data, f)
    sim = data.get("simulation", data)
    s = sim.get("summary", {})
    print(f"[done] {name} in {time.time()-t0:.0f}s | total_return={s.get('total_return')} "
          f"benchmark={s.get('benchmark_return')} alpha={s.get('alpha')} "
          f"maxDD={s.get('max_drawdown')} trades={s.get('total_trades')}", flush=True)

print("ALL DONE", flush=True)
