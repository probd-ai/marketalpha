"""Weekly top-5 tranches, each held 4 weeks, Alpha on NIFTY200 point-in-time.

On the first session of every week (signal at the previous close, fills at the open):
  * tranches bought 4 weeks ago expire;
  * the top TOP names by 22-session ROC among Alpha-eligible members each get a NEW tranche of
    equity / (TOP * WEEKS) — a name already held just gets another tranche (up to WEEKS tranches);
  * per symbol only the NET quantity trades (an expiring tranche rolled into a new one is not
    round-tripped). Sells first, then buys scaled to available cash.
Book size floats between TOP names (same leaders every week, 4 tranches each) and TOP*WEEKS names.
Members that leave the index are sold at the next open. Statutory charges both sides + slippage.
Controls: the live two-book design (start+mid, top 7), the daily one-stock plan, a random pick with the
same mechanics, equal-weight NIFTY200, Nifty 50; weekday luck = rebalance on session k (0..4) of the week.

  .venv/Scripts/python.exe scripts/research_grid/weekly_tranche.py
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
from daily_tranche import CAP, INDEX, SLIP, WINDOWS, run_daily, two_book  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from membership import members_as_of, load_membership, union_over  # noqa: E402
from rotation_roc import stats  # noqa: E402

TOP, WEEKS = 5, 4


def weekly_days(cal: pd.DatetimeIndex, k: int = 0) -> set:
    """The k-th session of every ISO week (or the last one if the week is shorter)."""
    s = pd.Series(cal, index=cal)
    return {g.iloc[min(k, len(g) - 1)] for _, g in s.groupby(cal.to_period("W"))}


def run_weekly(opens, closes, roc, iv, elig, start, end, buy_rate, sell_rate, k=0, rng=None,
               top=TOP, weeks=WEEKS, slip=SLIP, capital=CAP, held_log=None, slot_div=None, every=1, phase=0,
               book_log=None, schedule=None, max_w=None, trim_log=None, deploy_cash=False):
    """slot_div: tranche = equity / slot_div (default top*weeks). A smaller slot_div than top*weeks
    means cash runs out before tranches expire -> buying pauses until expiries free cash ("buy, then wait").
    every/phase: rebalance only every `every`-th week (phase picks which); `weeks` then counts REBALANCES,
    so an 8-week hold on a bi-weekly schedule is weeks=4.
    schedule: a rotation_roc cadence string (e.g. "monthly+3") that replaces the weekly calendar; `weeks` then
    counts those rebalances (monthly buys held 3 months = weeks=3).
    max_w: per-stock cap as a fraction of equity. New tranches are bought only up to the cap, and on every
    rebalance any holding above the cap is trimmed back to it (profit booked; every tranche shrinks pro rata).
    deploy_cash: after the new buys, leftover cash tops up current holdings best-ranked first, each only up to
    max_w (or to 1/top of equity if no cap) — booked as a fresh tranche, so it expires on the normal clock."""
    cal = closes.index[(closes.index >= pd.Timestamp(start)) & closes[BENCH].notna()]
    if end is not None:
        cal = cal[cal <= pd.Timestamp(end)]
    if schedule is not None:
        from rotation_roc import rebalance_days
        rebal = rebalance_days(cal, schedule)
    else:
        rebal = sorted(weekly_days(cal, k))
        rebal = set(rebal[phase::every])
    cclose = closes.ffill()
    last_bar = {s: closes[s].last_valid_index() for s in closes.columns}
    cash, traded, rows = capital, 0.0, []
    tr: dict[str, list[list]] = {}                 # sym -> [[qty, weeks_held], ...]

    def qty(s):
        return sum(t[0] for t in tr.get(s, []))

    for i, d in enumerate(cal):
        prev = cal[i - 1] if i > 0 else closes.index[closes.index < d][-1]
        members = members_as_of(iv, d.date())
        for s in [s for s in tr if s not in members]:            # left the index
            o = opens.at[d, s]
            if pd.isna(o):
                lb = last_bar.get(s)
                if lb is None or lb >= d:
                    continue
                o = closes.at[lb, s]
            q = qty(s)
            cash += q * o * (1 - slip) * (1 - sell_rate)
            traded += q * o
            tr.pop(s)

        if d in rebal:
            ok = elig.loc[prev] if prev in elig.index else pd.Series(dtype=bool)
            pool = [s for s in members if s in roc.columns and bool(ok.get(s, False))
                    and not pd.isna(roc.at[prev, s]) and not pd.isna(opens.at[d, s])]
            ranked = (list(rng.permutation(sorted(pool))) if rng is not None
                      else list(roc.loc[prev, pool].sort_values(ascending=False).index))
            picks = ranked[:top]
            px = lambda s: opens.at[d, s] if not pd.isna(opens.at[d, s]) else cclose.at[prev, s]
            equity = cash + sum(qty(s) * px(s) for s in tr)
            slot = equity / (slot_div or top * weeks)
            if max_w:                                                    # trim winners back to the cap
                for s in list(tr):
                    if pd.isna(opens.at[d, s]):
                        continue
                    v = qty(s) * opens.at[d, s]
                    if v > max_w * equity:
                        f = max_w * equity / v
                        sold = qty(s) * (1 - f)
                        cash += sold * opens.at[d, s] * (1 - slip) * (1 - sell_rate)
                        traded += sold * opens.at[d, s]
                        for t in tr[s]:
                            t[0] *= f
                        if trim_log is not None:
                            trim_log.append((d, s, v / equity))
            # target quantity per symbol after expiries + new tranches
            plan = {}
            for s in set(tr) | set(picks):
                keep = [t for t in tr.get(s, []) if t[1] < weeks]
                expiring = qty(s) - sum(t[0] for t in keep)
                new_q = slot / px(s) if s in picks else 0.0
                if max_w and new_q > 0:                                   # never buy past the cap
                    room = max_w * equity - sum(t[0] for t in keep) * px(s)
                    new_q = max(0.0, min(slot, room)) / px(s)
                plan[s] = (keep, expiring, new_q)
            for s, (keep, expiring, new_q) in plan.items():         # sells first
                net = new_q - expiring
                if net <= 0 and not pd.isna(opens.at[d, s]):          # net sell (or exact roll)
                    if net < 0:
                        cash += -net * opens.at[d, s] * (1 - slip) * (1 - sell_rate)
                        traded += -net * opens.at[d, s]
                    tr[s] = keep + ([[new_q, 0]] if new_q > 0 else [])
                    if not tr[s]:
                        tr.pop(s)
                elif net <= 0 and expiring > 0:                       # halted: expiring tranche waits
                    tr[s] = keep + [[expiring, weeks]]
            buys = {s: v for s, v in plan.items() if v[2] - v[1] > 0}
            need = sum((new_q - expiring) * opens.at[d, s] for s, (_, expiring, new_q) in buys.items())
            scale = min(1.0, cash / (need * (1 + slip) * (1 + buy_rate))) if need > 0 else 0.0
            for s, (keep, expiring, new_q) in buys.items():
                net = (new_q - expiring) * scale
                cost = net * opens.at[d, s] * (1 + slip) * (1 + buy_rate)
                cash -= cost
                traded += net * opens.at[d, s]
                tr[s] = keep + [[expiring + net, 0]]
            if deploy_cash and cash > 0.005 * equity:
                limit = (max_w or 1.0 / top) * equity
                held_ranked = [s for s in ranked if s in tr] + [s for s in tr if s not in ranked]
                for s in held_ranked:
                    if cash <= 0.005 * equity:
                        break
                    if pd.isna(opens.at[d, s]):
                        continue
                    room = limit - qty(s) * opens.at[d, s]
                    v = min(cash / (1 + slip) / (1 + buy_rate), room)
                    if v > 1e-6:
                        q_add = v / opens.at[d, s]
                        cash -= v * (1 + slip) * (1 + buy_rate)
                        traded += v
                        tr[s].append([q_add, 0])
            for s in tr:                                               # age every tranche one week
                for t in tr[s]:
                    t[1] += 1
        held = sum(qty(s) * cclose.at[d, s] for s in tr)
        rows.append((d, cash + held, len(tr)))
        if held_log is not None:
            held_log.append((d, frozenset(tr)))
        if book_log is not None:
            book_log.append((d, cash, {s: qty(s) * cclose.at[d, s] for s in tr}))
    eq = pd.DataFrame(rows, columns=["date", "equity", "n_held"]).set_index("date")
    return eq, traded


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
    roc22 = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    ret20 = closes[cols].apply(lambda c: c / c.dropna().shift(19).reindex(c.index) - 1)
    ew = ew_index(closes, iv)
    br, sr = charge_rates()

    rep = {}
    for w, (a, b) in WINDOWS.items():
        eq, traded = run_weekly(opens, closes, roc22, iv, elig, a, b, br, sr)
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        luck = [stats(run_weekly(opens, closes, roc22, iv, elig, a, b, br, sr, k=k)[0]["equity"]) for k in range(5)]
        rnd = [stats(run_weekly(opens, closes, roc22, iv, elig, a, b, br, sr, rng=np.random.default_rng(s))[0]["equity"])
               for s in range(5)]
        daily = stats(run_daily(opens, closes, ret20, iv, elig, a, b, br, sr)[0]["equity"])
        tb = stats(two_book(opens, closes, ret20, iv, elig, a, b, br, sr))
        e = ew.reindex(eq.index).ffill()
        nh = eq["n_held"].iloc[25:] if len(eq) > 25 else eq["n_held"]
        rep[w] = {"period": f"{eq.index[0].date()} -> {eq.index[-1].date()}",
                  "weekly": {**stats(eq["equity"]), "turnover_pa": traded / eq["equity"].mean() / yrs,
                             "names_avg": float(nh.mean()), "names_min": int(nh.min()), "names_max": int(nh.max())},
                  "weekday_luck": {"total_min": min(x["total_return"] for x in luck),
                                   "total_max": max(x["total_return"] for x in luck),
                                   "cagr_min": min(x["cagr"] for x in luck), "cagr_max": max(x["cagr"] for x in luck)},
                  "random_pick": {"total_mean": float(np.mean([x["total_return"] for x in rnd])),
                                  "cagr_mean": float(np.mean([x["cagr"] for x in rnd]))},
                  "daily_plan": daily, "two_book": tb, "ew_nifty200": stats(e),
                  "nifty50": stats(closes[BENCH].reindex(eq.index).ffill())}
        if w.startswith("full"):
            eq.to_csv(HERE / "weekly_tranche_equity.csv")
    (HERE / "weekly_tranche_report.json").write_text(json.dumps(rep, indent=2, default=str))
    for w, x in rep.items():
        wk, lk = x["weekly"], x["weekday_luck"]
        print(f"== {w} ({x['period']})")
        print(f"   weekly top-5  total {wk['total_return'] * 100:7.1f}%  cagr {wk['cagr'] * 100:5.1f}%  dd {wk['max_dd'] * 100:6.1f}%  "
              f"sh {wk['sharpe']:.2f}  turn {wk['turnover_pa']:.1f}x  names {wk['names_avg']:.1f} [{wk['names_min']}-{wk['names_max']}]")
        print(f"   weekday luck: total {lk['total_min'] * 100:.1f}% .. {lk['total_max'] * 100:.1f}%   random: total {x['random_pick']['total_mean'] * 100:.1f}%")
        for k in ["daily_plan", "two_book", "ew_nifty200", "nifty50"]:
            s = x[k]
            print(f"   {k:12s} total {s['total_return'] * 100:7.1f}%  cagr {s['cagr'] * 100:5.1f}%  dd {s['max_dd'] * 100:6.1f}%  sh {s['sharpe']:.2f}")


if __name__ == "__main__":
    main()
