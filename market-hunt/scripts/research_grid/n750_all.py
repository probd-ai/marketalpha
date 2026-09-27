"""Satellite (NIFTY500MICRO750): add "monthly top-12, held 3 months" and chart every design with its timing-luck band.

New design (honest harness, Alpha filter, 22d ROC): on the k-th session of each month buy a tranche of equity/36 in each
of the top 12; tranches held 3 months (3 rebalances); repeats stack. Timing luck = k 0..9.
All other designs are read from their saved CSVs (n750_study.py / n750_grid.py). Outputs n750_all.json (min|median|max
per window for every design) and n750_all.png (bands for four front-runners + current, dashed middle lines for the rest).

  .venv/Scripts/python.exe scripts/research_grid/n750_all.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from pymongo import MongoClient  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from alpha_test import RULES, load_means  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from longhold import cagr, hold_stats  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from n750_study import CAP, INDEX  # noqa: E402
from range_compare import START, WINDOWS  # noqa: E402
from weekly_tranche import run_weekly  # noqa: E402

INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5df", "#ffffff"
DESIGNS = [  # (label, csv, colour, band)
    ("Current: quarterly 2 books x 20", "n750_current.csv", "#2a78d6", True),
    ("Monthly top-12, hold 3 months (new)", "n750_monthly12x3.csv", "#8a4fd6", True),
    ("Weekly top-5, hold 8 wk", "n750_a8.csv", "#d55181", True),
    ("Bi-weekly top-5, hold 8 wk", "n750_bw5.csv", "#1baf7a", True),
    ("Weekly top-5, hold 4 wk", "n750_weekly.csv", "#eb6834", False),
    ("Weekly top-5, hold 12 wk", "n750_weekly12w.csv", "#008300", False),
    ("Bi-weekly top-5, hold 4 wk", "n750_biweekly4w.csv", "#c98500", False),
    ("Bi-weekly top-5, hold 12 wk", "n750_biweekly12w.csv", "#6b6a63", False),
]


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

    cs, info = [], None
    for k in range(10):
        log = [] if k == 0 else None
        eq, traded = run_weekly(opens, closes, roc22, iv, elig, START, None, br, sr, capital=CAP, top=12, weeks=3,
                                slot_div=36, schedule=f"monthly+{k}", held_log=log)
        cs.append(eq["equity"] / eq["equity"].iloc[0])
        if log is not None:
            yrs = (eq.index[-1] - eq.index[0]).days / 365.25
            info = {**hold_stats(log), "turnover_pa": traded / eq["equity"].mean() / yrs}
        print(f"monthly12x3 k={k} done", flush=True)
    idx = cs[0].index
    pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)}).to_csv(HERE / "n750_monthly12x3.csv")

    sets = {lab: pd.read_csv(HERE / f, index_col=0, parse_dates=True).reindex(idx) for lab, f, _, _ in DESIGNS}
    ew = ew_index(closes, iv).reindex(idx).ffill()
    nifty = closes[BENCH].reindex(idx).ffill()
    rep = {"monthly12x3_info": info}
    for w, (a, b) in WINDOWS.items():
        rep[w] = {"EW universe": cagr(ew.loc[a:b]) * 100, "Nifty 50": cagr(nifty.loc[a:b]) * 100}
        for lab, df in sets.items():
            sub = df.loc[a:b]
            cg = np.array([cagr(sub[c]) for c in sub.columns]) * 100
            dd = np.array([((sub[c].dropna() / sub[c].dropna().cummax()) - 1).min() for c in sub.columns]) * 100
            end = np.array([sub[c].dropna().iloc[-1] / sub[c].dropna().iloc[0] for c in sub.columns]) * CAP / 1e5
            rep[w][lab] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                           "spread": cg.max() - cg.min(), "dd_worst": dd.min(), "dd_best": dd.max(),
                           "end_min_L": end.min(), "end_max_L": end.max(), "variants": len(cg)}
    (HERE / "n750_all.json").write_text(json.dumps(rep, indent=2, default=float))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(12.5, 8.6), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.08}, facecolor=SURFACE)
    for a_ in (ax, axd):
        a_.set_facecolor(SURFACE)
        a_.grid(axis="y", color=GRID, lw=0.8)
        a_.axvline(pd.Timestamp("2023-07-01"), color=GRID, lw=1.2)
        for sp in ("top", "right", "left"):
            a_.spines[sp].set_visible(False)
        a_.spines["bottom"].set_color(GRID)
        a_.tick_params(length=0)
    full = rep["Full Oct 2020 – Sep 2026"]
    ends = []
    for lab, _, col, band in DESIGNS:
        df = sets[lab].apply(lambda c: c / c.dropna().iloc[0]) * CAP / 1e5
        med = df.median(axis=1)
        r = full[lab]
        if band:
            ax.fill_between(df.index, df.min(axis=1), df.max(axis=1), color=col, alpha=0.17, lw=0)
            ddf = df.div(df.cummax()) - 1
            axd.fill_between(ddf.index, ddf.min(axis=1) * 100, ddf.max(axis=1) * 100, color=col, alpha=0.17, lw=0)
            axd.plot(ddf.index, ddf.median(axis=1) * 100, color=col, lw=1.1)
        ax.plot(df.index, med, color=col, lw=2.1 if band else 1.2, ls="-" if band else "--",
                label=f"{lab}: {r['cagr_min']:.0f}–{r['cagr_max']:.0f}%/yr, worst fall {r['dd_worst']:.0f}%")
        ends.append((med.iloc[-1], df.min(axis=1).iloc[-1], df.max(axis=1).iloc[-1], band))
    last = None
    for (m, lo, hi, band) in sorted(ends, key=lambda t: -t[0]):
        y = m if last is None else min(m, last * 0.93)
        last = y
        ax.annotate(f"₹{lo:.0f}–{hi:.0f} L" if band else f"₹{m:.0f} L", (idx[-1], y), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8.6, color=INK)
    e = ew / ew.iloc[0] * CAP / 1e5
    ax.plot(e.index, e.values, color=MUTED, ls=":", lw=1.4, label=f"Equal-weight 750: {full['EW universe']:.0f}%/yr")
    ax.set_yscale("log")
    ax.set_yticks([16, 25, 35, 50, 70, 100, 140, 200])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"₹{x:g} L"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title("Satellite designs — ₹16 L, Oct 2020 – Sep 2026, Nifty 500 + Microcap 250 point-in-time (Alpha filter, "
                 "next-open fills, full costs)\nshaded = best-to-worst trading day; dashed = middle line only",
                 loc="left", fontsize=11.3, color=INK, pad=12)
    ax.text(pd.Timestamp("2023-07-01"), ax.get_ylim()[1], "  Jul 2023 →", va="top", fontsize=9, color=MUTED)
    ax.legend(loc="upper left", frameon=False, fontsize=8.6, bbox_to_anchor=(0, 0.95))
    ax.set_xlim(idx[0], idx[-1] + pd.Timedelta(days=170))
    axd.set_ylabel("Drawdown %")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator())
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.savefig(HERE / "n750_all.png", dpi=140, bbox_inches="tight", facecolor=SURFACE)

    print(json.dumps(info, default=float))
    for w in WINDOWS:
        x = rep[w]
        print(f"== {w}  (EW {x['EW universe']:.1f}%, Nifty {x['Nifty 50']:.1f}%)")
        for lab, *_ in DESIGNS:
            r = x[lab]
            print(f"   {lab:38s} {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}  spread {r['spread']:4.1f}  "
                  f"DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}  n={r['variants']}")


if __name__ == "__main__":
    main()
