"""Attribute the biased-vs-point-in-time gap to specific stocks — return-based.

The two runs differ in ONE thing: which stocks are eligible at each rebalance.
So the gap traces to stocks the BIASED run held while they were NOT yet index
members (back-dated climbers). Absolute per-stock P&L is not additive (rebalancing
resets cost basis), so we attribute by FORWARD PER-REBALANCE RETURN read straight
from prices: at each monthly rebalance, classify every held stock as member-then
vs look-ahead, and compare the returns each class earned over the next month.
"""
import os
import sys
from collections import defaultdict
from datetime import date, datetime

import requests
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

API = "http://localhost:3011/api/simulation/run"
INDEX = sys.argv[1] if len(sys.argv) > 1 else "NIFTY100"
BASE = dict(
    strategy_id="strategy_1781939795919", portfolio_base_value=100000,
    rebalance_frequency="monthly", rebalance_date="first", rebalance_type="equal_weight",
    universe=INDEX, benchmark_symbol="Nifty 50", max_holdings=10,
    momentum_ranking="20_day_return", start_date="2020-01-01", end_date="2026-06-30",
)


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, INDEX, anchor=date.today())

    sim = requests.post(API, json={**BASE, "point_in_time_membership": False}, timeout=900).json()["simulation"]
    results = sim["results"]

    # close price per (symbol, date) straight from the holdings snapshots
    px: dict[str, dict[str, float]] = defaultdict(dict)
    for day in results:
        for h in day["holdings"]:
            px[h["symbol"]][day["date"]] = h.get("current_price") or 0.0

    # rebalance day = first result-day of each calendar month
    rebal_days, seen = [], set()
    for day in results:
        ym = day["date"][:7]
        if ym not in seen:
            seen.add(ym); rebal_days.append(day)

    look = {"n": 0, "ret": 0.0}      # look-ahead picks (not a member when held)
    elig = {"n": 0, "ret": 0.0}      # legitimately-eligible picks
    per_stock_look = defaultdict(lambda: {"n": 0, "ret": 0.0})
    member_first = {}

    for a, b in zip(rebal_days, rebal_days[1:]):
        da = datetime.strptime(a["date"], "%Y-%m-%d").date()
        mem = members_as_of(iv, da)
        for h in a["holdings"]:
            s = h["symbol"]
            p0 = px[s].get(a["date"]); p1 = px[s].get(b["date"])
            if not p0 or not p1:
                continue
            r = p1 / p0 - 1
            if s in mem:
                elig["n"] += 1; elig["ret"] += r
            else:
                look["n"] += 1; look["ret"] += r
                per_stock_look[s]["n"] += 1; per_stock_look[s]["ret"] += r
                member_first.setdefault(s, da)

    def avg(d):
        return 100 * d["ret"] / d["n"] if d["n"] else 0.0

    print(f"\n=== {INDEX} BIASED run — per-rebalance forward-return attribution ===")
    print(f"held-stock slots: look-ahead={look['n']}  eligible={elig['n']}  "
          f"(={100*look['n']/(look['n']+elig['n']):.0f}% of all slots were not-yet-members)")
    print(f"avg 1-month return per slot:  LOOK-AHEAD={avg(look):+.2f}%   ELIGIBLE={avg(elig):+.2f}%")
    print(f"summed monthly return:        LOOK-AHEAD={100*look['ret']:+.0f}%  ELIGIBLE={100*elig['ret']:+.0f}%")
    print(f"\nTop look-ahead stocks by cumulative forward return WHILE held-before-membership:")
    print(f"{'symbol':12} {'months_held_as_lookahead':>4} {'cum_fwd_return':>16} {'first_held_lookahead':>20}")
    for s, d in sorted(per_stock_look.items(), key=lambda kv: kv[1]["ret"], reverse=True)[:15]:
        print(f"{s:12} {d['n']:>22} {100*d['ret']:>14.0f}%   {str(member_first[s]):>20}")


if __name__ == "__main__":
    main()
