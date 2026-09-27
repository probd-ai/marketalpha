"""Fund-level (between-sleeve) rebalance policy study on the live engine, Oct 2020 -> Sep 2026.

Sleeves are replayed once with the production engine (Monday checks). Only the fund layer changes:
  monthly (current) · quarterly · half-yearly · yearly · never · drift bands (checked on the first Monday of each month,
  rebalance only when some sleeve is more than X points from its target weight).
Reports return, worst fall, Sharpe, rebalances per year, extra ticket lines per rebalance (holdings touched with at
least one share at a ₹45 L fund), rupees moved, and how far the sleeves drift from target.

  .venv/Scripts/python.exe scripts/research_grid/desk_reset_policy.py
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from desk_churn import END, FUND, SLEEVES, START, replay_logged  # noqa: E402
from fund.data import build_asset_market, build_market, charge_rates  # noqa: E402
from fund.engine.asset import Asset  # noqa: E402
from fund.engine.calendar import period_start_shifted, trading_days, week_session  # noqa: E402
from fund.engine.costs import Costs  # noqa: E402
from fund.engine.grid10 import Grid10  # noqa: E402
from fund.engine.tranche import Tranche  # noqa: E402

COST = 0.0015
WINDOWS = {"Full": ("2020-10-01", "2026-09-25"), "2020-23": ("2020-10-01", "2023-06-30"),
           "2023-26": ("2023-07-01", "2026-09-25"), "Last 12m": ("2025-09-25", "2026-09-25")}


def run_policy(px: pd.DataFrame, w: dict, check_days: set, band):
    """band None -> rebalance on every check day; float -> only when max |weight - target| > band;
    ("hybrid", band_days, x) -> always on check_days, plus on band_days whenever drift > x."""
    cols = list(w)
    r = px[cols].pct_change().fillna(0).to_numpy()
    tgt = np.array([w[c] for c in cols])
    v = tgt.copy()
    out, drift, resets, moved = [1.0], [0.0], [], 0.0
    for i in range(1, len(px)):
        v = v * (1 + r[i])
        tot = v.sum()
        d = px.index[i]
        hyb = isinstance(band, tuple)
        dev = np.abs(v / tot - tgt).max()
        due = (d in check_days and (band is None or hyb or dev > band)) or             (hyb and d in band[1] and dev > band[2])
        if due:
            if True:
                m = np.abs(v - tgt * tot).sum() / 2
                moved += m / tot
                tot -= m * COST
                resets.append((d, v / v.sum()))
                v = tgt * tot
        out.append(tot)
        drift.append(float(np.abs(v / v.sum() - tgt).max()))
    return pd.Series(out, index=px.index), pd.Series(drift, index=px.index), resets, moved


def perf(s):
    s = s.dropna()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return {"cagr": ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100 if yrs > 0.9 else (s.iloc[-1] / s.iloc[0] - 1) * 100,
            "dd": float((s / s.cummax() - 1).min()) * 100, "sharpe": float(r.mean() / r.std() * math.sqrt(252))}


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    costs = Costs(*charge_rates(), 0.0005)
    curves, logs, cal = {}, {}, None
    for name, kind, uni, _ in SLEEVES:
        print("replaying", name, flush=True)
        if kind == "asset":
            m = build_asset_market(db, uni, START)
            curves[name], logs[name], _ = replay_logged(Asset(uni), m, costs, set())
            continue
        m = build_market(db, uni, kind, START, date.today())
        c = trading_days(m.closes[m.bench], START, END)
        sched = period_start_shifted(c, "weekly", 0) if kind == "grid10" else week_session(c, 0)
        curves[name], logs[name], cal = replay_logged(Grid10(10) if kind == "grid10" else Tranche(), m, costs, sched)
    w = {n: wt for n, _, _, wt in SLEEVES}
    px = pd.DataFrame({k: v.reindex(cal).ffill() for k, v in curves.items()})
    mondays = sorted(period_start_shifted(cal, "weekly", 0))
    first_mon = []
    seen = set()
    for d in mondays:
        if (d.year, d.month) not in seen:
            seen.add((d.year, d.month))
            first_mon.append(d)
    first_mon = first_mon[1:]
    pol = {
        "Monthly (current)": (set(first_mon), None),
        "Quarterly (Jan/Apr/Jul/Oct)": ({d for d in first_mon if d.month in (1, 4, 7, 10)}, None),
        "Quarterly (Feb/May/Aug/Nov)": ({d for d in first_mon if d.month in (2, 5, 8, 11)}, None),
        "Quarterly (Mar/Jun/Sep/Dec)": ({d for d in first_mon if d.month in (3, 6, 9, 12)}, None),
        "Half-yearly (Jan/Jul)": ({d for d in first_mon if d.month in (1, 7)}, None),
        "Half-yearly (Apr/Oct)": ({d for d in first_mon if d.month in (4, 10)}, None),
        "Yearly (Jan)": ({d for d in first_mon if d.month == 1}, None),
        "Never": (set(), None),
        "Half-yearly (Feb/Aug)": ({d for d in first_mon if d.month in (2, 8)}, None),
        "Half-yearly (Mar/Sep)": ({d for d in first_mon if d.month in (3, 9)}, None),
        "Yearly (Apr)": ({d for d in first_mon if d.month == 4}, None),
        "Yearly (Jul)": ({d for d in first_mon if d.month == 7}, None),
        "Yearly (Oct)": ({d for d in first_mon if d.month == 10}, None),
        "Band 3 pts (checked monthly)": (set(first_mon), 0.03),
        "Band 5 pts (checked monthly)": (set(first_mon), 0.05),
    }
    for label, months in (("Half-yearly Jan/Jul", (1, 7)), ("Yearly Jan", (1,))):
        cal_days = {d for d in first_mon if d.month in months}
        pol[f"{label} + 5-pt safety band"] = (cal_days, ("hybrid", set(first_mon), 0.05))
    yrs = (cal[-1] - cal[0]).days / 365.25
    res = {}
    for name, (days, band) in pol.items():
        eq, drift, resets, moved = run_policy(px, w, days, band)
        lines = []
        for d, _ in resets:                                  # holdings touched by the resize, >= 1 share at ₹45 L
            n = 0
            for s in w:
                L = logs[s].get(d) or logs[s][max(k for k in logs[s] if k <= d)]
                n += len(L["held"])
            lines.append(n)
        row = {k: perf(eq.loc[a:b]) for k, (a, b) in WINDOWS.items()}
        row.update({"resets_per_year": len(resets) / yrs, "lines_per_reset": float(np.mean(lines)) if lines else 0.0,
                    "extra_lines_per_year": sum(lines) / yrs, "moved_pct_per_year": moved / yrs * 100,
                    "drift_max_pts": float(drift.max() * 100), "drift_avg_pts": float(drift.mean() * 100)})
        # weights just before the end with no further reset
        res[name] = row
        f = row["Full"]
        print(f"{name:30s} CAGR {f['cagr']:5.1f}  DD {f['dd']:6.1f}  Sharpe {f['sharpe']:.2f}  "
              f"2020-23 {row['2020-23']['cagr']:5.1f}  2023-26 {row['2023-26']['cagr']:5.1f}  12m {row['Last 12m']['cagr']:5.1f}  "
              f"resets/yr {row['resets_per_year']:4.1f}  extra lines/yr {row['extra_lines_per_year']:5.0f}  "
              f"drift max {row['drift_max_pts']:4.1f} avg {row['drift_avg_pts']:4.1f} pts")
    (HERE / "desk_reset_policy.json").write_text(json.dumps(res, indent=2, default=float), encoding="utf-8")
    # how far does each sleeve drift with no rebalancing at all?
    eq, _, _, _ = run_policy(px, w, set(), None)
    grow = px / px.iloc[0]
    wv = grow.mul(pd.Series(w)).div(grow.mul(pd.Series(w)).sum(axis=1), axis=0) * 100
    print("never-rebalanced weights at the end:", wv.iloc[-1].round(1).to_dict())
    print("max weight reached if never rebalanced:", wv.max().round(1).to_dict(), " min:", wv.min().round(1).to_dict())


if __name__ == "__main__":
    main()
