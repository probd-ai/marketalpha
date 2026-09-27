"""Equity + drawdown chart: 22d-loser book (no filter) vs mean_long>50 entry filter, with
equal-weight PIT NIFTY50 and Nifty 50 buy & hold for reference. Reads meanlong_filter/*.csv.

  .venv/Scripts/python.exe scripts/research_grid/plot_equity.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from pymongo import MongoClient  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from grid_meanrev import WINDOWS, ew_index, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402

CAP = 10_000_000
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5df", "#ffffff"
SERIES = [  # (label, colour, style, width) — categorical slots 1-2; references in neutral ink
    ("22d losers, buffer 30", "#2a78d6", "-", 2.0),
    ("+ mean_long>50 on buys", "#eb6834", "-", 2.0),
    ("Equal-weight Nifty 50", MUTED, "--", 1.4),
    ("Nifty 50", MUTED, ":", 1.4),
]


def cagr(s: pd.Series) -> float:
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1


def main():
    d = HERE / "meanlong_filter"
    base = pd.read_csv(d / "base_equity.csv", index_col=0, parse_dates=True)
    filt = pd.read_csv(d / "b_entry_equity.csv", index_col=0, parse_dates=True)
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    _, closes, _ = load_data(db, sorted(union_over(iv, WINDOWS["full"][0], date.today())))
    ew = ew_index(closes, iv).reindex(base.index).ffill()

    curves = [base["equity"], filt["equity"],
              CAP * ew / ew.iloc[0], CAP * base["bench"] / base["bench"].iloc[0]]
    curves = [c / 1e7 for c in curves]                       # rupees crore

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(12, 7.6), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.08},
                                  facecolor=SURFACE)
    split = pd.Timestamp(WINDOWS["P2"][0])
    for a in (ax, axd):
        a.set_facecolor(SURFACE)
        a.grid(axis="y", color=GRID, lw=0.8)
        a.axvline(split, color=GRID, lw=1.2)
        for sp in ("top", "right", "left"):
            a.spines[sp].set_visible(False)
        a.spines["bottom"].set_color(GRID)
        a.tick_params(length=0)

    for (label, col, ls, lw), c in zip(SERIES, curves):
        ax.plot(c.index, c.values, color=col, ls=ls, lw=lw, label=f"{label}  ({cagr(c) * 100:.1f}% CAGR)")
        dd = c / c.cummax() - 1
        axd.plot(dd.index, dd.values * 100, color=col, ls=ls, lw=lw * 0.8)
        # direct label at the line end: value in ink, not series colour
        ax.annotate(f"₹{c.iloc[-1]:.2f} Cr", (c.index[-1], c.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK)

    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"₹{v:g} Cr"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_yticks([1, 1.5, 2, 3, 4])
    ax.set_title("Nifty 50 22-day loser book vs mean_long>50 entry filter — growth of ₹1 Cr "
                 "(point-in-time, next-open fills, full costs)", loc="left", fontsize=12, color=INK, pad=12)
    ax.text(split, ax.get_ylim()[1], "  Jul 2023 →", va="top", fontsize=9, color=MUTED)
    ax.legend(loc="upper left", frameon=False, fontsize=9.5)
    ax.set_xlim(curves[0].index[0], curves[0].index[-1] + pd.Timedelta(days=150))

    axd.set_ylabel("Drawdown %")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator())
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    out = HERE / "meanlong_filter" / "equity_compare.png"
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
