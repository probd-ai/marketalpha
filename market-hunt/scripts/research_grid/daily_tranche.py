"""Daily one-stock tranches ("21 overlapping monthly books"), Alpha on NIFTY200 point-in-time.

Every session:
  1. the tranche bought HOLD sessions ago expires and is sold at today's open — unless that stock is
     today's top pick, in which case it is rolled (kept, new HOLD clock) with no trade;
  2. buy ONE new stock at today's open: the highest 20_day_return (close / close 19 sessions earlier, at
     the previous close) among Alpha-eligible members that are not already held;
  3. size = min(cash, equity / HOLD) → the book settles at ~HOLD names; the first HOLD sessions load it.
ascending=True ranks lowest first (a reversal / value book: buy the biggest recent LOSER each day).
Members that leave the index are sold at the next open. Statutory charges both sides + slippage.
Controls: the live two-book design (start + mid, top 7 each), a random pick inside the Alpha-eligible
pool, equal-weight PIT NIFTY200, Nifty 50; and start-day luck (loading begins 0..4 sessions later).

  .venv/Scripts/python.exe scripts/research_grid/daily_tranche.py
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
sys.path.insert(0, str(HERE))
from alpha_test import RULES, load_means  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402
from rotation_roc import run, stats  # noqa: E402

INDEX, HOLD, SLIP, CAP = "NIFTY200", 21, 0.0005, 1_700_000
WINDOWS = {"full 2020-10 -> 2026-09": ("2020-10-01", None), "P1 2020-10 -> 2023-06": ("2020-10-01", "2023-06-30"),
           "P2 2023-07 -> 2026-09": ("2023-07-01", None), "2022-01 -> 2024-01": ("2022-01-01", "2024-01-01"),
           "2022-01 -> 2026-09": ("2022-01-01", None), "2026 YTD": ("2026-01-01", None)}


def run_daily(opens, closes, ret20, iv, elig, start, end, buy_rate, sell_rate, hold=HOLD, slip=SLIP,
              capital=CAP, rng=None, skip=0, held_log=None, ascending=False):
    cal = closes.index[(closes.index >= pd.Timestamp(start)) & closes[BENCH].notna()]
    if end is not None:
        cal = cal[cal <= pd.Timestamp(end)]
    cal = cal[skip:]
    cclose = closes.ffill()
    last_bar = {s: closes[s].last_valid_index() for s in closes.columns}
    cash, pos, traded, rows = capital, {}, 0.0, []           # pos: sym -> [qty, age_sessions]

    def sell(s, px):
        nonlocal cash, traded
        q = pos.pop(s)[0]
        cash += q * px * (1 - slip) * (1 - sell_rate)
        traded += q * px

    for i, d in enumerate(cal):
        prev = cal[i - 1] if i > 0 else closes.index[closes.index < d][-1]
        members = members_as_of(iv, d.date())
        for s in [s for s in pos if s not in members]:        # left the index
            o = opens.at[d, s]
            if pd.isna(o):
                lb = last_bar.get(s)
                if lb is None or lb >= d:
                    continue
                o = closes.at[lb, s]
            sell(s, o)
        ok = elig.loc[prev] if prev in elig.index else pd.Series(dtype=bool)
        pool = [s for s in members if s in ret20.columns and bool(ok.get(s, False))
                and not pd.isna(ret20.at[prev, s]) and not pd.isna(opens.at[d, s])]
        ranked = (list(rng.permutation(sorted(pool))) if rng is not None
                  else list(ret20.loc[prev, pool].sort_values(ascending=ascending).index))
        expiring = [s for s, (q, age) in pos.items() if age >= hold]
        top_unheld = next((s for s in ranked if s not in pos or s in expiring), None)
        for s in expiring:
            if s == top_unheld:
                pos[s][1] = 0                                  # roll: still the best pick, no trade
            elif not pd.isna(opens.at[d, s]):
                sell(s, opens.at[d, s])
        rolled = top_unheld in pos and pos[top_unheld][1] == 0
        if not rolled:
            pick = next((s for s in ranked if s not in pos), None)
            if pick is not None:
                equity = cash + sum(q * (opens.at[d, s] if not pd.isna(opens.at[d, s]) else cclose.at[prev, s])
                                    for s, (q, _) in pos.items())
                v = min(cash, equity / hold)
                if v > 1e-6:
                    px = opens.at[d, pick]
                    pos[pick] = [v / (px * (1 + slip) * (1 + buy_rate)), 0]
                    cash -= v
                    traded += v
        for s in pos:
            pos[s][1] += 1
        held = sum(q * cclose.at[d, s] for s, (q, _) in pos.items())
        rows.append((d, cash + held, len(pos)))
        if held_log is not None:
            held_log.append((d, frozenset(pos)))
    eq = pd.DataFrame(rows, columns=["date", "equity", "n_held"]).set_index("date")
    return eq, traded


def two_book(opens, closes, ret20, iv, elig, a, b, br, sr):
    tot = None
    for fq in ["monthly+1", "monthly_mid+1"]:
        eq, _ = run(opens, closes, ret20, iv, date.fromisoformat(a), None if b is None else date.fromisoformat(b),
                    7, "alpha", fq, CAP / 2, SLIP, br, sr, eligible=elig)
        tot = eq["equity"] if tot is None else tot + eq["equity"]
    return tot


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, INDEX, date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, score = load_data(db, ever)
    fields = {"truevx_score": score, **load_means(db, ever)}
    cols = [c for c in closes.columns if c != BENCH]
    elig = pd.DataFrame(True, index=closes.index, columns=cols)
    for f, thr in RULES.items():
        elig &= (fields[f].reindex(index=closes.index, columns=cols) > thr).fillna(False)
    ret20 = closes[cols].apply(lambda c: c / c.dropna().shift(19).reindex(c.index) - 1)
    ew = ew_index(closes, iv)
    br, sr = charge_rates()

    rep = {}
    for w, (a, b) in WINDOWS.items():
        eq, traded = run_daily(opens, closes, ret20, iv, elig, a, b, br, sr)
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        luck = [stats(run_daily(opens, closes, ret20, iv, elig, a, b, br, sr, skip=k)[0]["equity"]) for k in range(5)]
        rnd = [stats(run_daily(opens, closes, ret20, iv, elig, a, b, br, sr, rng=np.random.default_rng(k))[0]["equity"])
               for k in range(5)]
        tb = two_book(opens, closes, ret20, iv, elig, a, b, br, sr)
        e = ew.reindex(eq.index).ffill()
        rep[w] = {"period": f"{eq.index[0].date()} -> {eq.index[-1].date()}",
                  "daily": {**stats(eq["equity"]), "turnover_pa": traded / eq["equity"].mean() / yrs,
                            "avg_names": float(eq["n_held"].iloc[HOLD:].mean()) if len(eq) > HOLD else None},
                  "daily_start_luck": {"total_min": min(x["total_return"] for x in luck),
                                       "total_max": max(x["total_return"] for x in luck)},
                  "daily_random_pick": {"total_mean": float(np.mean([x["total_return"] for x in rnd])),
                                        "cagr_mean": float(np.mean([x["cagr"] for x in rnd]))},
                  "two_book_engine_schedule": stats(tb), "ew_nifty200": stats(e),
                  "nifty50": stats(closes[BENCH].reindex(eq.index).ffill())}
        if w.startswith("full"):
            pd.DataFrame({"daily_tranche": eq["equity"], "two_book": tb.reindex(eq.index),
                          "ew200": CAP * e / e.iloc[0]}).to_csv(HERE / "daily_tranche_equity.csv")
    (HERE / "daily_tranche_report.json").write_text(json.dumps(rep, indent=2, default=str))
    for w, x in rep.items():
        d, t = x["daily"], x["two_book_engine_schedule"]
        print(f"== {w} ({x['period']})")
        print(f"   daily tranche  total {d['total_return'] * 100:7.1f}%  cagr {d['cagr'] * 100:5.1f}%  dd {d['max_dd'] * 100:6.1f}%  "
              f"sh {d['sharpe']:.2f}  turn {d['turnover_pa']:.1f}x  names {d['avg_names']}")
        print(f"   start-day luck (5 starts): {x['daily_start_luck']['total_min'] * 100:.1f}% .. {x['daily_start_luck']['total_max'] * 100:.1f}%"
              f"   random pick: total {x['daily_random_pick']['total_mean'] * 100:.1f}% cagr {x['daily_random_pick']['cagr_mean'] * 100:.1f}%")
        print(f"   two-book (engine sched) total {t['total_return'] * 100:7.1f}%  cagr {t['cagr'] * 100:5.1f}%  dd {t['max_dd'] * 100:6.1f}%  sh {t['sharpe']:.2f}")
        print(f"   EW N200 cagr {x['ew_nifty200']['cagr'] * 100:5.1f}%   Nifty50 cagr {x['nifty50']['cagr'] * 100:5.1f}%")


if __name__ == "__main__":
    main()
