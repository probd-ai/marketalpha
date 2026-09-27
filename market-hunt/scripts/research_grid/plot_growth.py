"""Equity + drawdown, Oct 2020 -> Sep 2026: growth book (live LargeCaps config, honest harness) vs the value
book (NIFTY50 22d losers, buffer 30), equal-weight PIT NIFTY200 and Nifty 50. All rebased to Rs 17 L.
Reads alpha_n200_equity.csv and rot_NIFTY50_lb22_n20_buf30_monthly_low_equity.csv.

  .venv/Scripts/python.exe scripts/research_grid/plot_growth.py
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
SPLIT = pd.Timestamp("2023-07-01")


def cagr(s: pd.Series) -> float:
    return (s.iloc[-1] / s.iloc[0]) ** (365.25 / (s.index[-1] - s.index[0]).days) - 1


def main():
    g = pd.read_csv(HERE / "alpha_n200_equity.csv", index_col=0, parse_dates=True)
    v = pd.read_csv(HERE / "rot_NIFTY50_lb22_n20_buf30_monthly_low_equity.csv", index_col=0, parse_dates=True)["equity"]
    cap = g["book"].iloc[0]
    v = v.reindex(g.index).ffill()
    series = [  # (curve, label, colour, style, width) — categorical slots 1-2; references in neutral ink
        (g["book"], "Growth: Alpha N200 top-7, first+mid (LargeCaps)", "#2a78d6", "-", 2.2),
        (cap * v / v.iloc[0], "Value: Nifty 50 22d losers, buffer 30", "#eb6834", "-", 1.8),
        (g["ew200"], "Equal-weight Nifty 200", MUTED, "--", 1.4),
        (g["nifty50"], "Nifty 50", MUTED, ":", 1.4),
    ]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(12, 7.6), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.08}, facecolor=SURFACE)
    for a in (ax, axd):
        a.set_facecolor(SURFACE)
        a.grid(axis="y", color=GRID, lw=0.8)
        a.axvline(SPLIT, color=GRID, lw=1.2)
        for sp in ("top", "right", "left"):
            a.spines[sp].set_visible(False)
        a.spines["bottom"].set_color(GRID)
        a.tick_params(length=0)

    for s, label, c, ls, lw in series:
        s = s / 1e5                                           # rupees lakh
        ax.plot(s.index, s.values, color=c, ls=ls, lw=lw,
                label=f"{label}  ({cagr(s) * 100:.1f}% CAGR, worst {((s / s.cummax() - 1).min()) * 100:.0f}%)")
        dd = (s / s.cummax() - 1) * 100
        axd.plot(dd.index, dd.values, color=c, ls=ls, lw=lw * 0.8)
        ax.annotate(f"₹{s.iloc[-1]:.0f} L", (s.index[-1], s.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK)

    ax.set_yscale("log")
    ax.set_yticks([17, 25, 35, 50, 70, 90])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"₹{x:g} L"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title("Growth vs value book — ₹17 L, Oct 2020 – Sep 2026 (point-in-time, next-open fills, "
                 "full costs, 5 bp slippage)", loc="left", fontsize=12, color=INK, pad=12)
    ax.text(SPLIT, ax.get_ylim()[1], "  Jul 2023 →", va="top", fontsize=9, color=MUTED)
    ax.legend(loc="upper left", frameon=False, fontsize=9.5, bbox_to_anchor=(0, 0.93))
    ax.set_xlim(g.index[0], g.index[-1] + pd.Timedelta(days=110))
    axd.set_ylabel("Drawdown %")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator())
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    out = HERE / "growth_vs_value.png"
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
