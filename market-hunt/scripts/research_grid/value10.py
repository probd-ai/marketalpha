"""Value-10: at most 10 NIFTY50 stocks — buy the biggest 22d losers among grid-filter-eligible names (mean_long's last
5-point grid crossing was UP), hold until the stock becomes a top-10 66-day winner (profit booked) or fails the filter.
Free slots are refilled with the next-biggest eligible 22d losers. Checked daily (and weekly, weekday luck k=0..4),
signal at the previous close, fills at the open, equal weight (equity / names; exit cash funds entries, no trims).
Oct 2020 -> Sep 2026 (mean_long starts 2018). Compared with the value book, the grid-dynamic book and EW50.

  .venv/Scripts/python.exe scripts/research_grid/value10.py
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
from analyze_book import episodes  # noqa: E402
from combine_all import WINDOWS as WW  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from longhold import hold_stats  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402
from value_grid_filter import grid_state  # noqa: E402
from value_luck import rng  # noqa: E402

CAP, SLIP, N = 1_260_000, 0.0005, 10


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, ml = load_data(db, ever, field="mean_long")
    cols = [c for c in closes.columns if c != BENCH]
    state = grid_state(ml.reindex(index=closes.index, columns=cols))
    roc22 = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    roc66 = closes[cols].apply(lambda c: c / c.dropna().shift(66).reindex(c.index) - 1)
    br, sr = charge_rates()
    exits = {"winner": 0, "filter": 0}

    def pick(prev, pool, held):
        ok = {s for s in pool if state.at[prev, s] == 1}
        r66 = roc66.loc[prev, [s for s in pool if not pd.isna(roc66.at[prev, s])]]
        winners = set(r66.sort_values(ascending=False).index[:N])
        keep = []
        for s in held:
            if s in pool and s in winners:
                exits["winner"] += 1
            elif s in pool and s not in ok:
                exits["filter"] += 1
            else:
                keep.append(s)
        cands = [s for s in roc22.loc[prev, sorted(ok)].sort_values().index if s not in keep and s not in winners]
        return sorted(keep + cands[: max(0, N - len(keep))])

    sets, info = {}, {}
    for name, freqs in {"Value-10, daily check": ["daily"], "Value-10, weekly check": [f"weekly+{k}" for k in range(5)]}.items():
        cs, turns = [], []
        for j, fq in enumerate(freqs):
            log, hlog = ([], []) if j == 0 else (None, None)
            for k_ in exits:
                exits[k_] = 0
            eq, traded = run(opens, closes, roc22, iv, date(2020, 10, 1), date(2026, 9, 25), N, "low", fq, CAP, SLIP,
                             br, sr, pick_fn=pick, log=log, held_log=hlog)
            cs.append(eq["equity"] / eq["equity"].iloc[0])
            yrs = (eq.index[-1] - eq.index[0]).days / 365.25
            turns.append(traded / eq["equity"].mean() / yrs)
            if j == 0:
                ep = episodes(pd.DataFrame(log), closes.ffill().loc[eq.index[-1]], eq.index[-1])
                closed = ep[ep.status == "closed"]
                info[name] = {**hold_stats(hlog), "turnover_pa": turns[0], "exits_winner_pa": exits["winner"] / yrs,
                              "exits_filter_pa": exits["filter"] / yrs, "win_rate": float((closed.ret > 0).mean()),
                              "avg_win": float(closed.ret[closed.ret > 0].mean()),
                              "avg_loss": float(closed.ret[closed.ret <= 0].mean()),
                              "hold_min_days": int(ep.days.min()), "hold_max_days": int(ep.days.max()),
                              "hold_mean_days": float(ep.days.mean())}
        idx = cs[0].index
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)})
        sets[name].to_csv(HERE / f"value10_{'daily' if 'daily' in name else 'weekly'}.csv")
        print(f"{name} done", flush=True)
    idx = sets["Value-10, daily check"].index
    ew = ew_index(closes, iv).reindex(idx).ffill()
    rep = {"info": info,
           "Value book (monthly losers, 20)": rng(pd.read_csv(HERE / "value_luck_2020.csv", index_col=0, parse_dates=True).reindex(idx), WW, ew),
           "Grid dynamic (all up-state)": rng(pd.read_csv(HERE / "grid_dynamic_daily.csv", index_col=0, parse_dates=True).reindex(idx), WW, ew)}
    for name, df in sets.items():
        rep[name] = rng(df, WW, ew)
    (HERE / "value10.json").write_text(json.dumps(rep, indent=2, default=float))
    for k, x in info.items():
        print(f"{k:24s} names {x['names_avg']:.1f} [{x['names_min']}-{x['names_max']}]  hold min/median/mean/max "
              f"{x['hold_min_days']}/{x['hold_median_days']:.0f}/{x['hold_mean_days']:.0f}/{x['hold_max_days']}d  turnover {x['turnover_pa']:.1f}x  "
              f"exits/yr winner {x['exits_winner_pa']:.0f} filter {x['exits_filter_pa']:.0f}  win {x['win_rate'] * 100:.0f}% "
              f"avg +{x['avg_win'] * 100:.1f}/{x['avg_loss'] * 100:.1f}%")
    for part in ["Value book (monthly losers, 20)", "Grid dynamic (all up-state)", *sets]:
        print(f"== {part}")
        for w in WW:
            r = rep[part][w]
            print(f"   {w:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}  spread {r['spread']:4.1f}"
                  f"  DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}  EW50 {r['ew50']:5.1f}")


if __name__ == "__main__":
    main()
