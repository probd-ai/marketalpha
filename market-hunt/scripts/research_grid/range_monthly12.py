"""Add a MONTHLY book of 12 (one rebalance a month, top 12 Alpha/20d-return, rebalance day shifted k=0..9
sessions) to the timing-luck comparison. Reuses the saved normalised curves of the other three designs
(range_two.csv, range_weekly.csv, range_daily.csv from range_compare.py); same harness and costs.

  .venv/Scripts/python.exe scripts/research_grid/range_monthly12.py
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
from daily_tranche import CAP, INDEX  # noqa: E402
from grid_meanrev import BENCH, charge_rates, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from range_compare import GRID, INK, MUTED, START, SURFACE, WINDOWS  # noqa: E402
from rotation_roc import run  # noqa: E402

N, SLIP = 12, 0.0005
DESIGNS = {"Monthly book (12)": ("#eda100", None), "Two books (7)": ("#2a78d6", "range_two.csv"),
           "Weekly book (5)": ("#eb6834", "range_weekly.csv"), "Daily book (1)": ("#1baf7a", "range_daily.csv")}


def cagr(s):
    s = s.dropna()
    return (s.iloc[-1] / s.iloc[0]) ** (365.25 / (s.index[-1] - s.index[0]).days) - 1


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

    m12, names, turn = [], [], []
    for k in range(10):
        eq, traded = run(opens, closes, ret20, iv, date.fromisoformat(START), None, N, "alpha", f"monthly+{k}",
                         CAP, SLIP, br, sr, eligible=elig)
        m12.append(eq["equity"])
        names.append(eq["n_held"].mean())
        turn.append(traded / eq["equity"].mean() / ((eq.index[-1] - eq.index[0]).days / 365.25))
    idx = m12[0].index
    norm = {"Monthly book (12)": pd.DataFrame({i: c / c.iloc[0] for i, c in enumerate(m12)})}
    for d, (_, f) in DESIGNS.items():
        if f:
            norm[d] = pd.read_csv(HERE / f, index_col=0, parse_dates=True).reindex(idx)
    norm["Monthly book (12)"].to_csv(HERE / "range_monthly12.csv")
    nifty = closes[BENCH].reindex(idx).ffill()
    nifty = nifty / nifty.iloc[0]

    rep = {"monthly12_avg_names": float(np.mean(names)), "monthly12_turnover_pa": float(np.mean(turn))}
    for w, (a, b) in WINDOWS.items():
        rep[w] = {"Nifty 50": cagr(nifty.loc[a:b]) * 100}
        for d, df in norm.items():
            sub = df.loc[a:b]
            cg = np.array([cagr(sub[c]) for c in sub.columns]) * 100
            dd = np.array([((sub[c] / sub[c].cummax()) - 1).min() for c in sub.columns]) * 100
            end = np.array([sub[c].dropna().iloc[-1] / sub[c].dropna().iloc[0] for c in sub.columns]) * CAP / 1e5
            rep[w][d] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                         "end_min_L": end.min(), "end_median_L": float(np.median(end)), "end_max_L": end.max(),
                         "maxdd_worst": dd.min(), "maxdd_best": dd.max()}
    (HERE / "range_monthly12.json").write_text(json.dumps(rep, indent=2, default=float))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
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
    for d, (col, _) in DESIGNS.items():
        df = norm[d] * CAP / 1e5
        lo, hi, med = df.min(axis=1), df.max(axis=1), df.median(axis=1)
        ax.fill_between(df.index, lo, hi, color=col, alpha=0.22, lw=0)
        r = full[d]
        ax.plot(df.index, med, color=col, lw=2,
                label=f"{d}: {r['cagr_min']:.0f}–{r['cagr_max']:.0f}%/yr (middle {r['cagr_median']:.0f}%)")
        ax.annotate(f"₹{lo.iloc[-1]:.0f}–{hi.iloc[-1]:.0f} L", (df.index[-1], med.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK)
        dd = df.div(df.cummax()) - 1
        axd.fill_between(dd.index, dd.min(axis=1) * 100, dd.max(axis=1) * 100, color=col, alpha=0.2, lw=0)
        axd.plot(dd.index, dd.median(axis=1) * 100, color=col, lw=1.1)
    n = nifty * CAP / 1e5
    ax.plot(n.index, n.values, color=MUTED, ls=":", lw=1.4, label=f"Nifty 50: {full['Nifty 50']:.0f}%/yr")
    ax.annotate(f"₹{n.iloc[-1]:.0f} L", (n.index[-1], n.iloc[-1]), xytext=(6, 0), textcoords="offset points",
                va="center", fontsize=9, color=INK)
    ax.set_yscale("log")
    ax.set_yticks([17, 25, 35, 50, 70, 100, 140])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"₹{x:g} L"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title("Growth book designs — shaded band = best-to-worst trading day, line = middle\n"
                 "₹17 L, Alpha-filtered Nifty 200 point-in-time, next-open fills, full costs", loc="left",
                 fontsize=12, color=INK, pad=12)
    ax.text(pd.Timestamp("2023-07-01"), ax.get_ylim()[1], "  Jul 2023 →", va="top", fontsize=9, color=MUTED)
    ax.legend(loc="upper left", frameon=False, fontsize=9.3, bbox_to_anchor=(0, 0.93))
    ax.set_xlim(idx[0], idx[-1] + pd.Timedelta(days=170))
    axd.set_ylabel("Drawdown %")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator())
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.savefig(HERE / "range_compare_4.png", dpi=140, bbox_inches="tight", facecolor=SURFACE)

    print(f"monthly12 avg names {rep['monthly12_avg_names']:.1f}, turnover {rep['monthly12_turnover_pa']:.1f}x/yr")
    for w in WINDOWS:
        x = rep[w]
        print(f"== {w}   (Nifty {x['Nifty 50']:.1f}%/yr)")
        for d in DESIGNS:
            r = x[d]
            print(f"   {d:18s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}   "
                  f"₹{r['end_min_L']:.0f}–{r['end_max_L']:.0f}L (mid {r['end_median_L']:.0f})   DD {r['maxdd_worst']:5.1f}..{r['maxdd_best']:5.1f}")


if __name__ == "__main__":
    main()
