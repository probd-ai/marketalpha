"""Timing-luck ranges for three growth-book designs, Oct 2020 -> Sep 2026, honest harness.

  Two books (7)  : live LargeCaps structure (start + mid sleeves, top 7 by 20d return), rebalance shifted
                   k = 0..9 sessions  (k=1 is the engine's schedule with next-open fills)
  Weekly book (5): top 5 by 22d ROC each week, tranches held 4 weeks, rebalance on weekday k = 0..4
  Daily book (1) : one stock a day by 20d return, held 21 sessions, loading starts k = 0..9 sessions later
All Alpha-filtered NIFTY200 point-in-time, signal at previous close, next-open fills, full costs, 5 bp.
Each variant runs once over the full window; sub-period figures are read off that continuing curve.
Outputs range_compare.json (min / median / max per window) and range_compare.png (bands = min..max envelope).

  .venv/Scripts/python.exe scripts/research_grid/range_compare.py
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
from daily_tranche import CAP, INDEX, run_daily  # noqa: E402
from grid_meanrev import BENCH, charge_rates, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402
from weekly_tranche import run_weekly  # noqa: E402

START, SLIP = "2020-10-01", 0.0005
WINDOWS = {"Full Oct 2020 – Sep 2026": ("2020-10-01", "2026-09-25"),
           "Oct 2020 – Jun 2023": ("2020-10-01", "2023-06-30"),
           "Jul 2023 – Sep 2026": ("2023-07-01", "2026-09-25"),
           "Jan 2022 – Sep 2026": ("2022-01-01", "2026-09-25"),
           "2026 so far": ("2026-01-01", "2026-09-25")}
DESIGNS = {"Two books (7)": "#2a78d6", "Weekly book (5)": "#eb6834", "Daily book (1)": "#1baf7a"}
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5df", "#ffffff"


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
    roc22 = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    br, sr = charge_rates()

    curves: dict[str, list[pd.Series]] = {d: [] for d in DESIGNS}
    for k in range(10):
        tot = None
        for fq in [f"monthly+{k}", f"monthly_mid+{k}"]:
            eq, _ = run(opens, closes, ret20, iv, date.fromisoformat(START), None, 7, "alpha", fq, CAP / 2,
                        SLIP, br, sr, eligible=elig)
            tot = eq["equity"] if tot is None else tot + eq["equity"]
        curves["Two books (7)"].append(tot)
    for k in range(5):
        curves["Weekly book (5)"].append(run_weekly(opens, closes, roc22, iv, elig, START, None, br, sr, k=k)[0]["equity"])
    for k in range(10):
        curves["Daily book (1)"].append(run_daily(opens, closes, ret20, iv, elig, START, None, br, sr, skip=k)[0]["equity"])

    idx = curves["Two books (7)"][0].index
    norm = {d: pd.DataFrame({i: (c / c.iloc[0]).reindex(idx) for i, c in enumerate(cs)}) for d, cs in curves.items()}
    nifty = closes[BENCH].reindex(idx).ffill()
    nifty = nifty / nifty.iloc[0]

    def cagr(s):
        s = s.dropna()
        return (s.iloc[-1] / s.iloc[0]) ** (365.25 / (s.index[-1] - s.index[0]).days) - 1

    rep = {}
    for w, (a, b) in WINDOWS.items():
        rep[w] = {}
        for d, df in norm.items():
            sub = df.loc[a:b]
            cg = np.array([cagr(sub[c]) for c in sub.columns]) * 100
            tt = np.array([(sub[c].dropna().iloc[-1] / sub[c].dropna().iloc[0] - 1) for c in sub.columns]) * 100
            dd = np.array([((sub[c] / sub[c].cummax()) - 1).min() for c in sub.columns]) * 100
            rep[w][d] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                         "total_min": tt.min(), "total_median": float(np.median(tt)), "total_max": tt.max(),
                         "maxdd_worst": dd.min(), "maxdd_best": dd.max(), "variants": len(cg)}
        rep[w]["Nifty 50"] = {"cagr": cagr(nifty.loc[a:b]) * 100}
    (HERE / "range_compare.json").write_text(json.dumps(rep, indent=2, default=float))
    for d, df in norm.items():
        df.to_csv(HERE / f"range_{d.split()[0].lower()}.csv")

    # ---- chart: min..max band + median line per design, log scale, drawdown bands below ----
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
    for d, col in DESIGNS.items():
        df = norm[d] * CAP / 1e5
        lo, hi, med = df.min(axis=1), df.max(axis=1), df.median(axis=1)
        ax.fill_between(df.index, lo, hi, color=col, alpha=0.22, lw=0)
        r = full[d]
        ax.plot(df.index, med, color=col, lw=2,
                label=f"{d}: {r['cagr_min']:.0f}–{r['cagr_max']:.0f}%/yr (middle {r['cagr_median']:.0f}%), "
                      f"{r['variants']} timing variants")
        ax.annotate(f"₹{lo.iloc[-1]:.0f}–{hi.iloc[-1]:.0f} L", (df.index[-1], med.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK)
        dd = df.div(df.cummax()) - 1
        axd.fill_between(dd.index, dd.min(axis=1) * 100, dd.max(axis=1) * 100, color=col, alpha=0.22, lw=0)
        axd.plot(dd.index, dd.median(axis=1) * 100, color=col, lw=1.2)
    n = nifty * CAP / 1e5
    ax.plot(n.index, n.values, color=MUTED, ls=":", lw=1.4, label=f"Nifty 50: {full['Nifty 50']['cagr']:.0f}%/yr")
    ax.annotate(f"₹{n.iloc[-1]:.0f} L", (n.index[-1], n.iloc[-1]), xytext=(6, 0), textcoords="offset points",
                va="center", fontsize=9, color=INK)
    ax.set_yscale("log")
    ax.set_yticks([17, 25, 35, 50, 70, 100, 140])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"₹{x:g} L"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title("Growth book — same rules, different trading days: shaded band = best-to-worst day, line = middle\n"
                 "₹17 L, Alpha-filtered Nifty 200 point-in-time, next-open fills, full costs", loc="left",
                 fontsize=12, color=INK, pad=12)
    ax.text(pd.Timestamp("2023-07-01"), ax.get_ylim()[1], "  Jul 2023 →", va="top", fontsize=9, color=MUTED)
    ax.legend(loc="upper left", frameon=False, fontsize=9.3, bbox_to_anchor=(0, 0.93))
    ax.set_xlim(idx[0], idx[-1] + pd.Timedelta(days=170))
    axd.set_ylabel("Drawdown %")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator())
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.savefig(HERE / "range_compare.png", dpi=140, bbox_inches="tight", facecolor=SURFACE)

    for w, x in rep.items():
        print(f"== {w}   (Nifty 50 {x['Nifty 50']['cagr']:.1f}%/yr)")
        for d in DESIGNS:
            r = x[d]
            print(f"   {d:16s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}   "
                  f"total {r['total_min']:6.0f}% | {r['total_median']:6.0f}% | {r['total_max']:6.0f}%   "
                  f"maxDD {r['maxdd_worst']:5.1f}..{r['maxdd_best']:5.1f}")


if __name__ == "__main__":
    main()
