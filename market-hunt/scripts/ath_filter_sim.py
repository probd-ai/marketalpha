"""Near-ATH universe filter on top of the materialized base universe.

Replicates the URL simulation (strategy_1782331814378, NIFTY50, PIT, monthly/first,
equal-weight, useAllEligible, 2020-01-01..2025-08-30, statutory charges) and adds one
extra gate at each rebalance: keep only stocks whose close is within X% of their
ALL-TIME high. Variants: baseline (no gate), -1%, -5%, -10%.

Point-in-time correctness:
  - monthly universe comes from base_universe (already PIT + filter-replayed)
  - ATH = running max of CLOSE over the stock's FULL price history up to that date
    (2005+ collections), never future data
  - empty selection month -> 100% cash until the next rebalance

Mechanics (engine parity where it matters):
  - full re-weight to equal rupee at each snapshot close
  - charges on actual turnover: sells pay SELL_BPS, buys pay BUY_BPS (statutory,
    zero brokerage)
  - daily mark-to-market between snapshots; Sharpe uses 6% risk-free like the engine

Usage:
  .venv/Scripts/python.exe scripts/ath_filter_sim.py --run-key <key> [--base 100000]
"""
import argparse
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from settings import MONGO_URI, DB_NAME  # noqa: E402

PRICE_COLLECTIONS = ["prices_2005_2009", "prices_2010_2014", "prices_2015_2019",
                     "prices_2020_2024", "prices_2025_2029"]
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)
RISK_FREE = 0.06


def _band(thr):
    """Keep names within thr of their ATH (None = no gate)."""
    def sel(ratios):
        return [s for s, r in ratios.items() if thr is None or r >= 1 - thr]
    return sel


def _rank(n, furthest):
    """Take the n names furthest below (or nearest to) their ATH."""
    def sel(ratios):
        return [s for s, _ in sorted(ratios.items(), key=lambda kv: kv[1],
                                     reverse=not furthest)[:n]]
    return sel


# (label, selector(ratios: {sym: close/ATH}) -> [sym])
VARIANTS = [
    ("baseline", _band(None)),
    ("ATH -1%", _band(0.01)),
    ("ATH -5%", _band(0.05)),
    ("ATH -10%", _band(0.10)),
    ("bottom10", _rank(10, furthest=True)),   # 10 furthest below ATH
    ("top10", _rank(10, furthest=False)),     # 10 nearest ATH (contrast)
]


def close_frame(db, symbols, end_dt):
    """Full-history close DataFrame (dates x symbols) — history feeds the ATH."""
    rows = []
    for coll in PRICE_COLLECTIONS:
        rows += list(db[coll].find(
            {"symbol": {"$in": symbols}, "date": {"$lte": end_dt}},
            {"_id": 0, "symbol": 1, "date": 1, "close_price": 1}))
    df = pd.DataFrame(rows).dropna(subset=["close_price"])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    px = (df.drop_duplicates(["symbol", "date"])
            .pivot(index="date", columns="symbol", values="close_price")
            .sort_index().astype(float))
    return px


def simulate(px, ath_ratio, snaps, base_value, select):
    """Equal-weight snapshot-to-snapshot sim. snaps: [(Timestamp, set(symbols))].
    `select` picks the held names from {sym: close/ATH ratio} at each rebalance."""
    sim_dates = px.index[px.index >= snaps[0][0]]
    shares: dict = {}
    cash = base_value
    curve = pd.Series(index=sim_dates, dtype=float)
    snap_idx = {d: syms for d, syms in snaps}
    breadth, charges_total = [], 0.0

    last_px = px.ffill()
    for d in sim_dates:
        if d in snap_idx:
            held_val = {s: n * last_px.at[d, s] for s, n in shares.items()
                        if not np.isnan(last_px.at[d, s])}
            value = cash + sum(held_val.values())
            ratios = {s: ath_ratio.at[d, s] for s in snap_idx[d]
                      if not np.isnan(last_px.at[d, s])}
            elig = select(ratios)
            breadth.append(len(elig))
            target = {s: value / len(elig) for s in elig} if elig else {}
            buy_turn = sum(max(0.0, target.get(s, 0.0) - held_val.get(s, 0.0))
                           for s in set(target) | set(held_val))
            sell_turn = sum(max(0.0, held_val.get(s, 0.0) - target.get(s, 0.0))
                            for s in set(target) | set(held_val))
            charges = buy_turn * BUY_BPS + sell_turn * SELL_BPS
            charges_total += charges
            value -= charges
            target = {s: value / len(elig) for s in elig} if elig else {}
            shares = {s: v / last_px.at[d, s] for s, v in target.items()}
            cash = value - sum(target.values())
        curve.at[d] = cash + sum(n * last_px.at[d, s] for s, n in shares.items()
                                 if not np.isnan(last_px.at[d, s]))
    return curve, breadth, charges_total


def metrics(curve, base_value):
    total_ret = (curve.iloc[-1] / base_value - 1) * 100
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    cagr = ((curve.iloc[-1] / base_value) ** (1 / years) - 1) * 100
    daily = curve.pct_change().dropna()
    vol = daily.std() * np.sqrt(252)
    ann = (1 + daily.mean()) ** 252 - 1
    sharpe = (ann - RISK_FREE) / vol if vol > 0 else 0.0
    dd = ((curve / curve.cummax()) - 1).min() * 100
    return total_ret, cagr, sharpe, dd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-key", required=True)
    ap.add_argument("--base", type=float, default=100000.0)
    ap.add_argument("--benchmark", default="Nifty 50")
    ap.add_argument("--tag", default="", help="suffix for output filenames")
    args = ap.parse_args()

    db = MongoClient(MONGO_URI)[DB_NAME]
    manifest = db.base_universe_runs.find_one({"run_key": args.run_key})
    if not manifest:
        raise SystemExit(f"run_key {args.run_key!r} not found")
    snap_docs = list(db.base_universe.find({"run_key": args.run_key}).sort("date", 1))
    snaps = [(pd.Timestamp(s["date"]), set(s["symbols"])) for s in snap_docs]
    all_syms = sorted(set().union(*(s for _, s in snaps)))
    end_dt = datetime.strptime(manifest["end"], "%Y-%m-%d")

    print(f"loading full price history for {len(all_syms)} symbols + benchmark…")
    px = close_frame(db, all_syms, end_dt)
    ath_ratio = (px.ffill() / px.ffill().cummax())  # close / running ATH, PIT-safe

    bench = close_frame(db, [args.benchmark], end_dt)[args.benchmark]
    bench = bench[bench.index >= snaps[0][0]]
    bench_curve = args.base * bench / bench.iloc[0]
    b_ret, b_cagr, b_sharpe, b_dd = metrics(bench_curve, args.base)

    print(f"\nsim window: {manifest['start']}..{manifest['end']}  base=₹{args.base:,.0f}"
          f"  charges: statutory (0 brokerage)\n")
    hdr = (f"{'variant':<10} {'avg n':>6} {'min n':>6} {'empty':>6} {'return%':>9} "
           f"{'CAGR%':>7} {'alpha%':>8} {'sharpe':>7} {'maxDD%':>8} {'charges₹':>10}")
    print(hdr)
    print("─" * len(hdr))
    results = {}
    for label, select in VARIANTS:
        curve, breadth, charges = simulate(px, ath_ratio, snaps, args.base, select)
        t_ret, cagr, sharpe, dd = metrics(curve, args.base)
        empty = sum(1 for b in breadth if b == 0)
        print(f"{label:<10} {np.mean(breadth):>6.1f} {min(breadth):>6} {empty:>6} "
              f"{t_ret:>9.1f} {cagr:>7.2f} {t_ret - b_ret:>8.1f} {sharpe:>7.2f} "
              f"{dd:>8.1f} {charges:>10,.0f}")
        results[label] = curve
    print("─" * len(hdr))
    print(f"{args.benchmark:<10} {'':>6} {'':>6} {'':>6} {b_ret:>9.1f} {b_cagr:>7.2f} "
          f"{'—':>8} {b_sharpe:>7.2f} {b_dd:>8.1f}")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)
    out = pd.DataFrame({k: v for k, v in results.items()})
    out[args.benchmark] = bench_curve
    path = os.path.join(out_dir, f"ath_filter_curves{args.tag}.csv")
    out.to_csv(path, index_label="date", float_format="%.2f")
    print(f"\ndaily curves: {path}")


if __name__ == "__main__":
    main()
