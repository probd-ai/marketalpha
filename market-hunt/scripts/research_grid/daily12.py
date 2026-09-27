"""Daily book capped at 12 names, Alpha on NIFTY200 point-in-time (user design).

Every session (signal at previous close, fills at the open):
  * tranches older than HOLD sessions expire (sold) — unless their stock is today's pick (rolled);
  * today's pick = the highest 20_day_return Alpha-eligible member whose weight is below MAX_W;
      - already held            -> add one tranche to it;
      - new and < MAX_NAMES held -> buy one tranche (new name);
      - new and book is full     -> sell the holding with the LOWEST 20_day_return, then buy the pick;
  * tranche = min(cash, equity / HOLD).
Assumptions (not in the user's spec, needed to make it well-defined): tranches expire after HOLD
sessions (supplies the daily cash), and a single stock is capped at MAX_W of equity.
Timing luck = loading starts 0..9 sessions later. Compared with the saved curves of the other designs.

  .venv/Scripts/python.exe scripts/research_grid/daily12.py
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
from daily_tranche import CAP, INDEX, SLIP  # noqa: E402
from grid_meanrev import BENCH, charge_rates, load_data  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402
from range_compare import START, WINDOWS  # noqa: E402

HOLD, MAX_NAMES, MAX_W = 21, 12, 0.25


def run_daily12(opens, closes, ret20, iv, elig, start, end, buy_rate, sell_rate, skip=0, rng=None,
                hold=HOLD, max_names=MAX_NAMES, max_w=MAX_W, slip=SLIP, capital=CAP, held_log=None):
    cal = closes.index[(closes.index >= pd.Timestamp(start)) & closes[BENCH].notna()]
    if end is not None:
        cal = cal[cal <= pd.Timestamp(end)]
    cal = cal[skip:]
    cclose = closes.ffill()
    last_bar = {s: closes[s].last_valid_index() for s in closes.columns}
    cash, traded, rows, replaced = capital, 0.0, [], 0
    tr: dict[str, list[list]] = {}                     # sym -> [[qty, age], ...]

    def q(s):
        return sum(t[0] for t in tr.get(s, []))

    def sell_all(s, px):
        nonlocal cash, traded
        cash += q(s) * px * (1 - slip) * (1 - sell_rate)
        traded += q(s) * px
        tr.pop(s)

    for i, d in enumerate(cal):
        prev = cal[i - 1] if i > 0 else closes.index[closes.index < d][-1]
        members = members_as_of(iv, d.date())
        for s in [s for s in tr if s not in members]:
            o = opens.at[d, s]
            if pd.isna(o):
                lb = last_bar.get(s)
                if lb is None or lb >= d:
                    continue
                o = closes.at[lb, s]
            sell_all(s, o)
        px = lambda s: opens.at[d, s] if not pd.isna(opens.at[d, s]) else cclose.at[prev, s]
        equity = cash + sum(q(s) * px(s) for s in tr)
        ok = elig.loc[prev] if prev in elig.index else pd.Series(dtype=bool)
        pool = [s for s in members if s in ret20.columns and bool(ok.get(s, False))
                and not pd.isna(ret20.at[prev, s]) and not pd.isna(opens.at[d, s])]
        ranked = (list(rng.permutation(sorted(pool))) if rng is not None
                  else list(ret20.loc[prev, pool].sort_values(ascending=False).index))
        pick = next((s for s in ranked if q(s) * px(s) < max_w * equity), None)

        for s in list(tr):                                        # expiries (rolled if it is the pick)
            if pd.isna(opens.at[d, s]):
                continue
            keep = [t for t in tr[s] if t[1] < hold]
            exp_q = q(s) - sum(t[0] for t in keep)
            if exp_q <= 0:
                continue
            if s == pick:
                tr[s] = keep + [[exp_q, 0]]                       # roll one expiring lot as today's add
                pick = "__rolled__"
                continue
            cash += exp_q * opens.at[d, s] * (1 - slip) * (1 - sell_rate)
            traded += exp_q * opens.at[d, s]
            tr[s] = keep
            if not keep:
                tr.pop(s)

        if pick and pick != "__rolled__":
            if pick not in tr and len(tr) >= max_names:           # full: replace the weakest name
                held_rank = {s: ret20.at[prev, s] for s in tr if s in ret20.columns and not pd.isna(ret20.at[prev, s])
                             and not pd.isna(opens.at[d, s])}
                if held_rank:
                    weakest = min(held_rank, key=held_rank.get)
                    sell_all(weakest, opens.at[d, weakest])
                    replaced += 1
            if pick in tr or len(tr) < max_names:
                v = min(cash, equity / hold, max(0.0, max_w * equity - q(pick) * px(pick)))
                if v > 1e-6:
                    p = opens.at[d, pick]
                    tr.setdefault(pick, []).append([v / (p * (1 + slip) * (1 + buy_rate)), 0])
                    cash -= v
                    traded += v
        for s in tr:
            for t in tr[s]:
                t[1] += 1
        held = sum(q(s) * cclose.at[d, s] for s in tr)
        rows.append((d, cash + held, len(tr)))
        if held_log is not None:
            held_log.append((d, frozenset(tr)))
    eq = pd.DataFrame(rows, columns=["date", "equity", "n_held"]).set_index("date")
    return eq, traded, replaced


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
    br, sr = charge_rates()

    curves, info = [], []
    for k in range(10):
        log = [] if k == 0 else None
        eq, traded, repl = run_daily12(opens, closes, ret20, iv, elig, START, None, br, sr, skip=k, held_log=log)
        curves.append(eq["equity"] / eq["equity"].iloc[0])
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        info.append((traded / eq["equity"].mean() / yrs, repl / yrs, eq["n_held"].iloc[25:].mean()))
        if log is not None:
            runs, cur = [], {}
            for _, held in log:
                for s in list(cur):
                    if s not in held:
                        runs.append(cur.pop(s))
                for s in held:
                    cur[s] = cur.get(s, 0) + 1
            runs = np.array(runs + list(cur.values()))
            names = np.array([len(h) for _, h in log])[25:]
    idx = curves[0].index
    df = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(curves)})
    df.to_csv(HERE / "range_daily12.csv")
    rnd = run_daily12(opens, closes, ret20, iv, elig, START, None, br, sr, rng=np.random.default_rng(0))[0]["equity"]
    others = {"Weekly book (5)": "range_weekly.csv", "Two books (7)": "range_two.csv",
              "Daily book (1)": "range_daily.csv", "Monthly book (12)": "range_monthly12.csv"}
    sets = {"Daily-12 (new)": df, **{k: pd.read_csv(HERE / v, index_col=0, parse_dates=True).reindex(idx)
                                     for k, v in others.items()}}

    def cagr(s):
        s = s.dropna()
        return (s.iloc[-1] / s.iloc[0]) ** (365.25 / (s.index[-1] - s.index[0]).days) - 1

    rep = {"daily12": {"turnover_pa": float(np.mean([x[0] for x in info])),
                       "replacements_pa": float(np.mean([x[1] for x in info])),
                       "names_avg": float(names.mean()), "names_min": int(names.min()), "names_max": int(names.max()),
                       "hold_avg_days": float(runs.mean()), "hold_median_days": float(np.median(runs)),
                       "hold_p75_days": float(np.percentile(runs, 75)), "random_pick_cagr_full": cagr(rnd) * 100}}
    for w, (a, b) in WINDOWS.items():
        rep[w] = {}
        for k, d in sets.items():
            sub = d.loc[a:b]
            cg = np.array([cagr(sub[c]) for c in sub.columns]) * 100
            dd = np.array([((sub[c] / sub[c].cummax()) - 1).min() for c in sub.columns]) * 100
            rep[w][k] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                         "dd_worst": dd.min(), "dd_best": dd.max()}
    (HERE / "daily12_report.json").write_text(json.dumps(rep, indent=2, default=float))
    print(json.dumps(rep["daily12"], indent=1, default=float))
    for w in WINDOWS:
        print(f"== {w}")
        for k, r in rep[w].items():
            print(f"   {k:18s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}   DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}")


if __name__ == "__main__":
    main()
