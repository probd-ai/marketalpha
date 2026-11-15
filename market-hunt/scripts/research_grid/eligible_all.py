"""Stage 1 of the low-range breakout idea: hold EVERY Growth-eligible name instead of the top N.

Selection (monthly, signal = last session of the previous month, trade = first session of the month at the open):
  base pool   point-in-time NIFTY200 members on the trade day
  eligible    Growth/Alpha filter on the signal close: truevx_score>90, mean_short>80, mean_mid>60, mean_long>50
  filtration  eligible names that are ALSO point-in-time members of a second index (variants below)
  book        all of them, equal weight; zero eligible -> cash

Variants: N200 alone, N200 ∩ NIFTY50, N200 ∩ NIFTY100, N200 ∩ NIFTYMIDCAP150.
Controls (same pool, same months, same costs):
  EW-pool   equal weight on every member of the variant's pool (what the filter has to beat)
  random    the same number of names drawn at random from the pool each month, SEEDS draws -> p5 / p50 / p95
A holding that leaves the pool mid-month is sold at the next open (last close once it stops trading).
Costs: statutory charges from BrokerageCalculator + 5 bp slippage on every traded rupee. Rf = 0 in Sharpe.

  .venv/Scripts/python.exe scripts/research_grid/eligible_all.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from fund.data import BENCH, charge_rates, load_from, load_prices, load_truevx  # noqa: E402
from fund.engine.calendar import period_start_shifted, trading_days  # noqa: E402
from fund.engine.signals import alpha_eligible  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402

START = date(2020, 10, 1)            # PIT membership floor for all universes (fund.engine.fund.MODEL_START_FLOOR)
BASE = "NIFTY200"
FILTERS = [None, "NIFTY50", "NIFTY100", "NIFTYMIDCAP150"]
SLIP = 0.0005
SEEDS = 200
SPLIT = pd.Timestamp("2023-10-01")   # halves for regime stability
OUT = HERE / "eligible_all_results.json"


def stats(eq: pd.Series) -> dict:
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(252)
    dd = (eq / eq.cummax() - 1).min()
    return {"cagr": cagr, "vol": vol, "sharpe": (r.mean() * 252) / vol if vol > 0 else float("nan"), "maxdd": dd}


def halves(eq: pd.Series) -> tuple[float, float]:
    a, b = eq[eq.index < SPLIT], eq[eq.index >= SPLIT]
    return stats(a)["cagr"], stats(b)["cagr"]


def simulate(cal, opens, closes, last_bar, rebal: list, picks: dict, pool_of, buy_rate, sell_rate) -> dict:
    """Equal-weight book over `picks[d]` (list of symbols) rebalanced at the open of each d in `rebal`."""
    cash, sh = 1.0, {}
    eq, turnover, cost = [], 0.0, 0.0
    cl = closes.ffill()
    rset = set(rebal)

    def sell(s, q, px):
        nonlocal cash, turnover, cost
        v = q * px
        cash += v * (1 - SLIP) * (1 - sell_rate)
        turnover += v
        cost += v * (1 - (1 - SLIP) * (1 - sell_rate))

    for d in cal:
        # mid-month: a holding that left the pool is sold at today's open (or its last close once it stopped trading)
        if d not in rset and sh:
            pool = pool_of(d.date())
            for s in [s for s in sh if s not in pool]:
                o = opens.at[d, s]
                if pd.isna(o):
                    lb = last_bar.get(s)
                    if lb is None or lb >= d:
                        continue
                    o = closes.at[lb, s]
                sell(s, sh.pop(s), o)
        if d in rset:
            tgt = [s for s in picks[d]]
            px = {s: opens.at[d, s] for s in set(sh) | set(tgt)}
            tradable = {s for s, p in px.items() if not pd.isna(p)}
            frozen = {s: sh[s] for s in sh if s not in tradable}          # halted holding: keep, value at last close
            frozen_v = sum(q * cl.at[d, s] for s, q in frozen.items() if not pd.isna(cl.at[d, s]))
            equity = cash + frozen_v + sum(q * px[s] for s, q in sh.items() if s in tradable)
            tgt = [s for s in tgt if s in tradable]
            w = (equity - frozen_v) / len(tgt) if tgt else 0.0
            want = {s: w / px[s] for s in tgt}
            for s in sorted(sh):                                           # sells first
                if s in frozen:
                    continue
                dq = sh[s] - want.get(s, 0.0)
                if dq > 1e-12:
                    sell(s, dq, px[s])
                    sh[s] -= dq
                    if sh[s] <= 1e-12:
                        sh.pop(s)
            buys = {s: (want[s] - sh.get(s, 0.0)) * px[s] for s in tgt if want[s] - sh.get(s, 0.0) > 1e-12}
            need = sum(buys.values()) * (1 + SLIP) * (1 + buy_rate)
            scale = min(1.0, cash / need) if need > 0 else 0.0                 # sell costs leave a hair short
            for s, v in buys.items():
                v *= scale
                cash -= v * (1 + SLIP) * (1 + buy_rate)
                turnover += v
                cost += v * ((1 + SLIP) * (1 + buy_rate) - 1)
                sh[s] = sh.get(s, 0.0) + v / px[s]
        eq.append(cash + sum(q * cl.at[d, s] for s, q in sh.items() if not pd.isna(cl.at[d, s])))
    e = pd.Series(eq, index=cal)
    yrs = (cal[-1] - cal[0]).days / 365.25
    return {"eq": e, "turnover_yr": turnover / yrs / e.mean(), "cost_yr": cost / yrs / e.mean()}


def month_range_pct(db, symbols: list[str], month_end: pd.Timestamp) -> dict[str, float]:
    """(high - low) / close of the calendar month ending at `month_end` — the stage-2 ranking preview."""
    start = datetime(month_end.year, month_end.month, 1)
    end = datetime.combine(month_end.date(), datetime.max.time())
    rows = []
    for c in (c for c in db.list_collection_names() if c.startswith("prices_")):
        rows += list(db[c].find({"symbol": {"$in": symbols}, "date": {"$gte": start, "$lte": end}},
                                {"_id": 0, "symbol": 1, "date": 1, "high_price": 1, "low_price": 1, "close_price": 1}))
    if not rows:
        return {}
    df = pd.DataFrame(rows).sort_values("date")
    g = df.groupby("symbol")
    return ((g["high_price"].max() - g["low_price"].min()) / g["close_price"].last()).to_dict()


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    today = date.today()
    ivs = {u: load_membership(db, u, today) for u in [BASE] + [f for f in FILTERS if f]}
    syms = sorted(union_over(ivs[BASE], START, today))
    since = load_from(START)
    opens, closes = load_prices(db, syms, since)
    cols = [c for c in closes.columns if c != BENCH]
    tv = load_truevx(db, cols, since)
    elig = alpha_eligible(tv, closes.index, cols)
    cal = trading_days(closes[BENCH], START)
    rebal = sorted(period_start_shifted(cal, "monthly", 0))
    prev = {d: closes.index[closes.index < d][-1] for d in rebal}
    last_bar = {s: closes[s].last_valid_index() for s in cols}
    buy_rate, sell_rate = charge_rates()
    print(f"data {cal[0].date()} -> {cal[-1].date()} · {len(rebal)} monthly rebalances · {len(cols)} symbols")

    results, counts = {}, {}
    for f in FILTERS:
        name = f"N200" + (f"&{f.replace('NIFTY', 'N')}" if f else "")

        def pool_of(day, f=f):
            p = members_as_of(ivs[BASE], day)
            return p & members_as_of(ivs[f], day) if f else p

        pools = {d: sorted(s for s in pool_of(d.date()) if s in elig.columns) for d in rebal}
        picks = {d: [s for s in pools[d] if bool(elig.at[prev[d], s])] for d in rebal}
        n = [len(picks[d]) for d in rebal]
        counts[name] = {"avg": float(np.mean(n)), "min": int(min(n)), "max": int(max(n)),
                        "zero_months": int(sum(x == 0 for x in n)), "avg_pool": float(np.mean([len(pools[d]) for d in rebal])),
                        "latest": picks[rebal[-1]]}
        args = (cal, opens, closes, last_bar, rebal)
        book = simulate(*args, picks, pool_of, buy_rate, sell_rate)
        ew = simulate(*args, pools, pool_of, buy_rate, sell_rate)
        rng = np.random.default_rng(7)
        rnd, rcost = [], []
        for _ in range(SEEDS):
            rp = {d: list(rng.choice(pools[d], size=min(len(picks[d]), len(pools[d])), replace=False)) for d in rebal}
            rs = simulate(*args, rp, pool_of, buy_rate, sell_rate)
            rnd.append(stats(rs["eq"])["cagr"])
            rcost.append(rs["cost_yr"])
        rows = {"book": book, "ew_pool": ew}
        results[name] = {k: {**stats(v["eq"]), "h1": halves(v["eq"])[0], "h2": halves(v["eq"])[1],
                             "turnover_yr": v["turnover_yr"], "cost_yr": v["cost_yr"]} for k, v in rows.items()}
        results[name]["random_cagr"] = {"p5": float(np.percentile(rnd, 5)), "p50": float(np.percentile(rnd, 50)),
                                        "p95": float(np.percentile(rnd, 95)),
                                        "book_pctile": float((np.array(rnd) < results[name]["book"]["cagr"]).mean()),
                                        "cost_yr": float(np.mean(rcost))}
        b, e, r = results[name]["book"], results[name]["ew_pool"], results[name]["random_cagr"]
        print(f"\n{name}: eligible avg {counts[name]['avg']:.1f} of pool {counts[name]['avg_pool']:.0f} "
              f"(min {counts[name]['min']}, max {counts[name]['max']}, cash months {counts[name]['zero_months']})")
        print(f"  book    CAGR {b['cagr']:6.1%}  Sharpe {b['sharpe']:.2f}  DD {b['maxdd']:6.1%}  "
              f"H1 {b['h1']:6.1%}  H2 {b['h2']:6.1%}  turnover {b['turnover_yr']:.1f}x  cost {b['cost_yr']:.2%}/yr")
        print(f"  EW pool CAGR {e['cagr']:6.1%}  Sharpe {e['sharpe']:.2f}  DD {e['maxdd']:6.1%}  "
              f"H1 {e['h1']:6.1%}  H2 {e['h2']:6.1%}")
        print(f"  random  p5 {r['p5']:6.1%}  p50 {r['p50']:6.1%}  p95 {r['p95']:6.1%}  -> book beats {r['book_pctile']:.0%}  (random cost {r['cost_yr']:.2%}/yr)")

    # stage-2 preview: latest eligible names ranked by last full month's range
    last = rebal[-1]
    pm_end = closes.index[closes.index < pd.Timestamp(last.year, last.month, 1)][-1]
    allp = counts["N200"]["latest"]
    rg = month_range_pct(db, allp, pm_end)
    preview = sorted(((s, rg.get(s)) for s in allp), key=lambda x: (x[1] is None, x[1]))
    print(f"\nlatest rebalance {last.date()} (signal {prev[last].date()}), range month ending {pm_end.date()}:")
    for s, v in preview:
        tags = [f.replace("NIFTY", "N") for f in FILTERS[1:] if s in members_as_of(ivs[f], last.date())]
        print(f"  {s:<14} range {v:6.1%}  {'/'.join(tags)}" if v is not None else f"  {s:<14} range  n/a")

    OUT.write_text(json.dumps({"window": [str(cal[0].date()), str(cal[-1].date())], "counts": counts,
                               "results": results, "preview": preview}, indent=1, default=str))
    print(f"\nsaved {OUT.name}")


if __name__ == "__main__":
    main()
