"""Equity + drawdown, Jan 2008 -> Sep 2026: 22d-loser book + 30% gold (monthly rebalance) vs the loser book
alone, gold alone (TVC:GOLD in rupees, net 0.5%/yr) and Nifty 50. Reads gold_blend/equity.csv.

  .venv/Scripts/python.exe scripts/research_grid/plot_gold.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gold_blend import gold_inr  # noqa: E402

INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5df", "#ffffff"
CRASHES = [("2008-01-08", "2008-10-27"), ("2010-11-05", "2011-12-20"), ("2015-03-03", "2016-02-25"),
           ("2020-01-14", "2020-03-23")]


def cagr(s: pd.Series) -> float:
    return (s.iloc[-1] / s.iloc[0]) ** (365.25 / (s.index[-1] - s.index[0]).days) - 1


def main():
    b = pd.read_csv(HERE / "gold_blend" / "equity.csv", index_col=0, parse_dates=True)
    n = pd.read_csv(HERE / "rsma_rising_nifty50" / "equity.csv", index_col=0, parse_dates=True)["Nifty50"]
    g = gold_inr(b.index)
    cap = b["loser20 + 0% gold"].iloc[0]
    series = [  # (curve, label, colour, style, width) — slots 1-3; Nifty reference in neutral ink
        (b["loser20 + 30% gold"], "70% loser book + 30% gold", "#2a78d6", "-", 2.2),
        (b["loser20 + 0% gold"], "Loser book alone", "#eb6834", "-", 1.6),
        (cap * g / g.iloc[0], "Gold alone (₹, TVC:GOLD × USDINR)", "#eda100", "-", 1.6),
        (n.reindex(b.index).ffill(), "Nifty 50", MUTED, ":", 1.4),
    ]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(12, 7.6), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.08}, facecolor=SURFACE)
    for a in (ax, axd):
        a.set_facecolor(SURFACE)
        a.grid(axis="y", color=GRID, lw=0.8)
        for p, q in CRASHES:                                   # Nifty peak -> bottom, recessive band
            a.axvspan(pd.Timestamp(p), pd.Timestamp(q), color="#f3f2ee", zorder=0)
        for sp in ("top", "right", "left"):
            a.spines[sp].set_visible(False)
        a.spines["bottom"].set_color(GRID)
        a.tick_params(length=0)

    for s, label, c, ls, lw in series:
        s = s / 1e7
        ax.plot(s.index, s.values, color=c, ls=ls, lw=lw, label=f"{label}  ({cagr(s) * 100:.1f}% CAGR, "
                f"worst {((s / s.cummax() - 1).min()) * 100:.0f}%)")
        dd = (s / s.cummax() - 1) * 100
        axd.plot(dd.index, dd.values, color=c, ls=ls, lw=lw * 0.8)
        ax.annotate(f"₹{s.iloc[-1]:.2f} Cr", (s.index[-1], s.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK)

    ax.set_yscale("log")
    ax.set_yticks([0.5, 1, 2, 4, 6, 8, 10])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"₹{v:g} Cr"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title("₹1 Cr, Jan 2008 – Sep 2026 — Nifty 50 22-day loser book with a 30% gold sleeve "
                 "(monthly rebalance, all costs)", loc="left", fontsize=12, color=INK, pad=12)
    ax.legend(loc="upper left", frameon=False, fontsize=9.5)
    ax.text(pd.Timestamp("2016-06-01"), 0.42, " shaded = Nifty 50 crashes (peak → bottom)", fontsize=8.5,
            color=MUTED, va="bottom")
    ax.set_xlim(b.index[0], b.index[-1] + pd.Timedelta(days=330))
    axd.set_ylabel("Drawdown %")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator(2))
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    out = HERE / "gold_blend" / "loser_30gold.png"
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
