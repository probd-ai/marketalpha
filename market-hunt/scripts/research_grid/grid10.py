"""Grid-10 value book on point-in-time NIFTY50.

Filter: mean_long's last 5-point grid crossing was UP (value_grid_filter.grid_state == +1).
BUY : from the filtered names, the 10 with the LOWEST 22-day return (at most 10 held, equal weight ~10%).
SELL: a holding only when its grid state has turned DOWN  AND  its last-month (22-day) return is positive.
Free slots refill from the lowest-22d filtered names not held. Otherwise hold.
Checked daily, weekly (weekday luck k=0..4) and monthly (session k=0..9 of the month), signal at the previous close, fills at the open; exit cash funds entries
(no trims). Oct 2020 -> Sep 2026. Compared with the value book, grid dynamic and EW50.

  .venv/Scripts/python.exe scripts/research_grid/grid10.py
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
    br, sr = charge_rates()

    def pick(prev, pool, held):
        sells = {s for s in held if s in pool and state.at[prev, s] == -1
                 and not pd.isna(roc22.at[prev, s]) and roc22.at[prev, s] > 0}
        keep = [s for s in held if s not in sells]
        filtered = [s for s in pool if state.at[prev, s] == 1 and not pd.isna(roc22.at[prev, s]) and s not in keep]
        cands = list(roc22.loc[prev, filtered].sort_values().index)
        return sorted(keep + cands[: max(0, N - len(keep))])

    sets, info = {}, {}
    cadences = {"Grid-10, daily check": ["daily"], "Grid-10, weekly check": [f"weekly+{k}" for k in range(5)],
                "Grid-10, monthly check": [f"monthly+{k}" for k in range(10)]}
    for name, freqs in cadences.items():
        cs = []
        for j, fq in enumerate(freqs):
            log, hlog, book = ([], [], []) if j == 0 else (None, None, None)
            eq, traded = run(opens, closes, roc22, iv, date(2020, 10, 1), date(2026, 9, 25), N, "low", fq, CAP, SLIP,
                             br, sr, pick_fn=pick, log=log, held_log=hlog, book_log=book)
            cs.append(eq["equity"] / eq["equity"].iloc[0])
            if j == 0:
                yrs = (eq.index[-1] - eq.index[0]).days / 365.25
                ep = episodes(pd.DataFrame(log), closes.ffill().loc[eq.index[-1]], eq.index[-1])
                closed = ep[ep.status == "closed"]
                big = [max((v / (c + sum(b.values())) for v in b.values()), default=0) for _, c, b in book]
                info[name] = {**hold_stats(hlog), "turnover_pa": traded / eq["equity"].mean() / yrs,
                              "hold_min": int(ep.days.min()), "hold_mean": float(ep.days.mean()), "hold_max": int(ep.days.max()),
                              "open_at_end": int((ep.status == "open").sum()), "win_rate": float((closed.ret > 0).mean()),
                              "avg_win": float(closed.ret[closed.ret > 0].mean()),
                              "avg_loss": float(closed.ret[closed.ret <= 0].mean()),
                              "max_weight": float(max(big)), "median_largest_weight": float(np.median(big[20:]))}
        idx = cs[0].index
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)})
        sets[name].to_csv(HERE / f"grid10_{name.split()[1]}.csv")
        print(f"{name} done", flush=True)
    idx = sets["Grid-10, daily check"].index
    ew = ew_index(closes, iv).reindex(idx).ffill()
    rep = {"info": info,
           "Value book (monthly losers, 20)": rng(pd.read_csv(HERE / "value_luck_2020.csv", index_col=0, parse_dates=True).reindex(idx), WW, ew),
           "Grid dynamic (all up-state)": rng(pd.read_csv(HERE / "grid_dynamic_daily.csv", index_col=0, parse_dates=True).reindex(idx), WW, ew)}
    for name, df in sets.items():
        rep[name] = rng(df, WW, ew)
    d = sets["Grid-10, daily check"].iloc[:, 0].loc["2021-04-01":]
    rep["daily_cagr_from_apr_2021"] = float((d.iloc[-1] / d.iloc[0]) ** (365.25 / (d.index[-1] - d.index[0]).days) - 1)
    rep["from_apr_2021"] = {}
    for name, df in [("Value book", pd.read_csv(HERE / "value_luck_2020.csv", index_col=0, parse_dates=True)), *sets.items()]:
        sub = df.loc["2021-04-01":]
        cg = [((sub[c].dropna().iloc[-1] / sub[c].dropna().iloc[0]) ** (365.25 / (sub[c].dropna().index[-1] - sub[c].dropna().index[0]).days) - 1) * 100
              for c in sub.columns]
        rep["from_apr_2021"][name] = {"min": min(cg), "median": float(np.median(cg)), "max": max(cg)}
    (HERE / "grid10.json").write_text(json.dumps(rep, indent=2, default=float))
    for k, x in info.items():
        print(f"{k:22s} names {x['names_avg']:.1f} [{x['names_min']}-{x['names_max']}]  hold min/median/mean/max "
              f"{x['hold_min']}/{x['hold_median_days']:.0f}/{x['hold_mean']:.0f}/{x['hold_max']}d  turnover {x['turnover_pa']:.1f}x  "
              f"win {x['win_rate'] * 100:.0f}% avg +{x['avg_win'] * 100:.1f}/{x['avg_loss'] * 100:.1f}%  open at end {x['open_at_end']}  "
              f"max weight {x['max_weight'] * 100:.0f}% (median largest {x['median_largest_weight'] * 100:.0f}%)")
    print("from Apr 2021:", {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in rep["from_apr_2021"].items()})
    for part in ["Value book (monthly losers, 20)", "Grid dynamic (all up-state)", *sets]:
        print(f"== {part}")
        for w in WW:
            r = rep[part][w]
            print(f"   {w:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}  spread {r['spread']:4.1f}"
                  f"  DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}  EW50 {r['ew50']:5.1f}")


if __name__ == "__main__":
    main()
