"""Equity + drawdown, Jan 2008 -> Sep 2026: rising 222d RS-MA book vs 22d-loser book, EW NIFTY50, Nifty 50.
Reads rsma_rising_nifty50/equity.csv (written by rs_trend.py --rule rsma_rising).

  .venv/Scripts/python.exe scripts/research_grid/plot_rsma.py
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
SERIES = [  # (column, label, colour, style, width) — categorical slots 1-2; references in neutral ink
    ("A_rs_trend", "222d RS-MA rising", "#2a78d6", "-", 2.0),
    ("loser_plain", "22d losers, buffer 30", "#eb6834", "-", 2.0),
    ("EW50", "Equal-weight Nifty 50", MUTED, "--", 1.4),
    ("Nifty50", "Nifty 50", MUTED, ":", 1.4),
]
ERAS = [("2014-01-01", "2014 →"), ("2020-10-01", "Oct 2020 →")]


def cagr(s: pd.Series) -> float:
    return (s.iloc[-1] / s.iloc[0]) ** (365.25 / (s.index[-1] - s.index[0]).days) - 1


def main():
    eq = pd.read_csv(HERE / "rsma_rising_nifty50" / "equity.csv", index_col=0, parse_dates=True) / 1e7
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(12, 7.6), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.08}, facecolor=SURFACE)
    for a in (ax, axd):
        a.set_facecolor(SURFACE)
        a.grid(axis="y", color=GRID, lw=0.8)
        for d, _ in ERAS:
            a.axvline(pd.Timestamp(d), color=GRID, lw=1.2)
        for sp in ("top", "right", "left"):
            a.spines[sp].set_visible(False)
        a.spines["bottom"].set_color(GRID)
        a.tick_params(length=0)

    for col, label, c, ls, lw in SERIES:
        s = eq[col]
        ax.plot(s.index, s.values, color=c, ls=ls, lw=lw, label=f"{label}  ({cagr(s) * 100:.1f}% CAGR)")
        dd = (s / s.cummax() - 1) * 100
        axd.plot(dd.index, dd.values, color=c, ls=ls, lw=lw * 0.8)
        ax.annotate(f"₹{s.iloc[-1]:.2f} Cr", (s.index[-1], s.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK)

    ax.set_yscale("log")
    ax.set_yticks([0.5, 1, 2, 3, 4, 6, 8])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"₹{v:g} Cr"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title("Nifty 50, Jan 2008 – Sep 2026 — growth of ₹1 Cr (point-in-time NSE membership incl. "
                 "delisted, next-open fills, full costs)", loc="left", fontsize=12, color=INK, pad=12)
    for d, t in ERAS:
        ax.text(pd.Timestamp(d), ax.get_ylim()[1], f"  {t}", va="top", fontsize=9, color=MUTED)
    ax.legend(loc="upper left", frameon=False, fontsize=9.5, bbox_to_anchor=(0, 0.93))
    ax.set_xlim(eq.index[0], eq.index[-1] + pd.Timedelta(days=330))
    axd.set_ylabel("Drawdown %")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator(2))
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    out = HERE / "rsma_rising_nifty50" / "equity_compare.png"
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
