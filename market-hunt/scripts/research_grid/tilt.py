"""Reversal TILT on point-in-time NIFTY 50: hold every member, overweight 22d losers, underweight winners.

Each month (first session), rank the members that have a 22-session ROC at the previous close and an
open today: score s = +1 for the biggest loser ... -1 for the biggest winner (linear in rank).
Target weight w = (1/M) * (1 + k*s), M = number ranked (sums to 1; k=1 gives the top winner 0%).
A stock trades only when its value is more than BAND of equity away from target (no-trade band):
sells first (winners trimmed = profit booked), then buys (losers topped up), buys scaled to cash.
Members that leave the index are sold at the next open. Next-open fills, statutory charges both
sides + slippage. k=0 is equal-weight NIFTY 50 paying the same costs — the fair benchmark.
Membership: NSE month-end files; prices: Mongo + crash_prices/ (local only, nothing written to Mongo).

  .venv/Scripts/python.exe scripts/research_grid/tilt.py [--ks 0,0.25,0.5,1] [--band 0.005]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from crash_test import build_intervals, load_prices, read_mcwb, window_dd  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index  # noqa: E402
from membership import members_as_of  # noqa: E402
from rotation_roc import rebalance_days, stats  # noqa: E402
from rs_trend import CAP, CRASHES, END, SLIP, START, WINDOWS, sub_cagr  # noqa: E402


def run_tilt(opens, closes, roc, ivs, k, band, buy_rate, sell_rate, slip=SLIP, capital=CAP, freq="monthly"):
    cal = closes.index[(closes.index >= pd.Timestamp(START)) & (closes.index <= pd.Timestamp(END))
                       & closes[BENCH].notna()]
    rebal = rebalance_days(cal, freq)
    cclose = closes.ffill()
    last_bar = {s: closes[s].last_valid_index() for s in closes.columns}
    cash, qty, traded, pend_exit, rows = capital, {}, 0.0, set(), []

    def sell(s, q, px):
        nonlocal cash, traded
        cash += q * px * (1 - slip) * (1 - sell_rate)
        traded += q * px

    def buy(s, v, px):
        nonlocal cash, traded
        q = v / (px * (1 + slip) * (1 + buy_rate))
        cash -= v
        traded += q * px
        return q

    for i, d in enumerate(cal):
        for s in list(pend_exit):                                   # left the index
            if s not in qty:
                pend_exit.discard(s); continue
            o = opens.at[d, s]
            if pd.isna(o):
                lb = last_bar.get(s)
                if lb is None or lb >= d:
                    continue
                o = closes.at[lb, s]
            sell(s, qty.pop(s), o); pend_exit.discard(s)

        if d in rebal or i == 0:
            prev = cal[i - 1] if i > 0 else closes.index[closes.index < d][-1]
            members = members_as_of(ivs, d.date())
            pool = [s for s in members if s in roc.columns and not pd.isna(roc.at[prev, s])
                    and not pd.isna(opens.at[d, s])]
            r = roc.loc[prev, pool].sort_values()                     # ascending: biggest loser first
            m = len(r)
            score = pd.Series(np.linspace(1, -1, m) if m > 1 else [0.0], index=r.index)
            w = (1 + k * score) / m
            px = {s: opens.at[d, s] for s in pool}
            equity = cash + sum(q * (opens.at[d, s] if not pd.isna(opens.at[d, s]) else cclose.at[prev, s])
                                for s, q in qty.items())
            tol = band * equity
            # a held member with no signal today (no ROC / no open) is simply left as it is
            delta = {s: equity * w.get(s, 0.0) - qty.get(s, 0.0) * px[s] for s in pool}
            for s, dv in sorted(delta.items(), key=lambda x: x[1]):     # sells first (most negative)
                if dv < -tol and s in qty:
                    dq = min(-dv / px[s], qty[s])
                    sell(s, dq, px[s]); qty[s] -= dq
                    if qty[s] <= 1e-9:
                        qty.pop(s)
            buys = {s: dv for s, dv in delta.items() if dv > tol}
            need = sum(buys.values())
            scale = min(1.0, cash / need) if need > 0 else 0.0
            for s, dv in buys.items():
                v = dv * scale
                if v > 1e-6:
                    qty[s] = qty.get(s, 0.0) + buy(s, v, px[s])

        held = sum(q * cclose.at[d, s] for s, q in qty.items())
        rows.append((d, cash + held, held, len(qty)))
        if i + 1 < len(cal):
            nxt = members_as_of(ivs, cal[i + 1].date())
            pend_exit |= {s for s in qty if s not in nxt}

    eq = pd.DataFrame(rows, columns=["date", "equity", "held", "n_held"]).set_index("date")
    return eq, traded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ks", default="0,0.25,0.5,1")
    ap.add_argument("--band", type=float, default=0.005)
    args = ap.parse_args()
    ks = [float(x) for x in args.ks.split(",")]
    ivs = build_intervals(read_mcwb(), START, END)
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    opens, closes = load_prices(db, sorted({iv.symbol for iv in ivs}))
    opens, closes = opens.loc[: pd.Timestamp(END)], closes.loc[: pd.Timestamp(END)]
    stocks = [c for c in closes.columns if c != BENCH]
    roc = closes[stocks].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    br, sr = charge_rates()

    curves, rep = {}, {"band": args.band, "books": {}, "windows": {}, "crashes": {}}
    for k in ks:
        eq, traded = run_tilt(opens, closes, roc, ivs, k, args.band, br, sr)
        name = f"k={k:g}"
        curves[name] = eq["equity"]
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        rep["books"][name] = {**stats(eq["equity"]), "turnover_pa": traded / eq["equity"].mean() / yrs,
                              "avg_invested": float((eq["held"] / eq["equity"]).mean()),
                              "avg_names": float(eq["n_held"].mean())}
    idx = curves[f"k={ks[0]:g}"].index
    ew = ew_index(closes, ivs).reindex(idx).ffill()
    curves["EW50 cost-free"] = CAP * ew / ew.iloc[0]
    curves["Nifty50"] = CAP * closes[BENCH].reindex(idx).ffill() / closes[BENCH].reindex(idx).ffill().iloc[0]
    for k in ["EW50 cost-free", "Nifty50"]:
        rep["books"][k] = stats(curves[k])
    for w, (a, b) in WINDOWS.items():
        rep["windows"][w] = {k: sub_cagr(c, a, b) for k, c in curves.items()}
    for n, (a, b) in CRASHES.items():
        rep["crashes"][n] = {k: window_dd(c, a, b) for k, c in curves.items()}
    yr = pd.DataFrame(curves).resample("YE").last()
    yr = pd.concat([pd.DataFrame(curves).iloc[[0]], yr]).pct_change().dropna()
    yr.index = yr.index.year
    out = HERE / "tilt"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / f"equity_band{args.band:g}.csv")
    yr.to_csv(out / f"yearly_band{args.band:g}.csv")
    (out / f"report_band{args.band:g}.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
