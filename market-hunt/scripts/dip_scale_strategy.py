"""Dip-buy / scale-in strategy on a materialized base universe (infinite-fund model).

Reads the monthly PIT universe stored by scripts/build_base_universe.py and runs a
per-stock, capital-unconstrained mean-reversion book:

  ENTRY    stock in this month's universe, flat, close <= prev_close*(1-entry_drop)
           -> buy a FIXED rupee amount at close (one buy per day per stock).
  SCALE-IN position open, close <= last_buy_price*(1-add_drop)
           -> add the same fixed amount at close; target moves to avg*(1+tp_scaled).
  EXIT     close > avg_buy*(1+tp_single) while 1 lot, or > avg*(1+tp_scaled) once >=2
           lots -> sell ALL at close. Sell is evaluated before any same-day buy.
  FORCED   symbol drops out of the monthly universe -> sell at that session's close
           ('universe_exit'); open book at the final session is closed at last close
           ('eod') and reported separately.

Explicit modelling assumptions (flagged, all parameterizable):
  - "down 1%" = close-to-close daily drop (vs previous trading close).
  - fixed amount default Rs.10,000 per buy, fractional shares.
  - add-on trigger is 2% below the LAST buy price; --max-adds caps scale-ins
    (default unlimited).
  - executions at same-day close, no slippage; statutory charges (STT delivery,
    exchange txn, SEBI, stamp, GST) applied both sides, zero brokerage.

Usage:
  .venv/Scripts/python.exe scripts/dip_scale_strategy.py \
      --run-key strategy_..._pit_2020-01-01_2025-08-30 [--fixed 10000] \
      [--entry-drop 0.01] [--add-drop 0.02] [--tp1 0.02] [--tp2 0.04] [--max-adds -1]
"""
import argparse
import bisect
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from settings import MONGO_URI, DB_NAME  # noqa: E402

PRICE_COLLECTIONS = ["prices_2015_2019", "prices_2020_2024", "prices_2025_2029"]
# Indian equity delivery statutory charges (fraction of turnover), zero brokerage.
BUY_BPS = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL_BPS = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)


def close_series(db, symbol, start_dt, end_dt):
    rows = []
    for coll in PRICE_COLLECTIONS:
        rows += list(db[coll].find(
            {"symbol": symbol, "date": {"$gte": start_dt, "$lte": end_dt}},
            {"_id": 0, "date": 1, "close_price": 1}))
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r["date"]):
        ds = r["date"].strftime("%Y-%m-%d")
        px = r.get("close_price")
        if ds in seen or px is None or float(px) <= 0:
            continue
        seen.add(ds)
        out.append((ds, float(px)))
    return out


def run_symbol(symbol, series, snap_dates, snap_sets, cfg):
    """Simulate one symbol independently. Returns (cycles, cashflows).

    cycles: closed position dicts. cashflows: (date_str, +invested / -cost_released)
    used later for the aggregate deployment curve.
    """
    cycles, cashflows = [], []
    pos = None  # {lots, shares, cost, cost_net, last_buy_px, entry_date}

    def in_universe(ds):
        k = bisect.bisect_right(snap_dates, ds) - 1
        return k >= 0 and symbol in snap_sets[k]

    def close_position(ds, px, exit_type):
        nonlocal pos
        gross = pos["shares"] * px
        pnl_gross = gross - pos["cost"]
        pnl_net = gross * (1 - SELL_BPS) - pos["cost_net"]
        cycles.append({
            "symbol": symbol, "entry_date": pos["entry_date"], "exit_date": ds,
            "lots": pos["lots"], "invested": round(pos["cost"], 2),
            "avg_price": round(pos["cost"] / pos["shares"], 2), "exit_price": px,
            "pnl_gross": round(pnl_gross, 2), "pnl_net": round(pnl_net, 2),
            "ret_net_pct": round(pnl_net / pos["cost"] * 100, 3),
            "exit_type": exit_type,
            "holding_days": (datetime.strptime(ds, "%Y-%m-%d")
                             - datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days,
        })
        cashflows.append((ds, -pos["cost"]))
        pos = None

    for i, (ds, px) in enumerate(series):
        if ds < snap_dates[0]:
            continue
        member = in_universe(ds)

        if pos is not None:
            # 1) forced exit the first session the name is out of the universe
            if not member:
                close_position(ds, px, "universe_exit")
                continue
            # 2) take-profit (checked before any same-day buy)
            tp = cfg.tp1 if pos["lots"] == 1 else cfg.tp2
            avg = pos["cost"] / pos["shares"]
            if px > avg * (1 + tp):
                close_position(ds, px, "target")
                continue  # no re-entry same day
            # 3) scale-in: another add_drop below the last buy price
            if (px <= pos["last_buy_px"] * (1 - cfg.add_drop)
                    and (cfg.max_adds < 0 or pos["lots"] - 1 < cfg.max_adds)):
                sh = cfg.fixed / px
                pos["shares"] += sh
                pos["cost"] += cfg.fixed
                pos["cost_net"] += cfg.fixed * (1 + BUY_BPS)
                pos["last_buy_px"] = px
                pos["lots"] += 1
                cashflows.append((ds, cfg.fixed))
            continue

        # flat: entry on a daily dip while in-universe
        if member and i > 0:
            prev_px = series[i - 1][1]
            if px <= prev_px * (1 - cfg.entry_drop):
                sh = cfg.fixed / px
                pos = {"lots": 1, "shares": sh, "cost": cfg.fixed,
                       "cost_net": cfg.fixed * (1 + BUY_BPS),
                       "last_buy_px": px, "entry_date": ds}
                cashflows.append((ds, cfg.fixed))

    if pos is not None:  # mark the final open position closed at last close
        close_position(series[-1][0], series[-1][1], "eod")
    return cycles, cashflows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-key", required=True)
    ap.add_argument("--fixed", type=float, default=10000.0)
    ap.add_argument("--entry-drop", dest="entry_drop", type=float, default=0.01)
    ap.add_argument("--add-drop", dest="add_drop", type=float, default=0.02)
    ap.add_argument("--tp1", type=float, default=0.02)
    ap.add_argument("--tp2", type=float, default=0.04)
    ap.add_argument("--max-adds", dest="max_adds", type=int, default=-1,
                    help="max scale-ins per position (-1 = unlimited)")
    cfg = ap.parse_args()

    db = MongoClient(MONGO_URI)[DB_NAME]
    manifest = db.base_universe_runs.find_one({"run_key": cfg.run_key})
    if not manifest:
        raise SystemExit(f"run_key {cfg.run_key!r} not found in base_universe_runs — "
                         "run scripts/build_base_universe.py --store first")
    snaps = list(db.base_universe.find({"run_key": cfg.run_key}).sort("date", 1))
    snap_dates = [s["date"].strftime("%Y-%m-%d") for s in snaps]
    snap_sets = [set(s["symbols"]) for s in snaps]
    all_syms = sorted(set().union(*snap_sets))
    start_dt = datetime.strptime(manifest["start"], "%Y-%m-%d")
    end_dt = datetime.strptime(manifest["end"], "%Y-%m-%d")
    print(f"universe: {len(snaps)} snapshots, {len(all_syms)} distinct symbols "
          f"[{manifest['start']}..{manifest['end']}]")
    print(f"params: fixed=₹{cfg.fixed:,.0f} entry_drop={cfg.entry_drop:.1%} "
          f"add_drop={cfg.add_drop:.1%} tp1={cfg.tp1:.1%} tp2={cfg.tp2:.1%} "
          f"max_adds={'∞' if cfg.max_adds < 0 else cfg.max_adds}")

    all_cycles, all_flows = [], []
    for sym in all_syms:
        series = close_series(db, sym, start_dt, end_dt)
        if len(series) < 2:
            print(f"  ⚠ no price data for {sym} — skipped")
            continue
        cycles, flows = run_symbol(sym, series, snap_dates, snap_sets, cfg)
        all_cycles += cycles
        all_flows += flows

    if not all_cycles:
        raise SystemExit("no trades produced")

    # ── aggregate ────────────────────────────────────────────────────────────
    all_flows.sort(key=lambda x: x[0])
    deployed, peak, peak_date = 0.0, 0.0, None
    daily_net = defaultdict(float)
    for ds, amt in all_flows:
        daily_net[ds] += amt
    deploy_curve = []
    for ds in sorted(daily_net):
        deployed += daily_net[ds]
        deploy_curve.append((ds, deployed))
        if deployed > peak:
            peak, peak_date = deployed, ds

    n = len(all_cycles)
    by_type = Counter(c["exit_type"] for c in all_cycles)
    lots_dist = Counter(c["lots"] for c in all_cycles)
    total_buys = sum(c["lots"] for c in all_cycles)
    invested = sum(c["invested"] for c in all_cycles)
    pnl_gross = sum(c["pnl_gross"] for c in all_cycles)
    pnl_net = sum(c["pnl_net"] for c in all_cycles)
    wins = sum(1 for c in all_cycles if c["pnl_net"] > 0)
    hold = sorted(c["holding_days"] for c in all_cycles)
    per_year = defaultdict(float)
    for c in all_cycles:
        per_year[c["exit_date"][:4]] += c["pnl_net"]
    pnl_by_type = defaultdict(float)
    for c in all_cycles:
        pnl_by_type[c["exit_type"]] += c["pnl_net"]

    print(f"\n── results ─────────────────────────────────────────────")
    print(f"cycles: {n}  buys: {total_buys}  turnover bought: ₹{invested:,.0f}")
    print(f"exits: " + "  ".join(f"{k}={v} (₹{pnl_by_type[k]:,.0f})"
                                 for k, v in by_type.most_common()))
    print(f"lots/cycle: " + "  ".join(f"{k}×{v}" for k, v in sorted(lots_dist.items())))
    print(f"win rate: {wins/n*100:.1f}%   avg net ret/cycle: "
          f"{sum(c['ret_net_pct'] for c in all_cycles)/n:.2f}%")
    print(f"holding days: median {hold[len(hold)//2]}  p90 {hold[int(len(hold)*0.9)]}  "
          f"max {hold[-1]}")
    print(f"P&L: gross ₹{pnl_gross:,.0f}   net ₹{pnl_net:,.0f}")
    print(f"peak capital deployed: ₹{peak:,.0f} on {peak_date}")
    print(f"net P&L / peak deployment: {pnl_net/peak*100:.1f}% "
          f"over {manifest['start'][:4]}–{manifest['end'][:4]}")
    print("net P&L by exit year: " + "  ".join(f"{y}: ₹{v:,.0f}"
                                               for y, v in sorted(per_year.items())))

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)
    import csv
    trades_path = os.path.join(out_dir, "dip_scale_trades.csv")
    with open(trades_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_cycles[0].keys()))
        w.writeheader()
        w.writerows(sorted(all_cycles, key=lambda c: c["entry_date"]))
    curve_path = os.path.join(out_dir, "dip_scale_deployment.csv")
    with open(curve_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "capital_deployed"])
        w.writerows(deploy_curve)
    print(f"\ntrades: {trades_path}\ndeployment curve: {curve_path}")


if __name__ == "__main__":
    main()
