"""Level-cross tranche strategies on a TrueVX field (default mean_long), NIFTY50 point-in-time.

A strategy = entry levels + entry cross direction, exit levels + exit cross direction:
  S1 (recovery): buy on cross UP through 10/20/30/40, sell on cross DOWN through 70/80/90.
  S2 (opposite): buy on cross DOWN through 70/80/90, sell on cross UP through 10/20/30.
One tranche per entry level per stock (no doubling). Exit mode:
  all : the first qualifying exit cross sells every open tranche of that stock;
  one : each qualifying exit cross sells the OLDEST open tranche.
Stocks leaving the index are force-exited. Fixed rupee tranche = capital / (50 * #entry levels).
Next-open fills, statutory charges both sides + slippage. Controls as in grid_meanrev
(exposure-matched Nifty 50 and exposure-matched equal-weight PIT NIFTY50).

  .venv/Scripts/python.exe scripts/research_grid/grid_cross.py [--field mean_long]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid_meanrev import (BENCH, WINDOWS, Tranche, charge_rates, ew_index,  # noqa: E402
                          load_data, summarize)
from membership import load_membership, members_as_of, union_over  # noqa: E402

STRATEGIES = {
    "S1_recovery": {"entry": [10, 20, 30, 40], "entry_dir": "up", "exit": [70, 80, 90], "exit_dir": "down"},
    "S2_opposite": {"entry": [70, 80, 90], "entry_dir": "down", "exit": [10, 20, 30], "exit_dir": "up"},
}


def crossed(direction: str, level: int, p: float, s: float) -> bool:
    return p < level <= s if direction == "up" else p >= level > s


def run(opens, closes, score, intervals, start, end, capital, slip, buy_rate, sell_rate,
        entry, entry_dir, exit_levels, exit_dir, exit_mode):
    cal = closes.index[(closes.index >= pd.Timestamp(start)) & closes[BENCH].notna()]
    if end is not None:
        cal = cal[cal <= pd.Timestamp(end)]
    slot = capital / (50 * len(entry))
    last_bar = {s: closes[s].last_valid_index() for s in closes.columns}
    cclose = closes.ffill()
    cash = capital
    open_tr: dict[tuple[str, int], Tranche] = {}
    done: list[Tranche] = []
    pend_buy: list[tuple[str, int, str]] = []
    pend_sell: list[tuple[tuple[str, int], str]] = []
    eq_rows = []

    for i, d in enumerate(cal):
        keep = []
        for key, reason in pend_sell:
            tr = open_tr.get(key)
            if tr is None:
                continue
            o = opens.at[d, tr.symbol]
            if pd.isna(o):
                lb = last_bar.get(tr.symbol)
                if lb is not None and lb < d:
                    px, when = closes.at[lb, tr.symbol], lb
                else:
                    keep.append((key, reason)); continue
            else:
                px, when = o, d
            tr.proceeds = tr.qty * px * (1 - slip) * (1 - sell_rate)
            tr.exit_px, tr.exit_date, tr.reason = float(px), str(when.date()), reason
            bo = opens.at[d, BENCH]
            tr.bench_out = float(bo if not pd.isna(bo) else cclose.at[d, BENCH])
            cash += tr.proceeds
            done.append(open_tr.pop(key))
        pend_sell = keep

        members = members_as_of(intervals, d.date())
        keep = []
        for sym, lvl, sig in pend_buy:
            if (sym, lvl) in open_tr or sym not in members:
                continue
            o = opens.at[d, sym]
            if pd.isna(o):
                if last_bar.get(sym) is not None and last_bar[sym] >= d:
                    keep.append((sym, lvl, sig))
                continue
            px = o * (1 + slip)
            bo = opens.at[d, BENCH]
            open_tr[(sym, lvl)] = Tranche(sym, lvl, sig, str(d.date()), float(o),
                                          slot / (px * (1 + buy_rate)), slot,
                                          float(bo if not pd.isna(bo) else cclose.at[d, BENCH]))
            cash -= slot
        pend_buy = keep

        deployed = sum(t.qty * cclose.at[d, t.symbol] for t in open_tr.values())
        eq_rows.append((d, cash + deployed, deployed, len(open_tr), cclose.at[d, BENCH]))

        if i + 1 >= len(cal):
            break
        nxt = members_as_of(intervals, cal[i + 1].date())
        queued = {k for k, _ in pend_sell}
        for key, tr in open_tr.items():
            if key not in queued and tr.symbol not in nxt:
                pend_sell.append((key, "index_exit")); queued.add(key)

        if d not in score.index:
            continue
        pos = score.index.get_loc(d)
        if pos == 0:
            continue
        prev_row, row = score.iloc[pos - 1], score.iloc[pos]
        for sym in score.columns:
            p, s = prev_row[sym], row[sym]
            if pd.isna(p) or pd.isna(s):
                continue
            # exits (on held tranches)
            n_exit = sum(crossed(exit_dir, k, p, s) for k in exit_levels)
            if n_exit:
                held = sorted([k for k in open_tr if k[0] == sym and k not in queued],
                              key=lambda k: open_tr[k].entry_date)
                for k in (held if exit_mode == "all" else held[:n_exit]):
                    pend_sell.append((k, "grid")); queued.add(k)
            # entries (members only)
            if sym in nxt:
                for k in entry:
                    if crossed(entry_dir, k, p, s) and (sym, k) not in open_tr:
                        pend_buy.append((sym, k, str(d.date())))

    last = cal[-1]
    for tr in open_tr.values():
        tr.exit_date, tr.exit_px = str(last.date()), float(cclose.at[last, tr.symbol])
        tr.proceeds = tr.qty * tr.exit_px
        tr.bench_out, tr.reason = float(cclose.at[last, BENCH]), "open"
    eq = pd.DataFrame(eq_rows, columns=["date", "equity", "deployed", "n_open", "bench"]).set_index("date")
    return eq, done + list(open_tr.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="mean_long")
    ap.add_argument("--capital", type=float, default=10_000_000)
    ap.add_argument("--slip-bps", type=float, default=5.0)
    args = ap.parse_args()

    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    intervals = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(intervals, WINDOWS["full"][0], date.today()))
    opens, closes, score = load_data(db, ever, field=args.field)
    ew = ew_index(closes, intervals)
    br, sr = charge_rates()
    slip = args.slip_bps / 10_000
    out_dir = Path(__file__).with_name("grid_cross")
    out_dir.mkdir(exist_ok=True)

    report = {"field": args.field, "strategies": {}}
    for sname, cfg in STRATEGIES.items():
        for mode in ["all", "one"]:
            key = f"{sname}_{mode}"
            report["strategies"][key] = {}
            for w, (s, e) in WINDOWS.items():
                eq, trades = run(opens, closes, score, intervals, s, e, args.capital, slip, br, sr,
                                 cfg["entry"], cfg["entry_dir"], cfg["exit"], cfg["exit_dir"], mode)
                summ = summarize(eq, trades, args.capital, ew)
                summ["avg_open_tranches"] = float(eq["n_open"].mean())
                report["strategies"][key][w] = summ
                if w == "full":
                    eq.to_csv(out_dir / f"{args.field}_{key}_equity.csv")
                    pd.DataFrame([asdict(t) for t in trades]).to_csv(
                        out_dir / f"{args.field}_{key}_tranches.csv", index=False)
    (out_dir / f"{args.field}_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
