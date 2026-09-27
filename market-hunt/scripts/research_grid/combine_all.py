"""Whole-book combination, Oct 2020 -> Sep 2026: value core (Nifty 50 22d losers / gold / Nasdaq-100 = 70/20/10) +
growth (NIFTY200 weekly top-5 8w cap 10%) + satellite (NIFTY500MICRO750 weekly top-5 8w cap 10%).

Sleeves (rupees): value = rot_NIFTY50_lb22_n20_buf30_monthly_low_equity.csv; gold = TVC:GOLD x USDINR net 0.5%/yr;
NDX = NASDAQ:NDX x USDINR net 0.5%/yr; growth = n200_8wcap10_deploy.csv (5 weekday variants); satellite =
n750_cap_weekly8w_cap10deploy.csv (5 weekday variants). Target weights restored monthly (0.15% on rupees moved).
Timing luck: every growth x satellite variant pair (25). Outputs combine_all.json and combine_all.png.

  .venv/Scripts/python.exe scripts/research_grid/combine_all.py
"""
from __future__ import annotations

import json
import math
import sys
from itertools import product
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from hedge_compare import rd, to_inr  # noqa: E402
from multi_blend import blend_multi  # noqa: E402

CORE = {"Value": 0.70, "Gold": 0.20, "NDX": 0.10}
MIXES = {  # core / growth / satellite
    "Your capital split 58/22/20": (0.577, 0.218, 0.205),
    "Conservative 70/20/10": (0.70, 0.20, 0.10),
    "Balanced 50/30/20": (0.50, 0.30, 0.20),
    "Aggressive 40/35/25": (0.40, 0.35, 0.25),
}
WINDOWS = {"Full Oct 2020 – Sep 2026": ("2020-10-01", "2026-09-25"), "Oct 2020 – Jun 2023": ("2020-10-01", "2023-06-30"),
           "Jul 2023 – Sep 2026": ("2023-07-01", "2026-09-25"), "Jan 2022 – Sep 2026": ("2022-01-01", "2026-09-25"),
           "2026 so far": ("2026-01-01", "2026-09-25")}
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5df", "#ffffff"


def stats(s: pd.Series) -> dict:
    s = s.dropna()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return {"cagr": (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1, "max_dd": float((s / s.cummax() - 1).min()),
            "sharpe": float(r.mean() / r.std() * math.sqrt(252))}


def main():
    val = pd.read_csv(HERE / "rot_NIFTY50_lb22_n20_buf30_monthly_low_equity.csv", index_col=0, parse_dates=True)["equity"]
    gro = pd.read_csv(HERE / "n200_8wcap10_deploy.csv", index_col=0, parse_dates=True)
    sat = pd.read_csv(HERE / "n750_cap_weekly8w_cap10deploy.csv", index_col=0, parse_dates=True)
    idx = val.index.intersection(gro.index).intersection(sat.index)
    idx = idx[(idx >= "2020-10-01") & (idx <= "2026-09-25")]
    fx = rd("FX_IDC_USDINR.csv")
    gold, ndx = to_inr(rd("TVC_GOLD.csv"), fx, idx), to_inr(rd("NASDAQ_NDX.csv"), fx, idx)
    nifty = pd.read_csv(HERE / "alpha_n200_equity.csv", index_col=0, parse_dates=True)["nifty50"].reindex(idx).ffill()

    sleeves_med = pd.DataFrame({"Value": val.reindex(idx), "Gold": gold, "NDX": ndx,
                                "Growth": gro.reindex(idx).median(axis=1), "Satellite": sat.reindex(idx).median(axis=1)})
    sleeves_med = sleeves_med / sleeves_med.iloc[0]
    rep = {"sleeves": {c: stats(sleeves_med[c]) for c in sleeves_med},
           "corr_daily": sleeves_med.pct_change().corr().round(2).to_dict(), "mixes": {}}
    curves = {}
    for name, (wc, wg, ws) in MIXES.items():
        w = (wc * CORE["Value"], wc * CORE["Gold"], wc * CORE["NDX"], wg, ws)
        runs = []
        for gi, si in product(gro.columns, sat.columns):
            px = pd.DataFrame({"Value": val.reindex(idx), "Gold": gold, "NDX": ndx,
                               "Growth": gro[gi].reindex(idx), "Satellite": sat[si].reindex(idx)}).ffill()
            runs.append(blend_multi(px, w))
        df = pd.DataFrame({i: r for i, r in enumerate(runs)})
        curves[name] = df
        m = {"weights_flat": dict(zip(["Value", "Gold", "NDX", "Growth", "Satellite"], w)), "windows": {}}
        for wn, (a, b) in WINDOWS.items():
            ss = [stats(df[c].loc[a:b]) for c in df.columns]
            cg = np.array([x["cagr"] for x in ss]) * 100
            dd = np.array([x["max_dd"] for x in ss]) * 100
            m["windows"][wn] = {"cagr_min": cg.min(), "cagr_median": float(np.median(cg)), "cagr_max": cg.max(),
                                "dd_worst": dd.min(), "dd_best": dd.max(),
                                "sharpe_median": float(np.median([x["sharpe"] for x in ss]))}
        med = df.median(axis=1)
        y = med.resample("YE").last()
        y = pd.concat([med.iloc[[0]], y]).pct_change().dropna()
        mm = med.resample("ME").last().pct_change().dropna()
        m["yearly_median"] = {int(k.year): float(v) for k, v in y.items()}
        m["worst_month"], m["positive_months"] = float(mm.min()), float((mm > 0).mean())
        roll = med.resample("ME").last().pct_change(12).dropna()
        m["rolling12_min"], m["rolling12_median"] = float(roll.min()), float(roll.median())
        rep["mixes"][name] = m
    rep["nifty50"] = {wn: stats(nifty.loc[a:b])["cagr"] * 100 for wn, (a, b) in WINDOWS.items()}
    (HERE / "combine_all.json").write_text(json.dumps(rep, indent=2, default=float))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(12.5, 8.4), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.08}, facecolor=SURFACE)
    for a_ in (ax, axd):
        a_.set_facecolor(SURFACE)
        a_.grid(axis="y", color=GRID, lw=0.8)
        for sp in ("top", "right", "left"):
            a_.spines[sp].set_visible(False)
        a_.spines["bottom"].set_color(GRID)
        a_.tick_params(length=0)
    cols = {"Your capital split 58/22/20": "#2a78d6", "Conservative 70/20/10": "#1baf7a",
            "Balanced 50/30/20": "#eda100", "Aggressive 40/35/25": "#eb6834"}
    CAPL = 78
    last = None
    ends = []
    for name, df in curves.items():
        d = df * CAPL
        r = rep["mixes"][name]["windows"]["Full Oct 2020 – Sep 2026"]
        ax.fill_between(d.index, d.min(axis=1), d.max(axis=1), color=cols[name], alpha=0.16, lw=0)
        ax.plot(d.index, d.median(axis=1), color=cols[name], lw=2,
                label=f"{name}: {r['cagr_min']:.0f}–{r['cagr_max']:.0f}%/yr, worst fall {r['dd_worst']:.0f}%")
        dd = d.div(d.cummax()) - 1
        axd.plot(dd.index, dd.median(axis=1) * 100, color=cols[name], lw=1.2)
        ends.append((d.median(axis=1).iloc[-1], d.min(axis=1).iloc[-1], d.max(axis=1).iloc[-1]))
    for m_, lo, hi in sorted(ends, key=lambda t: -t[0]):
        y = m_ if last is None else min(m_, last * 0.93)
        last = y
        ax.annotate(f"₹{lo:.0f}–{hi:.0f} L", (idx[-1], y), xytext=(6, 0), textcoords="offset points", va="center",
                    fontsize=8.8, color=INK)
    n = nifty / nifty.iloc[0] * CAPL
    ax.plot(n.index, n.values, color=MUTED, ls=":", lw=1.4, label=f"Nifty 50: {rep['nifty50']['Full Oct 2020 – Sep 2026']:.0f}%/yr")
    ax.annotate(f"₹{n.iloc[-1]:.0f} L", (n.index[-1], n.iloc[-1]), xytext=(6, 0), textcoords="offset points", va="center",
                fontsize=8.8, color=INK)
    ndd = n / n.cummax() - 1
    axd.plot(ndd.index, ndd * 100, color=MUTED, ls=":", lw=1.1)
    ax.set_yscale("log")
    ax.set_yticks([78, 120, 180, 270, 400, 600])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"₹{x:g} L"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title("Whole book — ₹78 L, Oct 2020 – Sep 2026: value core (70% Nifty-50 losers / 20% gold / 10% Nasdaq-100)"
                 " + growth + satellite\nmix = core / growth / satellite, rebalanced monthly; shaded = best-to-worst "
                 "trading-day combination of the two weekly books", loc="left", fontsize=11, color=INK, pad=12)
    ax.legend(loc="upper left", frameon=False, fontsize=9.2)
    ax.set_xlim(idx[0], idx[-1] + pd.Timedelta(days=150))
    axd.set_ylabel("Drawdown %")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator())
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.savefig(HERE / "combine_all.png", dpi=140, bbox_inches="tight", facecolor=SURFACE)

    print("SLEEVES:", {k: (round(v["cagr"] * 100, 1), round(v["max_dd"] * 100, 1)) for k, v in rep["sleeves"].items()})
    print("CORR:", json.dumps(rep["corr_daily"]))
    for name, m in rep["mixes"].items():
        print(f"== {name}  flat weights {({k: round(v * 100, 1) for k, v in m['weights_flat'].items()})}")
        for wn, r in m["windows"].items():
            print(f"   {wn:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}   DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}  Sharpe {r['sharpe_median']:.2f}")
        print(f"   yearly {({k: round(v * 100, 1) for k, v in m['yearly_median'].items()})}  worst month {m['worst_month'] * 100:.1f}%  "
              f"positive months {m['positive_months'] * 100:.0f}%  rolling12 min {m['rolling12_min'] * 100:.0f}% median {m['rolling12_median'] * 100:.0f}%")
    print("Nifty 50:", {k: round(v, 1) for k, v in rep["nifty50"].items()})


if __name__ == "__main__":
    main()
