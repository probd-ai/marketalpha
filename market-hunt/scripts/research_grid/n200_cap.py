"""Growth book (NIFTY200): weekly top-5, hold 8 weeks — position behaviour and a per-stock cap.

Alpha filter, 22d ROC, honest harness, ₹17 L. Runs (weekday luck k=0..4 each):
  uncapped            (reference; also the position-peak analysis)
  cap 10% + deploy    buy <=10%, trim back to 10% each week, leftover cash tops up best-ranked holdings under 10%
  cap 15% + deploy
Position analysis (uncapped, k=0): each stock's peak weight, tier, days above 15%, price move before and after its peak.

  .venv/Scripts/python.exe scripts/research_grid/n200_cap.py
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
from grid_meanrev import BENCH, charge_rates, load_data  # noqa: E402
from longhold import cagr, hold_stats  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402
from range_compare import START, WINDOWS  # noqa: E402
from weekly_tranche import run_weekly  # noqa: E402

INDEX, CAP = "NIFTY200", 1_700_000
CONFIGS = {"8w no cap": dict(weeks=8, slot_div=40),
           "8w cap 10% +deploy": dict(weeks=8, slot_div=40, max_w=0.10, deploy_cash=True),
           "8w cap 15% +deploy": dict(weeks=8, slot_div=40, max_w=0.15, deploy_cash=True)}
TIERS = [("Large", "NIFTY100"), ("Mid", "NIFTYMIDCAP150")]


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
    br, sr = charge_rates()
    tiers = {t: load_membership(db, i, date.today()) for t, i in TIERS}

    sets, info, peaks = {}, {}, []
    for name, kw in CONFIGS.items():
        cs = []
        for k in range(5):
            book, trims, hlog = ([], [], []) if k == 0 else (None, [], None)
            eq, traded = run_weekly(opens, closes, roc22, iv, elig, START, None, br, sr, k=k, capital=CAP,
                                    book_log=book, trim_log=trims, held_log=hlog, **kw)
            cs.append(eq["equity"] / eq["equity"].iloc[0])
            if k == 0:
                yrs = (eq.index[-1] - eq.index[0]).days / 365.25
                w = [max((v / (c + sum(bk.values())) for v in bk.values()), default=0) for _, c, bk in book]
                info[name] = {**hold_stats(hlog), "turnover_pa": traded / eq["equity"].mean() / yrs,
                              "trims_pa": len(trims) / yrs, "peak_weight": float(max(w)),
                              "avg_largest": float(np.mean(w[40:])),
                              "avg_cash": float(np.mean([c / (c + sum(bk.values())) for _, c, bk in book][40:]))}
                if name == "8w no cap":
                    rows = [(d, s, v / (c + sum(bk.values()))) for d, c, bk in book for s, v in bk.items()]
                    df = pd.DataFrame(rows, columns=["date", "sym", "w"])
                    top = df.loc[df.groupby("sym").w.idxmax()].sort_values("w", ascending=False).head(10)
                    for _, r in top.iterrows():
                        s, d = r.sym, r.date
                        first = df[df.sym == s].date.min()
                        p = closes[s].asof(d)
                        after = closes[s].loc[d:d + pd.Timedelta(days=182)]
                        tier = next((t for t, ti in tiers.items() if s in members_as_of(ti, d.date())), "Other N200")
                        peaks.append({"sym": s, "peak_w": float(r.w), "date": str(d.date()), "tier": tier,
                                      "days_above_15": int(((df.sym == s) & (df.w > 0.15)).sum()),
                                      "rise_to_peak": float(p / closes[s].asof(first) - 1),
                                      "after_3m": float(closes[s].asof(d + pd.Timedelta(days=91)) / p - 1),
                                      "after_6m": float(closes[s].asof(d + pd.Timedelta(days=182)) / p - 1),
                                      "worst_6m": float(after.min() / p - 1)})
        idx = cs[0].index
        sets[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)})
        sets[name].to_csv(HERE / f"n200_{name.replace(' ', '').replace('%', '').replace('+', '_')}.csv")
        print(f"{name} done", flush=True)

    rep = {"info": info, "peaks_uncapped": peaks}
    for w, (a, b) in WINDOWS.items():
        rep[w] = {}
        for k, df in sets.items():
            sub = df.loc[a:b]
            cg = np.array([cagr(sub[c]) for c in sub.columns]) * 100
            dd = np.array([((sub[c].dropna() / sub[c].dropna().cummax()) - 1).min() for c in sub.columns]) * 100
            rep[w][k] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                         "dd_worst": dd.min(), "dd_best": dd.max()}
    (HERE / "n200_cap_report.json").write_text(json.dumps(rep, indent=2, default=float))
    print("PEAK POSITIONS (uncapped, k=0):")
    for p in peaks:
        print(f"   {p['sym']:12s} {p['peak_w'] * 100:5.1f}% on {p['date']}  {p['tier']:10s} days>15% {p['days_above_15']:3d}  "
              f"rise to peak {p['rise_to_peak']:+5.0%}  after 3m {p['after_3m']:+5.0%}  6m {p['after_6m']:+5.0%}  worst 6m {p['worst_6m']:+5.0%}")
    for k, v in info.items():
        print(f"{k:22s} names {v['names_avg']:.1f} [{v['names_min']}-{v['names_max']}]  hold median {v['hold_median_days']:.0f}d  "
              f"turnover {v['turnover_pa']:.1f}x  trims/yr {v['trims_pa']:.0f}  peak {v['peak_weight'] * 100:.1f}%  "
              f"avg largest {v['avg_largest'] * 100:.1f}%  cash {v['avg_cash'] * 100:.1f}%")
    for w in WINDOWS:
        print(f"== {w}")
        for k, r in rep[w].items():
            print(f"   {k:22s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}   DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}")


if __name__ == "__main__":
    main()
