"""All growth-book designs on one chart, Oct 2020 -> Sep 2026 (honest harness, ₹17 L).
Shaded bands (best-to-worst timing) for the three front-runners; middle lines only for the rest, to keep it legible.
Colours follow each design across every earlier chart (two books blue, weekly-4w orange, daily aqua, monthly yellow).
Reads the range_*.csv files written by range_compare.py, range_monthly12.py and longhold.py.

  .venv/Scripts/python.exe scripts/research_grid/plot_designs.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5df", "#ffffff"
CAP_L = 17
DESIGNS = [  # (label, csv, colour, band?)
    ("Weekly top-5, hold 8 wk (A8)", "range_a8.csv", "#d55181", True),
    ("Weekly top-5, hold 4 wk", "range_weekly.csv", "#eb6834", True),
    ("Two books of 7 (current)", "range_two.csv", "#2a78d6", True),
    ("Weekly top-5, hold 12 wk (A12)", "range_a12.csv", "#008300", False),
    ("Daily 1 stock", "range_daily.csv", "#1baf7a", False),
    ("Monthly 12", "range_monthly12.csv", "#c98500", False),
]


def cagr(s):
    s = s.dropna()
    return (s.iloc[-1] / s.iloc[0]) ** (365.25 / (s.index[-1] - s.index[0]).days) - 1


def main():
    base = pd.read_csv(HERE / "range_two.csv", index_col=0, parse_dates=True)
    idx = base.index
    nifty = pd.read_csv(HERE / "alpha_n200_equity.csv", index_col=0, parse_dates=True)["nifty50"].reindex(idx).ffill()
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(12.5, 8.4), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.08}, facecolor=SURFACE)
    for a in (ax, axd):
        a.set_facecolor(SURFACE)
        a.grid(axis="y", color=GRID, lw=0.8)
        a.axvline(pd.Timestamp("2023-07-01"), color=GRID, lw=1.2)
        for sp in ("top", "right", "left"):
            a.spines[sp].set_visible(False)
        a.spines["bottom"].set_color(GRID)
        a.tick_params(length=0)

    ends = []
    for label, f, col, band in DESIGNS:
        df = pd.read_csv(HERE / f, index_col=0, parse_dates=True).reindex(idx)
        df = df.apply(lambda c: c / c.dropna().iloc[0]) * CAP_L
        med = df.median(axis=1)
        cg = [cagr(df[c]) * 100 for c in df.columns]
        dd = min(((df[c].dropna() / df[c].dropna().cummax()) - 1).min() for c in df.columns) * 100
        lw = 2.2 if band else 1.3
        if band:
            ax.fill_between(df.index, df.min(axis=1), df.max(axis=1), color=col, alpha=0.18, lw=0)
            ddf = df.div(df.cummax()) - 1
            axd.fill_between(ddf.index, ddf.min(axis=1) * 100, ddf.max(axis=1) * 100, color=col, alpha=0.18, lw=0)
            axd.plot(ddf.index, ddf.median(axis=1) * 100, color=col, lw=1.1)
        ax.plot(df.index, med, color=col, lw=lw, ls="-" if band else "--",
                label=f"{label}: {min(cg):.0f}–{max(cg):.0f}%/yr, worst fall {dd:.0f}%")
        ends.append((med.iloc[-1], df.min(axis=1).iloc[-1], df.max(axis=1).iloc[-1], band))
    # end labels: ₹ range for banded designs, spaced so they do not collide
    last_y = None
    for (m, lo, hi, band), (label, _, col, _) in sorted(zip(ends, DESIGNS), key=lambda t: -t[0][0]):
        y = m if last_y is None else min(m, last_y * 0.93)   # keep ≥7% (log) between stacked labels
        last_y = y
        txt = f"₹{lo:.0f}–{hi:.0f} L" if band else f"₹{m:.0f} L"
        ax.annotate(txt, (idx[-1], y), xytext=(6, 0), textcoords="offset points", va="center", fontsize=8.8, color=INK)
    n = nifty / nifty.iloc[0] * CAP_L
    ax.plot(n.index, n.values, color=MUTED, ls=":", lw=1.4, label=f"Nifty 50: {cagr(n) * 100:.0f}%/yr")
    ax.annotate(f"₹{n.iloc[-1]:.0f} L", (n.index[-1], n.iloc[-1]), xytext=(6, 0), textcoords="offset points",
                va="center", fontsize=8.8, color=INK)
    ax.set_yscale("log")
    ax.set_yticks([17, 25, 35, 50, 70, 100, 140])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"₹{x:g} L"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title("Growth book designs — ₹17 L, Oct 2020 – Sep 2026 (Alpha-filtered Nifty 200 point-in-time, next-open "
                 "fills, full costs)\nshaded = best-to-worst trading day for the three front-runners; dashed = middle "
                 "line only", loc="left", fontsize=11.5, color=INK, pad=12)
    ax.text(pd.Timestamp("2023-07-01"), ax.get_ylim()[1], "  Jul 2023 →", va="top", fontsize=9, color=MUTED)
    ax.legend(loc="upper left", frameon=False, fontsize=9, bbox_to_anchor=(0, 0.94))
    ax.set_xlim(idx[0], idx[-1] + pd.Timedelta(days=170))
    axd.set_ylabel("Drawdown %")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator())
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    out = HERE / "designs_all.png"
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
