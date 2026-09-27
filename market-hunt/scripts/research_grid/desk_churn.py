"""Weekly churn of the live fund model: what each Monday's ticket would have held, Oct 2020 -> Sep 2026.

Uses the production engine (fund.engine) exactly as the desk does — Monday checks, monthly reset on the first Monday of
each month, ticket lines netted per (sleeve, symbol), whole shares for a ₹45 L fund (sleeve sizes follow the model's
drifting weights between resets). Per Monday and per sleeve:
  lines (ticket rows = symbols traded with at least one share), buys, sells, rupees traded, turnover % of the sleeve.
Combined = the whole ticket. Also: forced index-exit trades that fall on other weekdays.

  .venv/Scripts/python.exe scripts/research_grid/desk_churn.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from fund.data import build_asset_market, build_market, charge_rates  # noqa: E402
from fund.engine.asset import Asset  # noqa: E402
from fund.engine.calendar import period_start_shifted, prev_session, trading_days, week_session  # noqa: E402
from fund.engine.costs import Costs  # noqa: E402
from fund.engine.fund import blend  # noqa: E402
from fund.engine.grid10 import Grid10  # noqa: E402
from fund.engine.tranche import Tranche  # noqa: E402
from fund.engine.types import SleeveState  # noqa: E402

START, END, FUND = date(2020, 10, 1), date(2026, 9, 25), 4_500_000
SLEEVES = [("Value core", "grid10", "NIFTY50", 0.28), ("Gold", "asset", "GOLDBEES", 0.08),
           ("Nasdaq-100", "asset", "MON100", 0.04), ("Growth", "tranche", "NIFTY200", 0.35),
           ("Satellite", "tranche", "NIFTY500MICRO750", 0.25)]


def replay_logged(book, m, costs, sched):
    """replay_sleeve, but keeping per-day orders, holdings after the day and the previous close's equity."""
    cal = trading_days(m.closes[m.bench], START, END)
    st = SleeveState(cash=1.0, params={})
    eq, log = {}, {}
    for i, d in enumerate(cal):
        prev = prev_session(cal, m.all_dates, i)
        eq_prev = st.cash + sum(st.qty(s) * m.cclose.at[prev, s] for s in st.lots)
        orders = book.on_day(st, m, costs, cal, i, prev, d in sched)
        eq[d] = st.cash + sum(st.qty(s) * m.cclose.at[d, s] for s in st.lots)
        if orders or d in sched:
            log[d] = {"orders": orders, "eq_prev": eq_prev, "held": {s: st.qty(s) for s in st.lots},
                      "close": {s: float(m.cclose.at[d, s]) for s in set(st.lots) | {o.symbol for o in orders}}}
    return pd.Series(eq), log, cal


def stats(x):
    x = np.asarray(x, dtype=float)
    return {"avg": float(x.mean()), "median": float(np.median(x)), "min": float(x.min()), "max": float(x.max())}


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    costs = Costs(*charge_rates(), 0.0005)
    curves, logs, cal = {}, {}, None
    for name, kind, uni, _ in SLEEVES:
        print("replaying", name, flush=True)
        if kind == "asset":
            m = build_asset_market(db, uni, START)
            c = trading_days(m.closes[m.bench], START, END)
            curves[name], logs[name], _ = replay_logged(Asset(uni), m, costs, set())
            continue
        m = build_market(db, uni, kind, START, date.today())
        c = trading_days(m.closes[m.bench], START, END)
        sched = period_start_shifted(c, "weekly", 0) if kind == "grid10" else week_session(c, 0)
        book = Grid10(10) if kind == "grid10" else Tranche()
        curves[name], logs[name], cal = replay_logged(book, m, costs, sched)
    mondays = sorted(period_start_shifted(cal, "weekly", 0))[1:]          # skip day one (the initial build)
    reset_days, seen = set(), set()
    for d in mondays:
        if (d.year, d.month) not in seen:
            seen.add((d.year, d.month))
            reset_days.add(d)
    weights = {n: w for n, _, _, w in SLEEVES}
    px = pd.DataFrame({k: v.reindex(cal).ffill() for k, v in curves.items()})
    fr = blend(px, weights, 0.0015, reset_days=reset_days)
    sv = fr.sleeve_values
    rows = []
    for d in mondays:
        i = cal.get_loc(d)
        prev = cal[i - 1]
        tot_prev = float(sv.loc[prev].sum())
        rec = {"date": d, "reset": d in reset_days, "lines": 0, "buys": 0, "sells": 0, "traded": 0.0}
        for name in weights:
            R = FUND * float(sv.loc[prev, name]) / tot_prev                  # ₹ in the sleeve before trading
            L = logs[name].get(d)
            net, px_ = {}, {}
            if L:
                scale = R / L["eq_prev"] if L["eq_prev"] > 0 else 0.0
                for o in L["orders"]:
                    net[o.symbol] = net.get(o.symbol, 0.0) + (o.value if o.side == "BUY" else -o.value) * scale
                px_ = dict(L["close"])
            if d in reset_days:
                before = float(sv.loc[prev, name]) * float(px.loc[d, name] / px.loc[prev, name])
                f = float(sv.loc[d, name]) / before if before > 0 else 1.0
                held = L["held"] if L else {}
                if not L:                                                    # asset sleeve: find its holding
                    last = max((k for k in logs[name] if k <= d), default=None)
                    held = logs[name][last]["held"] if last is not None else {}
                    px_ = {s: float(curves[name].loc[d]) for s in held}      # value per unit, not a share price
                for s, q in held.items():
                    val = R * (q * px_.get(s, 0) / max(sum(q2 * px_.get(s2, 0) for s2, q2 in held.items()), 1e-12))
                    net[s] = net.get(s, 0.0) + val * (f - 1)
            lines = {s: v for s, v in net.items() if abs(v) >= (px_.get(s) or 1.0) and abs(v) >= 1.0}
            buys = sum(1 for v in lines.values() if v > 0)
            traded = sum(abs(v) for v in lines.values())
            rec[f"{name}|lines"], rec[f"{name}|buys"], rec[f"{name}|sells"] = len(lines), buys, len(lines) - buys
            rec[f"{name}|traded"], rec[f"{name}|turnover"] = traded, traded / R * 100 if R > 0 else 0.0
            rec["lines"] += len(lines)
            rec["buys"] += buys
            rec["sells"] += len(lines) - buys
            rec["traded"] += traded
        rec["turnover"] = rec["traded"] / FUND * 100
        rows.append(rec)
    df = pd.DataFrame(rows).set_index("date")
    df.to_csv(HERE / "desk_churn_weekly.csv")
    last52 = df.loc[df.index >= df.index[-1] - pd.Timedelta(days=364)]
    out = {"weeks": len(df), "reset_weeks": int(df.reset.sum()), "per_sleeve": {}, "combined": {}}
    for name in weights:
        s = {k: stats(df[f"{name}|{k}"]) for k in ("lines", "buys", "sells", "traded", "turnover")}
        s["weeks_no_trade_pct"] = float((df[f"{name}|lines"] == 0).mean() * 100)
        s["normal_weeks_lines_avg"] = float(df.loc[~df.reset, f"{name}|lines"].mean())
        s["reset_weeks_lines_avg"] = float(df.loc[df.reset, f"{name}|lines"].mean())
        s["last52_lines_avg"] = float(last52[f"{name}|lines"].mean())
        s["last52_turnover_avg"] = float(last52[f"{name}|turnover"].mean())
        out["per_sleeve"][name] = s
    c = {k: stats(df[k]) for k in ("lines", "buys", "sells", "traded", "turnover")}
    c["weeks_no_trade_pct"] = float((df.lines == 0).mean() * 100)
    c["normal_weeks"] = {k: stats(df.loc[~df.reset, k]) for k in ("lines", "traded", "turnover")}
    c["reset_weeks"] = {k: stats(df.loc[df.reset, k]) for k in ("lines", "traded", "turnover")}
    c["last52"] = {k: stats(last52[k]) for k in ("lines", "traded", "turnover")}
    c["annual_turnover_pct"] = float(df.turnover.sum() / ((df.index[-1] - df.index[0]).days / 365.25))
    out["combined"] = c
    # forced index exits that fall on other weekdays
    other = {}
    for name in weights:
        n = sum(len([o for o in L["orders"]]) for d, L in logs[name].items() if d not in set(mondays) and d != cal[0])
        other[name] = n / ((cal[-1] - cal[0]).days / 365.25)
    out["off_monday_trades_per_year"] = other
    (HERE / "desk_churn.json").write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(json.dumps(out, indent=1, default=lambda x: round(float(x), 2)))


if __name__ == "__main__":
    main()
