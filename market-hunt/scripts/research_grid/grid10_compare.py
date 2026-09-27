"""Grid-10 (monthly + weekly check) vs the value book (monthly 22d losers, N=20, buffer 30) on a point-in-time index (argv[1], default NIFTY50).

Every timing variant is run (value/monthly Grid-10: session k=0..9 of the month; weekly Grid-10: weekday k=0..4) and
full stats are computed per variant, then reported as min | median | max:
  return / risk : CAGR, max drawdown, Sharpe
  churning      : turnover x/yr, buys/yr, sells/yr, names held
  holding       : position length min / median / mean / max (days)
  profit / loss : closed trades, win rate, avg win / avg loss, profit factor, realised and open P&L (Rs)
Plus the k=0 variant's best / worst trades and top contributors, and an equity + drawdown chart (median variant line,
min..max band) vs the equal-weight index. Oct 2020 -> Sep 2026, Rs 12.6 L, next-open fills, full costs, 5 bp slippage.

  .venv/Scripts/python.exe scripts/research_grid/grid10_compare.py [NIFTY100]
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
from analyze_book import episodes  # noqa: E402
from combine_all import WINDOWS as WW, stats  # noqa: E402
from value_luck import rng  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from longhold import hold_stats  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402
from value_grid_filter import grid_state  # noqa: E402

CAP, SLIP, N = 1_260_000, 0.0005, 10
INDEX = sys.argv[1] if len(sys.argv) > 1 else "NIFTY50"
SFX = "" if INDEX == "NIFTY50" else f"_{INDEX.lower()}"
EWL = {"NIFTY50": "Equal-weight Nifty 50", "NIFTY100": "Equal-weight Nifty 100"}.get(INDEX, f"Equal-weight {INDEX}")
START, END = date(2020, 10, 1), date(2026, 9, 25)
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5df", "#ffffff"
COLOURS = {"Value book (monthly losers, 20)": "#eb6834", "Grid-10 monthly": "#2a78d6", "Grid-10 weekly": "#1baa7a"}


def variant_stats(eq, traded, log, hlog, closes):
    s = eq["equity"]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    st = stats(s)
    lg = pd.DataFrame(log)
    ep = episodes(lg, closes.ffill().loc[s.index[-1]], s.index[-1])
    cl = ep[ep.status == "closed"]
    gain, loss = cl.pnl[cl.pnl > 0].sum(), -cl.pnl[cl.pnl <= 0].sum()
    return {"cagr": st["cagr"] * 100, "max_dd": st["max_dd"] * 100, "sharpe": st["sharpe"],
            "turnover_pa": traded / s.mean() / yrs,
            "buys_pa": float((lg.side == "BUY").sum() / yrs), "sells_pa": float((lg.side == "SELL").sum() / yrs),
            "names_avg": hold_stats(hlog)["names_avg"],
            "hold_min": float(ep.days.min()), "hold_median": float(ep.days.median()),
            "hold_mean": float(ep.days.mean()), "hold_max": float(ep.days.max()),
            "closed_trades": float(len(cl)), "win_rate": float((cl.ret > 0).mean() * 100),
            "avg_win": float(cl.ret[cl.ret > 0].mean() * 100), "avg_loss": float(cl.ret[cl.ret <= 0].mean() * 100),
            "profit_factor": float(gain / loss) if loss else float("inf"),
            "realised_pnl": float(cl.pnl.sum()), "open_pnl": float(ep[ep.status == "open"].pnl.sum()),
            "final_value": float(s.iloc[-1])}, ep


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, INDEX, date.today())
    ever = sorted(union_over(iv, START, date.today()))
    opens, closes, ml = load_data(db, ever, field="mean_long")
    cols = [c for c in closes.columns if c != BENCH]
    state = grid_state(ml.reindex(index=closes.index, columns=cols))
    roc22 = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    br, sr = charge_rates()

    def grid_pick(prev, pool, held):
        sells = {s for s in held if s in pool and state.at[prev, s] == -1
                 and not pd.isna(roc22.at[prev, s]) and roc22.at[prev, s] > 0}
        keep = [s for s in held if s not in sells]
        filtered = [s for s in pool if state.at[prev, s] == 1 and not pd.isna(roc22.at[prev, s]) and s not in keep]
        cands = list(roc22.loc[prev, filtered].sort_values().index)
        return sorted(keep + cands[: max(0, N - len(keep))])

    books = {"Value book (monthly losers, 20)": ([f"monthly+{k}" for k in range(10)], dict(n=20, buffer=30)),
             "Grid-10 monthly": ([f"monthly+{k}" for k in range(10)], dict(n=N, pick_fn=grid_pick)),
             "Grid-10 weekly": ([f"weekly+{k}" for k in range(5)], dict(n=N, pick_fn=grid_pick))}
    rep, curves = {}, {}
    for name, (freqs, kw) in books.items():
        rows, cs = [], []
        for j, fq in enumerate(freqs):
            log, hlog = [], []
            n = kw["n"]
            eq, traded = run(opens, closes, roc22, iv, START, END, n, "low", fq, CAP, SLIP, br, sr,
                             buffer=kw.get("buffer", 0), pick_fn=kw.get("pick_fn"), log=log, held_log=hlog)
            st, ep = variant_stats(eq, traded, log, hlog, closes)
            rows.append({"variant": fq, **st})
            cs.append(eq["equity"])
            if j == 0:
                cl = ep[ep.status == "closed"].sort_values("ret")
                contrib = ep.groupby("symbol").pnl.sum().sort_values()
                rep.setdefault("k0_detail", {})[name] = {
                    "best_trades": cl.tail(5)[["symbol", "start", "end", "days", "ret"]].astype(str).to_dict("records"),
                    "worst_trades": cl.head(5)[["symbol", "start", "end", "days", "ret"]].astype(str).to_dict("records"),
                    "top_contributors": {k: round(v) for k, v in contrib.tail(5)[::-1].items()},
                    "bottom_contributors": {k: round(v) for k, v in contrib.head(5).items()},
                    "open_at_end": sorted(ep[ep.status == "open"].symbol)}
        df = pd.DataFrame(rows).set_index("variant")
        df.to_csv(HERE / f"grid10_compare{SFX}_{name.split()[0].lower()}_{name.split()[-1].strip(')').lower()}.csv")
        rep[name] = {c: {"min": float(df[c].min()), "median": float(df[c].median()), "max": float(df[c].max())}
                     for c in df.columns}
        idx = cs[0].index
        curves[name] = pd.DataFrame({i: c.reindex(idx) for i, c in enumerate(cs)}).ffill()
        print(f"{name} done", flush=True)

    idx = curves["Grid-10 weekly"].index
    ew = ew_index(closes, iv).reindex(idx).ffill()
    ew = CAP * ew / ew.iloc[0]
    for n, df in curves.items():
        df.reindex(idx).to_csv(HERE / f"grid10_curves{SFX}_{n.split()[0].lower()}_{n.split()[-1].strip(')').lower()}.csv")
    rep["windows"] = {n: rng(curves[n].reindex(idx), {**WW, "Apr 2021 - Sep 2026": ("2021-04-01", "2026-09-25")}, ew)
                      for n in curves}
    rep["EW"] = {"cagr": stats(ew)["cagr"] * 100, "max_dd": stats(ew)["max_dd"] * 100, "sharpe": stats(ew)["sharpe"]}
    (HERE / f"grid10_compare{SFX}.json").write_text(json.dumps(rep, indent=2, default=float))

    metrics = [("cagr", "CAGR %", 1), ("max_dd", "Max DD %", 1), ("sharpe", "Sharpe", 2), ("turnover_pa", "Turnover x/yr", 1),
               ("buys_pa", "Buys/yr", 0), ("sells_pa", "Sells/yr", 0), ("names_avg", "Names held", 1),
               ("hold_min", "Hold min d", 0), ("hold_median", "Hold median d", 0), ("hold_mean", "Hold mean d", 0),
               ("hold_max", "Hold max d", 0), ("closed_trades", "Closed trades", 0), ("win_rate", "Win rate %", 0),
               ("avg_win", "Avg win %", 1), ("avg_loss", "Avg loss %", 1), ("profit_factor", "Profit factor", 2),
               ("realised_pnl", "Realised P&L L", 1), ("open_pnl", "Open P&L L", 1), ("final_value", "Final value L", 1)]
    names = list(books)
    print(f"{'metric':16s}" + "".join(f"{n[:22]:>30s}" for n in names))
    for key, lab, d in metrics:
        sc = 1e5 if key in ("realised_pnl", "open_pnl", "final_value") else 1
        cells = [f"{rep[n][key]['min'] / sc:.{d}f} | {rep[n][key]['median'] / sc:.{d}f} | {rep[n][key]['max'] / sc:.{d}f}" for n in names]
        print(f"{lab:16s}" + "".join(f"{c:>30s}" for c in cells))
    print("EW", {k: round(v, 2) for k, v in rep["EW"].items()})
    for n, x in rep["windows"].items():
        print(f"== {n}")
        for w, r in x.items():
            print(f"   {w:26s} CAGR {r['cagr_min']:5.1f} | {r['cagr_median']:5.1f} | {r['cagr_max']:5.1f}"
                  f"  DD {r['dd_worst']:5.1f}..{r['dd_best']:5.1f}  EW {r['ew50']:5.1f}")
    for n, x in rep["k0_detail"].items():
        print(f"== {n} (k=0)")
        for k, v in x.items():
            print(f"   {k}: {v}")

    # ---- chart: median-variant line, min..max band, drawdown of median variant
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(12, 7.6), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.3], "hspace": 0.08}, facecolor=SURFACE)
    for a in (ax, axd):
        a.set_facecolor(SURFACE)
        a.grid(axis="y", color=GRID, lw=0.8)
        for sp in ("top", "right", "left"):
            a.spines[sp].set_visible(False)
        a.spines["bottom"].set_color(GRID)
        a.tick_params(length=0)
    for n in names:
        df = curves[n].reindex(idx).ffill() / 1e5
        fin = df.iloc[-1]
        med = df[fin.sort_values().index[len(fin) // 2]]
        c = COLOURS[n]
        r = rep[n]
        ax.fill_between(idx, df.min(axis=1), df.max(axis=1), color=c, alpha=0.13, lw=0)
        ax.plot(idx, med, color=c, lw=2, label=f"{n}  CAGR {r['cagr']['min']:.1f}–{r['cagr']['max']:.1f}%, "
                f"turnover {r['turnover_pa']['median']:.1f}x, hold {r['hold_median']['median']:.0f}d")
        axd.plot(idx, (med / med.cummax() - 1) * 100, color=c, lw=1.4)
        ax.annotate(f"₹{med.iloc[-1]:.0f} L", (idx[-1], med.iloc[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=9, color=INK)
    e = ew / 1e5
    ax.plot(idx, e, color=MUTED, ls="--", lw=1.4, label=f"{EWL}  CAGR {rep['EW']['cagr']:.1f}%")
    axd.plot(idx, (e / e.cummax() - 1) * 100, color=MUTED, ls="--", lw=1.1)
    ax.annotate(f"₹{e.iloc[-1]:.0f} L", (idx[-1], e.iloc[-1]), xytext=(6, 0), textcoords="offset points",
                va="center", fontsize=9, color=INK)
    ax.set_yscale("log")
    ax.set_yticks([12.6, 20, 30, 40, 50])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"₹{x:g} L"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title(f"{INDEX} — value core options — ₹12.6 L, Oct 2020 – Sep 2026 (line = median timing variant, band = all "
                 "variants; PIT, next-open fills, full costs)", loc="left", fontsize=11.5, color=INK, pad=12)
    ax.legend(loc="upper left", frameon=False, fontsize=9.3)
    ax.set_xlim(idx[0], idx[-1] + pd.Timedelta(days=110))
    axd.set_ylabel("Drawdown % (median)")
    axd.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    axd.xaxis.set_major_locator(mdates.YearLocator())
    axd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    out = HERE / f"grid10_vs_value{SFX}.png"
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
