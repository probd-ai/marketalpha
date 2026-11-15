"""Fully-invested NIFTY50 rotation: hold N names picked by 22-session ROC, point-in-time.

Each rebalance (first session of each month or week):
  * signal : ROC22 = close / close 22 own-bars earlier - 1, at the PREVIOUS session's close;
             only point-in-time NIFTY50 members on the rebalance day with 23 valid closes rank.
  * pick   : top N by highest ROC ("high") or lowest ROC ("low"), or N at random ("random").
  * trade  : at the rebalance session's open, move every name to equal weight 1/N of equity;
             only the deltas trade. Statutory charges + slippage on every rupee traded.
Between rebalances: a holding that leaves the index is sold at the next open; the cash waits
for the next rebalance. Valuation at close, last close carried over a missing bar.

Controls: equal-weight PIT NIFTY50 buy & hold (daily-rebalanced, cost-free — so it is a
slightly generous bar) and Nifty 50 buy & hold; plus a random-N band over many seeds.

Usage:
  .venv/Scripts/python.exe scripts/research_grid/rotation_roc.py [--index NIFTY50] [--lookback 22]
      [--n 20] [--buffer 30] [--freq monthly] [--seeds 20]
  --buffer: hysteresis — a holding is kept while it ranks inside the top BUFFER; retained names
  drift (no trims), new names split the freed cash capped at 1/n. The random control uses the
  same mechanics with a random ranking, so its costs are comparable.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid_meanrev import (BENCH, INDEX, WINDOWS, cagr, charge_rates, ew_index,  # noqa: E402
                          load_data, max_dd, sharpe)
from membership import load_membership, members_as_of, union_over  # noqa: E402

LOOKBACK = 22


def rebalance_days(cal: pd.DatetimeIndex, freq: str) -> set:
    # "<freq>+k": k sessions AFTER that schedule's day. With k=1 the signal is the schedule day's own close
    # and the fill is the next open — i.e. "decide at the engine's rebalance close, buy next morning".
    if "+" in freq:                                   # "<freq>+k": k sessions after that schedule's day
        name, k = freq.split("+")
        base = sorted(rebalance_days(cal, name))
        pos = {d: j for j, d in enumerate(cal)}
        return {cal[pos[d] + int(k)] for d in base if pos[d] + int(k) < len(cal)}
    if freq == "daily":                                # every session
        return set(cal)
    if freq == "quarterly_mid":                        # middle session of each calendar quarter (engine "mid")
        s = pd.Series(cal, index=cal)
        return {g.iloc[len(g) // 2] for _, g in s.groupby(cal.to_period("Q"))}
    if freq == "monthly_mid":                          # first session on/after the 15th of each month
        s = pd.Series(cal, index=cal)
        return set(s[s.index.day >= 15].groupby(s[s.index.day >= 15].index.to_period("M")).first().values)
    key = cal.to_period({"monthly": "M", "weekly": "W", "quarterly": "Q"}[freq])
    first = pd.Series(cal, index=cal).groupby(key).first()
    return set(first.values)


def run(opens, closes, roc, intervals, start, end, n, mode, freq, capital, slip,
        buy_rate, sell_rate, rng=None, buffer=0, exit_winners=0, log=None, eligible=None,
        elig_exit=True, pick_fn=None, same_close=False, alpha_day1=False, held_log=None, book_log=None,
        fixed_seats=False):
    """exit_winners>0: a holding is sold only when it ranks among the top `exit_winners` by ROC
    (the winners); every bottom-n name is bought. Book size floats, equal weight = equity/len(book).
    mode "alpha": only names with eligible[prev, s] True rank; highest `roc` first (pass the
    20-day-return frame as roc); an under-filled book splits equity across whatever qualifies.
    eligible with other modes: only eligible names can be BOUGHT; elig_exit=True also sells a
    holding that stops being eligible, False lets it stay under the normal buffer rule.
    pick_fn(prev, pool, held) -> list: custom book selection; the book floats, equal weight =
    equity/len(book), retained names are never trimmed (same no-trim deployment as exit_winners).
    same_close / alpha_day1: ENGINE-EMULATION knobs for reconciling with market-hunt — rank on the
    rebalance day's own close and fill at that close (look-ahead-ish), and break day-one ties
    alphabetically (the engine's no-warm-up bug). Never use them for honest results.
    log: optional list that receives every fill as a dict (date, symbol, side, qty, px, value, cash_flow).
    fixed_seats (pick_fn books): n seats of equity/n; only a NEW pick is bought (up to its seat, from cash),
    retained names are never topped up and leftover cash stays idle — an unfilled seat is cash, not a bigger
    bet on the names that remain. Default off (unchanged behaviour)."""
    cal = closes.index[(closes.index >= pd.Timestamp(start)) & closes[BENCH].notna()]
    if end is not None:
        cal = cal[cal <= pd.Timestamp(end)]
    rebal = rebalance_days(cal, freq)
    cclose = closes.ffill()
    last_bar = {s: closes[s].last_valid_index() for s in closes.columns}

    fill = closes if same_close else opens           # the price trades execute at
    cash, qty = capital, {}                          # qty: symbol -> shares
    traded = 0.0
    pend_exit: set[str] = set()
    eq_rows = []

    day = None

    def sell(sym, q, px):
        nonlocal cash, traded
        v = q * px * (1 - slip)
        cash += v * (1 - sell_rate)
        traded += q * px
        if log is not None:
            log.append({"date": day, "symbol": sym, "side": "SELL", "qty": q, "px": float(px),
                        "cash_flow": v * (1 - sell_rate)})

    def buy(sym, v_target, px):
        nonlocal cash, traded
        p = px * (1 + slip)
        q = v_target / (p * (1 + buy_rate))
        cash -= v_target
        traded += q * px
        if log is not None:
            log.append({"date": day, "symbol": sym, "side": "BUY", "qty": q, "px": float(px),
                        "cash_flow": -v_target})
        return q

    for i, d in enumerate(cal):
        day = d
        members = members_as_of(intervals, d.date())
        # forced exits queued yesterday (left the index)
        for sym in list(pend_exit):
            if sym not in qty:
                pend_exit.discard(sym); continue
            o = fill.at[d, sym]
            if pd.isna(o):
                lb = last_bar.get(sym)
                if lb is None or lb >= d:
                    continue                          # halted; retry tomorrow
                o = closes.at[lb, sym]
            sell(sym, qty.pop(sym), o)
            pend_exit.discard(sym)

        if d in rebal or i == 0:                      # invest on day one, then on schedule
            prev = d if same_close else (cal[i - 1] if i > 0 else closes.index[closes.index < d][-1])
            pool = [s for s in members if s in roc.columns and not pd.isna(roc.at[prev, s])
                    and not pd.isna(fill.at[d, s])]
            if mode == "random":
                ranked = list(rng.permutation(sorted(pool)))
            elif mode == "alpha":
                ok = eligible.loc[prev] if prev in eligible.index else pd.Series(dtype=bool)
                pool = [s for s in pool if bool(ok.get(s, False))]
                ranked = (list(rng.permutation(sorted(pool))) if rng is not None      # ranker-off control
                          else sorted(pool) if (alpha_day1 and i == 0)
                          else list(roc.loc[prev, pool].sort_values(ascending=False).index))
            else:
                ranked = list(roc.loc[prev, pool].sort_values(ascending=(mode == "low")).index)
            ranked_all = ranked
            if eligible is not None and mode != "alpha":
                ok = eligible.loc[prev] if prev in eligible.index else pd.Series(dtype=bool)
                ranked = [s for s in ranked_all if bool(ok.get(s, False))]
            if pick_fn is not None:
                pick = pick_fn(prev, pool, set(qty))
            elif eligible is not None and mode != "alpha" and buffer > n and not elig_exit:
                # entry filter only: holdings keep their seat on the unfiltered buffer rank
                keep = [s for s in ranked_all[:buffer] if s in qty]
                pick = keep + [s for s in ranked if s not in keep][: max(0, n - len(keep))]
            elif exit_winners:
                winners = set(ranked[-exit_winners:]) if mode != "random" else set(ranked[:exit_winners])
                entries = [s for s in ranked if s not in winners][:n] if mode == "random" else ranked[:n]
                keep = [s for s in qty if s not in winners]
                pick = keep + [s for s in entries if s not in keep]
            elif buffer > n:
                # hysteresis: a holding stays while it ranks inside the top `buffer`; fill to n
                keep = [s for s in ranked[:buffer] if s in qty]
                pick = keep + [s for s in ranked if s not in keep][: max(0, n - len(keep))]
            else:
                pick = ranked[:n]
            # value book at today's open (names with no open keep last close)
            px_open = {s: (fill.at[d, s] if not pd.isna(fill.at[d, s]) else cclose.at[prev, s]) for s in qty}
            equity = cash + sum(q * px_open[s] for s, q in qty.items())
            target = equity / max(n if fixed_seats else
                                  len(pick) if (exit_winners or mode == "alpha" or pick_fn) else n, 1)
            # sells first (dropped names, then trims), then buys
            for s in [s for s in qty if s not in pick]:
                if not pd.isna(fill.at[d, s]):
                    sell(s, qty.pop(s), fill.at[d, s])
            if buffer > n or exit_winners or pick_fn is not None:
                # never trim retained names; deploy ALL freed cash (no idle-cash drag):
                # first fill each pick's shortfall to 1/n pro-rata, then spread any remainder equally
                tradable = [s for s in pick if not pd.isna(fill.at[d, s])]   # a halted holding just waits
                gaps = {s: (0.0 if fixed_seats and s in qty else max(target - qty.get(s, 0.0) * fill.at[d, s], 0.0))
                        for s in tradable}
                total_gap = sum(gaps.values())
                budget = cash
                alloc = {s: (budget * g / total_gap if total_gap > budget else g) for s, g in gaps.items()} \
                    if total_gap > 0 else {s: 0.0 for s in tradable}
                rest = budget - sum(alloc.values())
                if rest > 1e-6 and tradable and not fixed_seats:
                    for s in tradable:
                        alloc[s] += rest / len(tradable)
                for s, v in alloc.items():
                    if v > 1e-6:
                        qty[s] = qty.get(s, 0.0) + buy(s, v, fill.at[d, s])
            else:
                for s in pick:
                    if s in qty:
                        cur = qty[s] * fill.at[d, s]
                        if cur > target:
                            dq = (cur - target) / fill.at[d, s]
                            sell(s, dq, fill.at[d, s]); qty[s] -= dq
                for s in pick:
                    cur = qty.get(s, 0.0) * fill.at[d, s]
                    need = min(target - cur, cash)
                    if need > 1e-6:
                        qty[s] = qty.get(s, 0.0) + buy(s, need, fill.at[d, s])

        held = sum(q * cclose.at[d, s] for s, q in qty.items())
        eq_rows.append((d, cash + held, held, len(qty), cclose.at[d, BENCH]))
        if held_log is not None:
            held_log.append((d, frozenset(qty)))
        if book_log is not None:
            book_log.append((d, cash, {s: q * cclose.at[d, s] for s, q in qty.items()}))

        if i + 1 < len(cal):
            nxt = members_as_of(intervals, cal[i + 1].date())
            pend_exit |= {s for s in qty if s not in nxt}

    eq = pd.DataFrame(eq_rows, columns=["date", "equity", "held", "n_held", "bench"]).set_index("date")
    return eq, traded


def stats(series: pd.Series) -> dict:
    return {"cagr": cagr(series), "max_dd": max_dd(series), "sharpe": sharpe(series),
            "total_return": series.iloc[-1] / series.iloc[0] - 1}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--freq", choices=["monthly", "weekly"], default="monthly")
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--capital", type=float, default=10_000_000)
    ap.add_argument("--slip-bps", type=float, default=5.0)
    ap.add_argument("--index", default=INDEX, help="point-in-time universe, e.g. NIFTY50 or NIFTY100")
    ap.add_argument("--lookback", type=int, default=LOOKBACK, help="ROC lookback in the stock's own sessions")
    ap.add_argument("--buffer", type=int, default=0,
                    help="hysteresis rank: keep a holding while it ranks inside the top BUFFER (0 = off)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    intervals = load_membership(db, args.index, date.today())
    ever = sorted(union_over(intervals, WINDOWS["full"][0], date.today()))
    opens, closes, _ = load_data(db, ever)
    stocks = closes.drop(columns=[BENCH])
    roc = stocks.apply(lambda c: c / c.dropna().shift(args.lookback).reindex(c.index) - 1)
    ew = ew_index(closes, intervals)
    buy_rate, sell_rate = charge_rates()
    slip = args.slip_bps / 10_000

    tag = f"{args.index}_lb{args.lookback}_n{args.n}_buf{args.buffer}_{args.freq}"
    report = {"index": args.index, "lookback": args.lookback, "buffer": args.buffer, "n": args.n, "freq": args.freq, "seeds": args.seeds, "slip_bps": args.slip_bps, "windows": {}}
    for name, (s, e) in WINDOWS.items():
        w = {}
        for mode in ["high", "low"]:
            eq, traded = run(opens, closes, roc, intervals, s, e, args.n, mode, args.freq,
                             args.capital, slip, buy_rate, sell_rate, buffer=args.buffer)
            yrs = (eq.index[-1] - eq.index[0]).days / 365.25
            w[mode] = {**stats(eq["equity"]), "turnover_pa": traded / eq["equity"].mean() / yrs,
                       "avg_names": float(eq["n_held"].mean())}
            if name == "full":
                eq.to_csv(Path(__file__).with_name(f"rot_{tag}_{mode}_equity.csv"))
        rnd, rnd_turn = [], []
        for seed in range(args.seeds):
            eq, traded = run(opens, closes, roc, intervals, s, e, args.n, "random", args.freq,
                             args.capital, slip, buy_rate, sell_rate, rng=np.random.default_rng(seed),
                             buffer=args.buffer)
            rnd.append(stats(eq["equity"]))
            rnd_turn.append(traded / eq["equity"].mean() / ((eq.index[-1] - eq.index[0]).days / 365.25))
        c = np.array([r["cagr"] for r in rnd])
        w["random"] = {"cagr_mean": float(c.mean()), "cagr_p05": float(np.percentile(c, 5)),
                       "cagr_p95": float(np.percentile(c, 95)),
                       "sharpe_mean": float(np.mean([r["sharpe"] for r in rnd])),
                       "max_dd_mean": float(np.mean([r["max_dd"] for r in rnd])),
                       "turnover_pa": float(np.mean(rnd_turn))}
        idx = eq.index
        e50 = ew.reindex(idx).ffill()
        w["ew50_buy_hold"] = stats(e50)
        w["nifty_buy_hold"] = stats(closes[BENCH].reindex(idx).ffill())
        w["period"] = f"{idx[0].date()} -> {idx[-1].date()}"
        report["windows"][name] = w

    out = Path(args.out) if args.out else Path(__file__).with_name(f"rot_{tag}.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
