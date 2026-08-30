"""Materialize the monthly base universe a simulator run would produce, so strategies
can be layered on top without re-running the qualification engine each time.

Replays EXACTLY what /api/simulation/run does for universe production with
useAllEligible (max_holdings=None):
  - PIT membership (fail-loud if reconstitution events missing)
  - trading calendar = dates with BOTH indicator and price data (engine parity)
  - rich filter evaluated DAILY with per-symbol CrossState (last_cross_dir is
    path-dependent — it must see every session, not just rebalance dates)
  - snapshot recorded on day 0 + every monthly/first rebalance date, gated to
    members_as_of that date

Storage (idempotent per run_key — delete+insert):
  base_universe_runs : one manifest doc per run (params, filter, counts)
  base_universe      : one doc per snapshot {run_key, date, symbols, n}

Usage:
  .venv/Scripts/python.exe scripts/build_base_universe.py \
      --strategy strategy_1782331814378 --universe NIFTY50 \
      --start 2020-01-01 --end 2025-08-30 [--store]

Default is a dry run (prints snapshots); pass --store to persist.
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime

from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of, union_over  # noqa: E402
from engine.stateless import (  # noqa: E402
    _filter_grids,
    _parse_filter_groups,
    apply_strategy_rules,
    get_rebalance_dates,
    qualify_by_filter,
)
from settings import MONGO_URI, DB_NAME  # noqa: E402

PRICE_COLLECTIONS = ["prices_2015_2019", "prices_2020_2024", "prices_2025_2029"]


def build(db, strategy_id: str, universe: str, start: str, end: str,
          rebalance_frequency: str = "monthly", rebalance_date: str = "first"):
    strat = db.simulation_strategies.find_one({"id": strategy_id})
    if not strat:
        raise SystemExit(f"strategy {strategy_id!r} not found")

    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    intervals = load_membership(db, universe, anchor=date.today())  # fail-loud PIT
    ever = sorted(union_over(intervals, start_dt.date(), end_dt.date()))
    print(f"PIT ever-members in {universe} [{start}..{end}]: {len(ever)}")

    # indicators: {date_str: {sym: stock_data}} — same null handling as the engine (`or 0`)
    indicator_data: dict = defaultdict(dict)
    for doc in db.indicators.find(
        {"indicator_type": "truevx", "symbol": {"$in": ever},
         "date": {"$gte": start_dt, "$lte": end_dt}},
        {"symbol": 1, "date": 1, "data": 1},
    ):
        dd = doc.get("data") or {}
        indicator_data[doc["date"].strftime("%Y-%m-%d")][doc["symbol"]] = {
            "symbol": doc["symbol"],
            "truevx_score": dd.get("truevx_score") or 0,
            "mean_short": dd.get("mean_short") or 0,
            "mean_mid": dd.get("mean_mid") or 0,
            "mean_long": dd.get("mean_long") or 0,
        }

    # prices: only needed to reproduce the engine's calendar (indicator ∩ price dates)
    price_dates_by_day: dict = defaultdict(set)
    for coll in PRICE_COLLECTIONS:
        for doc in db[coll].find(
            {"symbol": {"$in": ever}, "date": {"$gte": start_dt, "$lte": end_dt}},
            {"symbol": 1, "date": 1},
        ):
            price_dates_by_day[doc["date"].strftime("%Y-%m-%d")].add(doc["symbol"])

    dates = sorted(set(indicator_data.keys()) & set(price_dates_by_day.keys()))
    if not dates:
        raise SystemExit("no overlapping indicator+price dates — nothing to build")
    rebal = get_rebalance_dates(dates, rebalance_frequency, rebalance_date)
    print(f"calendar: {len(dates)} sessions ({dates[0]}..{dates[-1]}), "
          f"{len(rebal)} rebalance dates")

    filter_groups = _parse_filter_groups(strat.get("filter")) if strat.get("filter") else None
    grids = _filter_grids(filter_groups) if filter_groups else []
    cross_states: dict = {}
    if filter_groups:
        print(f"rich filter: {len(filter_groups)} group(s), {len(grids)} grid(s)")

    snapshots = []  # (date_str, sorted symbols)
    for i, ds in enumerate(dates):
        day = indicator_data.get(ds, {})
        if not day:
            continue
        if filter_groups is not None:
            qualified = qualify_by_filter(day, filter_groups, cross_states, grids)
        else:
            qualified = apply_strategy_rules(day, strat.get("rules") or [])
        if i == 0 or ds in rebal:  # engine always rebalances day 0
            members = members_as_of(intervals, datetime.strptime(ds, "%Y-%m-%d").date())
            syms = sorted(s["symbol"] for s in qualified
                          if s["symbol"] in members and s["symbol"] in price_dates_by_day[ds])
            snapshots.append((ds, syms))

    return strat, snapshots


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--universe", default="NIFTY50")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--freq", default="monthly")
    ap.add_argument("--rdate", default="first")
    ap.add_argument("--store", action="store_true")
    args = ap.parse_args()

    db = MongoClient(MONGO_URI)[DB_NAME]
    strat, snapshots = build(db, args.strategy, args.universe, args.start, args.end,
                             args.freq, args.rdate)

    run_key = (f"{args.strategy}_{args.universe}_{args.freq}_{args.rdate}"
               f"_pit_{args.start}_{args.end}")
    sizes = [len(s) for _, s in snapshots]
    print(f"\nrun_key: {run_key}")
    print(f"snapshots: {len(snapshots)}  breadth min/med/max = "
          f"{min(sizes)}/{sorted(sizes)[len(sizes)//2]}/{max(sizes)}")
    for ds, syms in snapshots[:3] + snapshots[-2:]:
        print(f"  {ds}: n={len(syms)}  {syms[:8]}{'...' if len(syms) > 8 else ''}")

    if not args.store:
        print("\nDRY RUN — pass --store to persist to Mongo (base_universe / base_universe_runs)")
        return

    db.base_universe.delete_many({"run_key": run_key})
    db.base_universe_runs.delete_many({"run_key": run_key})
    db.base_universe.insert_many([
        {"run_key": run_key, "strategy_id": args.strategy, "universe": args.universe,
         "date": datetime.strptime(ds, "%Y-%m-%d"), "symbols": syms, "n": len(syms)}
        for ds, syms in snapshots
    ])
    db.base_universe_runs.insert_one({
        "run_key": run_key, "strategy_id": args.strategy, "strategy_name": strat.get("name"),
        "universe": args.universe, "start": args.start, "end": args.end,
        "rebalance_frequency": args.freq, "rebalance_date": args.rdate,
        "point_in_time": True, "filter": strat.get("filter"), "rules": strat.get("rules"),
        "n_snapshots": len(snapshots), "generated_at": datetime.now().isoformat(),
    })
    db.base_universe.create_index([("run_key", 1), ("date", 1)])
    print(f"STORED: {len(snapshots)} snapshots under run_key={run_key}")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"base_universe_{run_key}.json")
    with open(out_path, "w") as f:
        json.dump({"run_key": run_key, "snapshots": {ds: syms for ds, syms in snapshots}},
                  f, indent=1)
    print(f"JSON snapshot: {out_path}")


if __name__ == "__main__":
    main()
