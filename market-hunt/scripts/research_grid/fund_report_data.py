"""Data + charts for the fund report (whole book, AGGRESSIVE 40/35/25, Rs 45 L, Oct 2020 -> Sep 2026).

Sleeves (each a normalised equity curve, weekday timing variants k=0..4):
  Value core  28%  Grid-10 weekly, NIFTY50                  grid10_weekly.csv
  Gold         8%  TVC:GOLD x FX_IDC:USDINR, -0.5%/yr        gold/
  Nasdaq-100   4%  NASDAQ:NDX x FX_IDC:USDINR, -0.5%/yr      gold/
  Growth      35%  NIFTY200 weekly top-5, 8w, cap 10%        n200_8wcap10_deploy.csv
  Satellite   25%  NIFTY500MICRO750, same rules              n750_cap_weekly8w_cap10deploy.csv
Sleeve weights restored at every month change (0.15% on rupees moved). Every value x growth x satellite weekday
combination (5 x 5 x 5 = 125) is blended; the "median path" is the combination with the median final value.
Also rebuilds each stock book's CURRENT holdings (k=0 variant) as of the last session.

Writes fund_report/report_data.json and fund_report/*.png.

  .venv/Scripts/python.exe scripts/research_grid/fund_report_data.py
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from itertools import product
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
from hedge_compare import rd, to_inr  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402
from value_grid_filter import grid_state  # noqa: E402
from weekly_tranche import run_weekly  # noqa: E402

OUT = HERE / "fund_report"
TOTAL = 4_500_000
W = {"Value core": 0.28, "Gold": 0.08, "Nasdaq-100": 0.04, "Growth": 0.35, "Satellite": 0.25}
SWITCH_COST = 0.0015
START, END = "2020-10-01", "2026-09-25"
WINDOWS = {"Full period (Oct 2020 – Sep 2026)": ("2020-10-01", END), "Oct 2020 – Jun 2023": ("2020-10-01", "2023-06-30"),
           "Jul 2023 – Sep 2026": ("2023-07-01", END), "Jan 2022 – Sep 2026": ("2022-01-01", END),
           "Last 12 months": ("2025-09-25", END), "2026 year to date": ("2026-01-01", END)}
STRESS = {"2022 sell-off (Oct 2021 – Jun 2022)": ("2021-10-15", "2022-06-30"),
          "2023 Adani / rate scare (Dec 2022 – Mar 2023)": ("2022-12-01", "2023-03-31"),
          "2024-25 correction (Sep 2024 – Mar 2025)": ("2024-09-25", "2025-03-31"),
          "2026 Q1 fall (Jan – Mar 2026)": ("2026-01-01", "2026-03-31")}
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5df", "#ffffff"
COL = {"Whole fund": "#2a78d6", "Value core": "#1baa7a", "Gold": "#c98a00", "Nasdaq-100": "#7a5cc9",
       "Growth": "#eb6834", "Satellite": "#d0457a", "Nifty 50": MUTED, "Equal-weight Nifty 50": MUTED}


def stats(s):
    s = s.dropna()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return {"cagr": ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1) * 100 if yrs > 0.9 else (s.iloc[-1] / s.iloc[0] - 1) * 100,
            "max_dd": float((s / s.cummax() - 1).min()) * 100,
            "sharpe": float(r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else float("nan"),
            "vol": float(r.std() * math.sqrt(252)) * 100}


def blend(px: pd.DataFrame, w: list[float]):
    """Monthly-restored blend; returns (equity normalised to 1, per-sleeve cumulative P&L as fraction of start, costs)."""
    r = px.pct_change().fillna(0).to_numpy()
    month = px.index.to_period("M")
    v = list(w)
    pnl = np.zeros(len(w))
    cost = 0.0
    out = [1.0]
    for i in range(1, len(px)):
        g = [v[j] * r[i, j] for j in range(len(v))]
        pnl += g
        v = [v[j] + g[j] for j in range(len(v))]
        tot = sum(v)
        if month[i] != month[i - 1]:
            moved = sum(abs(v[j] - w[j] * tot) for j in range(len(v))) / 2
            c = moved * SWITCH_COST
            cost += c
            tot -= c
            v = [w[j] * tot for j in range(len(v))]
        out.append(tot)
    return pd.Series(out, index=px.index), pnl, cost


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=0.8)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0)


def lakh(ax, ticks):
    ax.set_yscale("log")
    ax.set_yticks(ticks)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"₹{x:g} L"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())


def current_books(db):
    """Holdings of each stock book (k=0 variant) at the last session, weights of the book's own capital."""
    br, sr = charge_rates()
    res = {}
    # value core: Grid-10 weekly on NIFTY50
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, ml = load_data(db, ever, field="mean_long")
    cols = [c for c in closes.columns if c != BENCH]
    state = grid_state(ml.reindex(index=closes.index, columns=cols))
    roc22 = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)

    def pick(prev, pool, held):
        sells = {s for s in held if s in pool and state.at[prev, s] == -1
                 and not pd.isna(roc22.at[prev, s]) and roc22.at[prev, s] > 0}
        keep = [s for s in held if s not in sells]
        filtered = [s for s in pool if state.at[prev, s] == 1 and not pd.isna(roc22.at[prev, s]) and s not in keep]
        return sorted(keep + list(roc22.loc[prev, filtered].sort_values().index)[: max(0, 10 - len(keep))])

    book, log = [], []
    run(opens, closes, roc22, iv, date(2020, 10, 1), END, 10, "low", "weekly+0", 1_260_000, 0.0005, br, sr,
        pick_fn=pick, book_log=book, log=log)
    res["Value core"] = snapshot(book, log, closes, extra={s: {"grid_state": None if pd.isna(state.loc[:END].iloc[-1][s]) else int(state.loc[:END].iloc[-1][s]),
                                                              "ret22": float(roc22.loc[:END].iloc[-1][s] * 100)} for s in cols})
    ew50 = ew_index(closes, iv)
    nifty = closes[BENCH]
    # growth / satellite: weekly top-5 Alpha, 8w tranches, 10% cap + trim + deploy
    for name, index in (("Growth", "NIFTY200"), ("Satellite", "NIFTY500MICRO750")):
        iv2 = load_membership(db, index, date.today())
        ever2 = sorted(union_over(iv2, date(2020, 10, 1), date.today()))
        o2, c2, score = load_data(db, ever2)
        fields = {"truevx_score": score, **load_means(db, ever2)}
        cols2 = [c for c in c2.columns if c != BENCH]
        elig = pd.DataFrame(True, index=c2.index, columns=cols2)
        for f, thr in RULES.items():
            elig &= (fields[f].reindex(index=c2.index, columns=cols2) > thr).fillna(False)
        r22 = c2[cols2].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
        b2 = []
        run_weekly(o2, c2, r22, iv2, elig, START, END, br, sr, k=0, capital=1_000_000, weeks=8, slot_div=40,
                   max_w=0.10, deploy_cash=True, book_log=b2)
        last = r22.index[r22.index <= END][-1]
        res[name] = snapshot(b2, None, c2, extra={s: {"alpha_eligible": bool(elig.at[last, s]),
                                                      "ret22": float(r22.at[last, s] * 100)} for s in cols2})
        res[name]["eligible_today"] = int(elig.loc[last].sum())
        res[name]["top5_today"] = list(r22.loc[last, [s for s in cols2 if elig.at[last, s]]]
                                       .sort_values(ascending=False).index[:5])
    return res, ew50, nifty, (br, sr)


def snapshot(book, log, closes, extra):
    d, cash, b = book[-1]
    eq = cash + sum(b.values())
    rows = []
    for s, v in sorted(b.items(), key=lambda kv: -kv[1]):
        row = {"symbol": s, "weight": v / eq * 100}
        if log is not None:
            lg = [x for x in log if x["symbol"] == s]
            qty, since = 0.0, None
            for x in lg:                       # start of the current holding spell
                if qty <= 1e-9:
                    since = x["date"]
                qty += x["qty"] if x["side"] == "BUY" else -x["qty"]
            row["held_since"] = str(pd.Timestamp(since).date())
        row.update(extra.get(s, {}))
        rows.append(row)
    return {"as_of": str(pd.Timestamp(d).date()), "cash_pct": cash / eq * 100, "holdings": rows}


def main():
    OUT.mkdir(exist_ok=True)
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    books, ew50, nifty, (br, sr) = current_books(db)
    print("books done", flush=True)

    val = pd.read_csv(HERE / "grid10_weekly.csv", index_col=0, parse_dates=True)
    gro = pd.read_csv(HERE / "n200_8wcap10_deploy.csv", index_col=0, parse_dates=True)
    sat = pd.read_csv(HERE / "n750_cap_weekly8w_cap10deploy.csv", index_col=0, parse_dates=True)
    idx = val.index.intersection(gro.index).intersection(sat.index)
    idx = idx[(idx >= START) & (idx <= END)]
    fx = rd("FX_IDC_USDINR.csv")
    gold, ndx = to_inr(rd("TVC_GOLD.csv"), fx, idx), to_inr(rd("NASDAQ_NDX.csv"), fx, idx)
    gold, ndx = gold / gold.iloc[0], ndx / ndx.iloc[0]
    bench = {"Nifty 50": nifty.reindex(idx).ffill(), "Equal-weight Nifty 50": ew50.reindex(idx).ffill()}
    bench = {k: v / v.iloc[0] for k, v in bench.items()}
    sl = {"Value core": val.reindex(idx).ffill(), "Growth": gro.reindex(idx).ffill(), "Satellite": sat.reindex(idx).ffill()}

    runs, meta = [], []
    for vi, gi, si in product(val.columns, gro.columns, sat.columns):
        px = pd.DataFrame({"Value core": sl["Value core"][vi], "Gold": gold, "Nasdaq-100": ndx,
                           "Growth": sl["Growth"][gi], "Satellite": sl["Satellite"][si]})
        eq, pnl, cost = blend(px, list(W.values()))
        runs.append(eq)
        meta.append((vi, gi, si, pnl, cost))
    fund = pd.DataFrame({i: r for i, r in enumerate(runs)})
    fin = fund.iloc[-1]
    mi = int(fin.sort_values().index[len(fin) // 2])
    med = fund[mi]
    rep = {"as_of": END, "charges": {"buy_pct": br * 100, "sell_pct": sr * 100, "slippage_bp": 5},
           "weights": W, "total": TOTAL, "books": books}

    # windows: whole fund (min/median/max over 125), sleeves (min/median/max over 5), benchmarks
    rep["windows"] = {}
    for wn, (a, b) in WINDOWS.items():
        ss = [stats(fund[c].loc[a:b]) for c in fund.columns]
        row = {"Whole fund": {"min": min(x["cagr"] for x in ss), "median": float(np.median([x["cagr"] for x in ss])),
                              "max": max(x["cagr"] for x in ss), "dd_worst": min(x["max_dd"] for x in ss),
                              "dd_median": float(np.median([x["max_dd"] for x in ss])),
                              "sharpe": float(np.median([x["sharpe"] for x in ss]))}}
        for n, df in sl.items():
            s2 = [stats(df[c].loc[a:b]) for c in df.columns]
            row[n] = {"min": min(x["cagr"] for x in s2), "median": float(np.median([x["cagr"] for x in s2])),
                      "max": max(x["cagr"] for x in s2), "dd_worst": min(x["max_dd"] for x in s2),
                      "dd_median": float(np.median([x["max_dd"] for x in s2])),
                      "sharpe": float(np.median([x["sharpe"] for x in s2]))}
        for n, s in {"Gold": gold, "Nasdaq-100": ndx, **bench}.items():
            x = stats(s.loc[a:b])
            row[n] = {"min": x["cagr"], "median": x["cagr"], "max": x["cagr"], "dd_worst": x["max_dd"],
                      "dd_median": x["max_dd"], "sharpe": x["sharpe"]}
        rep["windows"][wn] = row

    # median path detail
    y = med.resample("YE").last()
    y = pd.concat([med.iloc[[0]], y]).pct_change().dropna()
    yb = {n: pd.concat([s.iloc[[0]], s.resample("YE").last()]).pct_change().dropna() for n, s in bench.items()}
    ysl = {}
    for n, s in {"Value core": sl["Value core"].median(axis=1), "Gold": gold, "Nasdaq-100": ndx,
                 "Growth": sl["Growth"].median(axis=1), "Satellite": sl["Satellite"].median(axis=1)}.items():
        ysl[n] = pd.concat([s.iloc[[0]], s.resample("YE").last()]).pct_change().dropna()
    rep["yearly"] = {int(k.year): {"Whole fund": float(v) * 100, **{n: float(yb[n].loc[k]) * 100 for n in yb},
                                   **{n: float(ysl[n].loc[k]) * 100 for n in ysl}} for k, v in y.items()}
    mm = med.resample("ME").last()
    mm = pd.concat([med.iloc[[0]], mm]).pct_change().dropna()
    rep["monthly"] = {f"{k.year}-{k.month:02d}": float(v) * 100 for k, v in mm.items()}
    rep["month_stats"] = {"positive_pct": float((mm > 0).mean() * 100), "worst": float(mm.min() * 100),
                          "worst_when": str(mm.idxmin().date())[:7], "best": float(mm.max() * 100),
                          "best_when": str(mm.idxmax().date())[:7]}
    roll = med.resample("ME").last().pct_change(12).dropna() * 100
    rep["rolling12"] = {"min": float(roll.min()), "min_when": str(roll.idxmin().date())[:7], "median": float(roll.median()),
                        "max": float(roll.max()), "pct_negative": float((roll < 0).mean() * 100)}
    dd = med / med.cummax() - 1
    under = (dd < 0).astype(int)
    runs_u = under.groupby((under != under.shift()).cumsum()).sum()
    rep["longest_underwater_days"] = int(runs_u.max())
    eps, used = [], pd.Series(False, index=med.index)
    for _ in range(5):
        c = dd[~used]
        if c.min() >= -0.03:
            break
        tr = c.idxmin()
        pk = med.loc[:tr].idxmax()
        rec = med.loc[tr:][med.loc[tr:] >= med.loc[pk]]
        end = rec.index[0] if len(rec) else None
        eps.append({"peak": str(pk.date()), "trough": str(tr.date()), "recovered": str(end.date()) if end else "not yet",
                    "depth": float(dd.loc[tr] * 100), "nifty": float((bench["Nifty 50"].loc[pk:tr].min() /
                                                                       bench["Nifty 50"].loc[pk] - 1) * 100)})
        used.loc[pk:(end or med.index[-1])] = True
    rep["drawdowns"] = sorted(eps, key=lambda e: e["peak"])
    rep["stress"] = {}
    for sn, (a, b) in STRESS.items():
        f = fund.loc[a:b]
        fdd = (f / f.cummax() - 1).min() * 100
        rep["stress"][sn] = {"fund_median": float(fdd.median()), "fund_worst": float(fdd.min()),
                             **{n: float(((s.loc[a:b] / s.loc[a:b].cummax() - 1).min()) * 100)
                                for n, s in {"Nifty 50": bench["Nifty 50"], "Equal-weight Nifty 50": bench["Equal-weight Nifty 50"],
                                             "Gold": gold}.items()},
                             **{n: float(((df.loc[a:b] / df.loc[a:b].cummax() - 1).min().median()) * 100)
                                for n, df in sl.items()}}
    vi, gi, si, pnl, cost = meta[mi]
    rep["attribution_L"] = {n: float(p * TOTAL / 1e5) for n, p in zip(W, pnl)}
    rep["attribution_L"]["Monthly re-balancing cost"] = -float(cost * TOTAL / 1e5)
    rep["median_final_L"] = float(med.iloc[-1] * TOTAL / 1e5)
    rep["final_range_L"] = [float(fin.min() * TOTAL / 1e5), float(fin.max() * TOTAL / 1e5)]
    daily = pd.DataFrame({"Value core": sl["Value core"].median(axis=1), "Gold": gold, "Nasdaq-100": ndx,
                          "Growth": sl["Growth"].median(axis=1), "Satellite": sl["Satellite"].median(axis=1),
                          "Nifty 50": bench["Nifty 50"]}).pct_change().dropna()
    rep["corr"] = daily.corr().round(2).to_dict()
    rep["recent"] = {}
    for lab, days in {"1 month": 30, "3 months": 91, "6 months": 182, "12 months": 365}.items():
        a = med.index[med.index >= med.index[-1] - pd.Timedelta(days=days)][0]
        rep["recent"][lab] = {"Whole fund": float((med.iloc[-1] / med.loc[a] - 1) * 100),
                              **{n: float((s.iloc[-1] / s.loc[a] - 1) * 100) for n, s in bench.items()}}
    (OUT / "report_data.json").write_text(json.dumps(rep, indent=2, default=float), encoding="utf-8")

    # ---------------- charts
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    L = TOTAL / 1e5
    # 1) whole fund vs benchmarks + drawdown
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(11, 6.6), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.25], "hspace": 0.08}, facecolor=SURFACE)
    style(ax); style(axd)
    f = fund * L
    ax.fill_between(idx, f.min(axis=1), f.max(axis=1), color=COL["Whole fund"], alpha=0.15, lw=0)
    ax.plot(idx, med * L, color=COL["Whole fund"], lw=2.2, label="Whole fund (median of 125 trading-day combinations)")
    axd.plot(idx, (med / med.cummax() - 1) * 100, color=COL["Whole fund"], lw=1.4)
    for n, ls in (("Equal-weight Nifty 50", "--"), ("Nifty 50", ":")):
        s = bench[n] * L
        ax.plot(idx, s, color=MUTED, ls=ls, lw=1.4, label=n)
        axd.plot(idx, (s / s.cummax() - 1) * 100, color=MUTED, ls=ls, lw=1.0)
        ax.annotate(f"₹{s.iloc[-1]:.0f} L", (idx[-1], s.iloc[-1]), xytext=(6, 0), textcoords="offset points", va="center", fontsize=9)
    ax.annotate(f"₹{med.iloc[-1] * L:.0f} L\n(₹{f.iloc[-1].min():.0f}–{f.iloc[-1].max():.0f} L)", (idx[-1], med.iloc[-1] * L),
                xytext=(6, 0), textcoords="offset points", va="center", fontsize=9)
    lakh(ax, [45, 70, 100, 150, 200, 300, 400])
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.set_xlim(idx[0], idx[-1] + pd.Timedelta(days=170))
    axd.set_ylabel("Drawdown")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator())
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.savefig(OUT / "fund_equity.png", dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    # 2) sleeves, each rebased to its own allocation
    fig, ax = plt.subplots(figsize=(11, 5.2), facecolor=SURFACE)
    style(ax)
    sm = {"Value core": sl["Value core"].median(axis=1), "Gold": gold, "Nasdaq-100": ndx,
          "Growth": sl["Growth"].median(axis=1), "Satellite": sl["Satellite"].median(axis=1)}
    for n, s in sm.items():
        s = s / s.iloc[0]
        ax.plot(idx, s, color=COL[n], lw=1.9, label=f"{n}  ({stats(s)['cagr']:.1f}%/yr)")
        ax.annotate(f"{s.iloc[-1]:.1f}x", (idx[-1], s.iloc[-1]), xytext=(6, 0), textcoords="offset points", va="center", fontsize=9)
    e = bench["Equal-weight Nifty 50"]
    ax.plot(idx, e, color=MUTED, ls="--", lw=1.3, label=f"Equal-weight Nifty 50  ({stats(e)['cagr']:.1f}%/yr)")
    ax.set_yscale("log")
    ax.set_yticks([1, 1.5, 2, 3, 5, 8])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:g}x"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.set_xlim(idx[0], idx[-1] + pd.Timedelta(days=120))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.savefig(OUT / "sleeves.png", dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    # 3) calendar-year returns: fund vs Nifty 50 vs EW50
    yrs = list(rep["yearly"])
    fig, ax = plt.subplots(figsize=(11, 4.2), facecolor=SURFACE)
    style(ax)
    x = np.arange(len(yrs))
    for j, (n, c) in enumerate((("Whole fund", COL["Whole fund"]), ("Equal-weight Nifty 50", "#a8a79f"), ("Nifty 50", "#d4d3cc"))):
        vals = [rep["yearly"][yv][n] for yv in yrs]
        bars = ax.bar(x + (j - 1) * 0.27, vals, 0.25, color=c, label=n, edgecolor=SURFACE, linewidth=1)
        if n == "Whole fund":
            for bx, v in zip(bars, vals):
                ax.annotate(f"{v:+.0f}%", (bx.get_x() + bx.get_width() / 2, v), xytext=(0, 3 if v >= 0 else -11),
                            textcoords="offset points", ha="center", fontsize=8.5, color=INK)
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{yv}" + (" (Oct–Dec)" if yv == 2020 else " (to Sep)" if yv == 2026 else "") for yv in yrs])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    fig.savefig(OUT / "yearly.png", dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    # 4) rolling 12-month return
    fig, ax = plt.subplots(figsize=(11, 3.6), facecolor=SURFACE)
    style(ax)
    r12 = (med / med.shift(252) - 1).dropna() * 100
    rn = (bench["Nifty 50"] / bench["Nifty 50"].shift(252) - 1).dropna() * 100
    ax.fill_between(r12.index, 0, r12, where=r12 >= 0, color=COL["Whole fund"], alpha=0.18, lw=0)
    ax.fill_between(r12.index, 0, r12, where=r12 < 0, color="#d03b3b", alpha=0.25, lw=0)
    ax.plot(r12.index, r12, color=COL["Whole fund"], lw=1.6, label="Whole fund, trailing 12 months")
    ax.plot(rn.index, rn, color=MUTED, ls=":", lw=1.3, label="Nifty 50, trailing 12 months")
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.savefig(OUT / "rolling12.png", dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    # 5) allocation bar
    fig, ax = plt.subplots(figsize=(11, 1.25), facecolor=SURFACE)
    left = 0
    for n, w in W.items():
        ax.barh(0, w, left=left, color=COL[n], edgecolor=SURFACE, linewidth=2, height=0.7)
        ax.text(left + w / 2, 0, f"{n}\n{w * 100:.0f}% · ₹{w * L:.2f} L", ha="center", va="center", fontsize=8.6,
                color="white" if w > 0.05 else INK)
        left += w
    ax.set_xlim(0, 1)
    ax.axis("off")
    fig.savefig(OUT / "allocation.png", dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    w0 = rep["windows"]["Full period (Oct 2020 – Sep 2026)"]
    print("FUND full", {k: round(v, 1) for k, v in w0["Whole fund"].items()})
    for wn, row in rep["windows"].items():
        print(f"  {wn:36s} fund {row['Whole fund']['min']:5.1f}|{row['Whole fund']['median']:5.1f}|{row['Whole fund']['max']:5.1f}"
              f" DD {row['Whole fund']['dd_worst']:5.1f}  N50 {row['Nifty 50']['median']:5.1f}  EW50 {row['Equal-weight Nifty 50']['median']:5.1f}")
    print("yearly", {k: round(v["Whole fund"], 1) for k, v in rep["yearly"].items()})
    print("attribution L", {k: round(v, 1) for k, v in rep["attribution_L"].items()}, "final", round(rep["median_final_L"], 1),
          [round(v, 1) for v in rep["final_range_L"]])
    print("drawdowns", rep["drawdowns"])
    print("stress", json.dumps(rep["stress"], indent=0)[:1500])
    print("rolling12", rep["rolling12"], "months", rep["month_stats"], "underwater", rep["longest_underwater_days"])
    print("recent", rep["recent"])
    for n, b in books.items():
        print(n, b["as_of"], [(h["symbol"], round(h["weight"], 1)) for h in b["holdings"]])


if __name__ == "__main__":
    main()
