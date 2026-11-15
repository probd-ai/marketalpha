"""Equity curve: streak book ("just turned strong", daily) vs Grid-10 value core vs EW NIFTY 50 vs Nifty 50,
Jan 2008 -> latest, same inputs and costs (meanlong_streak.py, grid10_2008.py). Growth of ₹1 on a log scale, with
drawdown below. Grid-10 line = Monday (production weekday, also the median of the five); the band = all five weekdays.

  .venv/Scripts/python.exe scripts/research_grid/streak_equity_chart.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mt  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
SERIES = [  # fixed categorical order (validated light-mode palette)
    ("streak", "Streak book (just turned strong)", "#2a78d6", 2.2),
    ("grid10", "Grid-10 value core", "#eb6834", 1.6),
    ("ew50", "Equal-weight Nifty 50", "#1baf7a", 1.4),
    ("nifty50", "Nifty 50 index", "#eda100", 1.4),
]


def cagr(s: pd.Series) -> float:
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1


def main():
    st = pd.read_csv(HERE / "meanlong_streak" / "equity.csv", index_col=0, parse_dates=True)
    gd = pd.read_csv(HERE / "grid10_2008" / "equity.csv", index_col=0, parse_dates=True)
    idx = gd.index.intersection(st.index)
    curves = {"streak": st.loc[idx, "daily_shortest"], "grid10": gd.loc[idx, "grid10_k0"],
              "ew50": gd.loc[idx, "ew50"], "nifty50": gd.loc[idx, "nifty50"]}
    curves = {k: v / v.iloc[0] for k, v in curves.items()}
    band = gd.loc[idx, [f"grid10_k{k}" for k in range(5)]]
    band = band / band.iloc[0]

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2})
    fig, (ax, dx) = plt.subplots(2, 1, figsize=(12, 7.6), sharex=True, gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.08})
    fig.patch.set_facecolor(SURFACE)
    for a in (ax, dx):
        a.set_facecolor(SURFACE)
        a.grid(axis="y", color=GRID, linewidth=0.8)
        a.set_axisbelow(True)
        for sp in ("top", "right", "left"):
            a.spines[sp].set_visible(False)
        a.spines["bottom"].set_color(GRID)
        a.tick_params(length=0)

    ax.fill_between(idx, band.min(axis=1), band.max(axis=1), color="#eb6834", alpha=0.13, linewidth=0,
                    label="Grid-10, range of the 5 weekdays")
    ends = []
    for key, label, color, lw in reversed(SERIES):
        s = curves[key]
        ax.plot(idx, s, color=color, linewidth=lw, label=f"{label}  ({cagr(s) * 100:.1f}%/yr)", zorder=3)
        ends.append((s.iloc[-1], key, label, color))
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(mt.FixedLocator([0.5, 1, 2, 4, 8]))
    ax.yaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"₹{v:g}"))
    ax.yaxis.set_minor_formatter(mt.NullFormatter())
    ax.set_ylabel("Growth of ₹1 (log scale)")
    # direct labels at the right edge: ink text beside a colored marker, nudged apart when close
    ends.sort()
    placed = []
    for val, key, label, color in ends:
        y = val
        while placed and y / placed[-1] < 1.12:
            y = placed[-1] * 1.12
        placed.append(y)
        ax.plot([idx[-1]], [val], "o", color=color, markersize=6, markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=4)
        ax.annotate(f"₹{val:.2f}  {label.split(' (')[0]}", xy=(idx[-1], y), xytext=(10, 0), textcoords="offset points",
                    va="center", fontsize=9.5, color=INK)
    h, lab = ax.get_legend_handles_labels()
    order = [lab.index(x) for x in sorted(lab, key=lambda t: ("Streak" not in t, "Grid-10 value" not in t, "range" in t, "Equal" not in t))]
    ax.legend([h[i] for i in order], [lab[i] for i in order], loc="upper left", frameon=False, fontsize=9.5, labelcolor=INK)
    ax.set_title("Nifty 50 books, Jan 2008 – Oct 2026 (point-in-time members, after costs)", loc="left",
                 fontsize=13, fontweight="bold", color=INK, pad=12)
    for name, (a, b) in {"GFC": ("2008-01", "2009-03"), "COVID": ("2020-01", "2020-03")}.items():
        ax.axvspan(pd.Timestamp(a), pd.Timestamp(b) + pd.offsets.MonthEnd(0), color=INK2, alpha=0.06, linewidth=0)
        ax.text(pd.Timestamp(a), 0.47, name, fontsize=8.5, color=INK2)

    for key, label, color, lw in reversed(SERIES):
        s = curves[key]
        dx.plot(idx, (s / s.cummax() - 1) * 100, color=color, linewidth=1.2 if key != "streak" else 1.6)
    dx.set_ylabel("Drawdown")
    dx.yaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    dx.set_ylim(-70, 2)
    dx.xaxis.set_major_locator(matplotlib.dates.YearLocator(2))
    dx.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y"))
    fig.text(0.09, 0.012, "Grid-10 line = Monday (production weekday and the median of the five).\n"
             "Streak book: hold 10; sell when mean_long drops below its 222-day SMA; refill with the shortest streak above it.",
             fontsize=8.5, color=INK2)
    out = HERE / "meanlong_streak" / "equity_curve.png"
    fig.subplots_adjust(left=0.09, right=0.80, top=0.92, bottom=0.10)
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
