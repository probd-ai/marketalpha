#!/usr/bin/env python3
"""Run the aligned-stack on the whole PIT-capable universe (NIFTY500MICRO750, ~750 names)
and decompose the held book by cap tier (N50 / Next50 / Midcap150 / Smallcap250 / Microcap250).
Tier = current-snapshot index_meta membership (approx; not as-of-date). Read via the running API."""
import requests, collections
from datetime import datetime

BASE = "http://localhost:3001"
UNIV = "NIFTY500MICRO750"

# 4-rule aligned-stack (the named strategy: l>50, m>60, S>80, truevx>90)
RULES = [
    {"id": "a", "metric": "truevx_score", "operator": ">", "threshold": 90.0, "name": "r"},
    {"id": "b", "metric": "mean_short",  "operator": ">", "threshold": 80.0, "name": "r"},
    {"id": "c", "metric": "mean_mid",    "operator": ">", "threshold": 60.0, "name": "r"},
    {"id": "d", "metric": "mean_long",   "operator": ">", "threshold": 50.0, "name": "r"},
]

# Tier map: assign each symbol to its highest-cap tier (priority order).
TIER_ORDER = [("N50", "NIFTY50"), ("Next50", "NIFTY100"), ("Mid150", "NIFTYMIDCAP150"),
              ("Small250", "NIFTYSMALLCAP250"), ("Micro250", "NIFTYMICROCAP250")]

def build_tier_map():
    tier_of = {}
    counts = {}
    for label, idx in TIER_ORDER:
        r = requests.get(f"{BASE}/api/data/index/{idx}", timeout=30).json()
        syms = [c["Symbol"] for c in r.get("companies", [])]
        n_new = 0
        for s in syms:
            if s not in tier_of:
                tier_of[s] = label; n_new += 1
        counts[label] = n_new
        print(f"  tier {label:9} <- {idx:18} {len(syms)} members ({n_new} new)")
    return tier_of, counts

def main():
    print("Building cap-tier map (current-snapshot index_meta)...")
    tier_of, tier_universe = build_tier_map()

    print(f"\nCreating temp strategy + running on {UNIV} (full period, PIT)...")
    sid = requests.post(f"{BASE}/api/simulation/strategies",
                        json={"name": "_whole_universe", "description": "auto", "rules": RULES},
                        timeout=30).json()["strategy_id"]
    try:
        body = {"strategy_id": sid, "portfolio_base_value": 100000, "rebalance_frequency": "monthly",
                "rebalance_date": "first", "rebalance_type": "equal_weight", "universe": UNIV,
                "benchmark_symbol": "Nifty 500", "max_holdings": None, "momentum_ranking": "20_day_return",
                "start_date": "2020-01-01", "end_date": "2026-08-30", "point_in_time_membership": True}
        t0 = datetime.now()
        sim = requests.post(f"{BASE}/api/simulation/run", json=body, timeout=600).json()["simulation"]
        secs = (datetime.now() - t0).total_seconds()
    finally:
        requests.delete(f"{BASE}/api/simulation/strategies/{sid}", timeout=30)

    s = sim["summary"]
    results = sim["results"]
    yrs = (datetime(2026, 8, 30) - datetime(2020, 1, 1)).days / 365.25
    cagr = ((1 + s["total_return"] / 100) ** (1 / yrs) - 1) * 100
    print(f"\n=== {UNIV} aligned-stack (4-rule) — full period ({secs:.0f}s) ===")
    print(f"  total_return={s['total_return']:.1f}%  CAGR={cagr:.2f}%  Sharpe={s['sharpe_ratio']}  "
          f"maxDD={s['max_drawdown']}%  benchN500={s['benchmark_return']:.1f}%")

    # Sample monthly (first result per calendar month) to keep it light + representative.
    seen = set(); sampled = []
    for d in results:
        ym = d["date"][:7]
        if ym not in seen:
            seen.add(ym); sampled.append(d)

    cnt = collections.Counter()      # holdings count by tier (summed over sampled months)
    mv = collections.Counter()        # market value by tier
    tot_holdings = 0
    # yearly capital share
    yr_mv = collections.defaultdict(lambda: collections.Counter())
    for d in sampled:
        yr = d["date"][:4]
        for h in d.get("holdings", []):
            t = tier_of.get(h["symbol"], "Other")
            cnt[t] += 1; mv[t] += h.get("market_value", 0) or 0
            yr_mv[yr][t] += h.get("market_value", 0) or 0
            tot_holdings += 1

    n = len(sampled)
    tiers = ["N50", "Next50", "Mid150", "Small250", "Micro250", "Other"]
    total_mv = sum(mv.values()) or 1
    print(f"\n  Sampled {n} monthly snapshots; avg holdings/month = {tot_holdings / n:.1f}")
    print(f"\n  {'Tier':10}{'univ':>6}{'avg_holds':>11}{'%count':>9}{'%capital':>10}")
    for t in tiers:
        uni = tier_universe.get(t, 0) if t != "Other" else 0
        avg_h = cnt[t] / n
        pc = 100 * cnt[t] / tot_holdings if tot_holdings else 0
        pk = 100 * mv[t] / total_mv
        print(f"  {t:10}{uni:>6}{avg_h:>11.1f}{pc:>8.1f}%{pk:>9.1f}%")

    print(f"\n  Capital share by tier, by year (%):")
    print("  " + "year ".ljust(7) + "".join(f"{t:>10}" for t in tiers))
    for yr in sorted(yr_mv):
        ytot = sum(yr_mv[yr].values()) or 1
        print("  " + yr.ljust(7) + "".join(f"{100*yr_mv[yr][t]/ytot:>9.1f}%" for t in tiers))

if __name__ == "__main__":
    main()
