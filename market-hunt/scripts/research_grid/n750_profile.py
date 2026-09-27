"""Deep profile of the satellite designs on NIFTY500MICRO750, Oct 2020 -> Sep 2026 (honest harness, one
representative timing each): what the book actually holds and how it behaves.

Designs: Current quarterly (two books first + mid-quarter, top 20 each, engine day + next open), Weekly top-5 hold
8w, Bi-weekly top-5 hold 8w, Weekly top-5 hold 4w.
Per design:
  * cap mix over time — point-in-time tier: Large = NIFTY100, Mid = NIFTYMIDCAP150, Small = NIFTYSMALLCAP250,
    Micro = in the 750 but in none of those (NIFTY500 = the first three), cash;
  * sector mix (index_meta Industry — CURRENT classification, not point-in-time);
  * names held, top-1 / top-5 weight, invested %;
  * holding episodes: length, win rate, average win / loss (episode return = close on last held day / close on
    first held day — approximate, ignores top-ups);
  * liquidity: position value / 20-day average traded value (close x volume) at ₹16 L and scaled to ₹1 Cr;
  * returns: yearly, monthly (share positive, worst, best), rolling 12-month range, beta / correlation vs Nifty 500;
  * breadth: Alpha-eligible names in the 750 per month (under-filling of the 2 x 20 current book).
Outputs n750_profile.json and n750_capmix.png / n750_names.png.

  .venv/Scripts/python.exe scripts/research_grid/n750_profile.py
"""
from __future__ import annotations

import json
import math
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
from grid_meanrev import BENCH, LOAD_FROM, charge_rates, load_data  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402
from n750_study import CAP, INDEX  # noqa: E402
from range_compare import START  # noqa: E402
from rotation_roc import run  # noqa: E402
from weekly_tranche import run_weekly  # noqa: E402

SLIP = 0.0005
TIERS = [("Large", "NIFTY100"), ("Mid", "NIFTYMIDCAP150"), ("Small", "NIFTYSMALLCAP250")]
TIER_COL = {"Large": "#2a78d6", "Mid": "#1baf7a", "Small": "#eda100", "Micro": "#eb6834", "Cash": "#c9c8c0"}
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5df", "#ffffff"


def load_value(db, symbols):
    rows = []
    for c in [c for c in db.list_collection_names() if c.startswith("prices_") and c >= "prices_2020"]:
        rows += list(db[c].find({"symbol": {"$in": symbols}, "date": {"$gte": LOAD_FROM}},
                                {"_id": 0, "symbol": 1, "date": 1, "value": 1}))
    v = pd.DataFrame(rows)
    v["date"] = pd.to_datetime(v["date"]).dt.normalize()
    v = v.drop_duplicates(["symbol", "date"]).pivot(index="date", columns="symbol", values="value").sort_index()
    return v.apply(lambda c: c.dropna().rolling(20, min_periods=10).mean().reindex(c.index)).ffill()


def merge_books(a, b):
    mb = {d: (c, bk) for d, c, bk in b}
    out = []
    for d, c, bk in a:
        c2, bk2 = mb.get(d, (0.0, {}))
        m = dict(bk)
        for s, v in bk2.items():
            m[s] = m.get(s, 0.0) + v
        out.append((d, c + c2, m))
    return out


def profile(book, tiers_iv, sector, adv, nifty500, closes):
    dates = [d for d, _, _ in book]
    eq = pd.Series([c + sum(bk.values()) for _, c, bk in book], index=dates)
    month_end = pd.Series(dates, index=dates).groupby(pd.DatetimeIndex(dates).to_period("M")).last()
    snaps, liq = [], []
    for d in month_end:
        c, bk = next((c, bk) for dd, c, bk in book if dd == d)
        tot = c + sum(bk.values())
        mem = {name: members_as_of(iv, d.date()) for name, iv in tiers_iv.items()}
        row = {"date": d, "Cash": c / tot, "names": len(bk)}
        for t in ["Large", "Mid", "Small", "Micro"]:
            row[t] = 0.0
        sec = {}
        for s, v in bk.items():
            t = next((t for t, _ in TIERS if s in mem[t]), "Micro")
            row[t] += v / tot
            sec[sector.get(s, "Unknown")] = sec.get(sector.get(s, "Unknown"), 0.0) + v / tot
            a = adv.at[d, s] if s in adv.columns else np.nan
            if a and not pd.isna(a) and a > 0:
                liq.append(v / a)
        w = sorted((v / tot for v in bk.values()), reverse=True)
        row["top1"] = w[0] if w else 0.0
        row["top5"] = sum(w[:5])
        row["sectors"] = sec
        snaps.append(row)
    sn = pd.DataFrame(snaps).set_index("date")
    # holding episodes (approximate return from closes)
    runs, cur = [], {}
    for d, _, bk in book:
        for s in list(cur):
            if s not in bk:
                runs.append((s, cur[s], prev_d))
                cur.pop(s)
        for s in bk:
            cur.setdefault(s, d)
        prev_d = d
    runs += [(s, d0, dates[-1]) for s, d0 in cur.items()]
    ep = []
    for s, d0, d1 in runs:
        c0, c1 = closes[s].asof(d0), closes[s].asof(d1)
        if c0 and c1 and c0 > 0:
            ep.append(((d1 - d0).days, c1 / c0 - 1))
    ep = np.array(ep)
    r = eq.pct_change().dropna()
    m = eq.resample("ME").last().pct_change().dropna()
    y = eq.resample("YE").last()
    y = pd.concat([eq.iloc[[0]], y]).pct_change().dropna()
    roll = eq.resample("ME").last().pct_change(12).dropna()
    n5 = nifty500.reindex(eq.index).ffill().pct_change().dropna()
    j = pd.concat([r, n5], axis=1).dropna()
    beta = float(np.cov(j.iloc[:, 0], j.iloc[:, 1])[0, 1] / np.var(j.iloc[:, 1]))
    sec_avg = pd.DataFrame(list(sn["sectors"])).fillna(0).mean().sort_values(ascending=False)
    yr_tier = sn[["Large", "Mid", "Small", "Micro", "Cash"]].groupby(sn.index.year).mean()
    liq = np.array(liq)
    return sn, {
        "cagr": (eq.iloc[-1] / eq.iloc[0]) ** (365.25 / (eq.index[-1] - eq.index[0]).days) - 1,
        "max_dd": float((eq / eq.cummax() - 1).min()), "sharpe": float(r.mean() / r.std() * math.sqrt(252)),
        "beta_vs_nifty500": beta, "corr_vs_nifty500": float(j.corr().iloc[0, 1]),
        "tier_avg": sn[["Large", "Mid", "Small", "Micro", "Cash"]].mean().to_dict(),
        "tier_by_year": yr_tier.round(3).to_dict("index"),
        "micro_max": float(sn["Micro"].max()), "micro_min": float(sn["Micro"].min()),
        "names_avg": float(sn["names"].mean()), "names_min": int(sn["names"].min()), "names_max": int(sn["names"].max()),
        "top1_avg": float(sn["top1"].mean()), "top1_max": float(sn["top1"].max()),
        "top5_avg": float(sn["top5"].mean()), "cash_avg": float(sn["Cash"].mean()),
        "top_sectors_avg": sec_avg.head(6).round(3).to_dict(),
        "sector_max_single_month": float(max(max(s.values()) for s in sn["sectors"] if s)),
        "episodes": int(len(ep)), "hold_median_days": float(np.median(ep[:, 0])),
        "hold_p90_days": float(np.percentile(ep[:, 0], 90)), "win_rate": float((ep[:, 1] > 0).mean()),
        "avg_win": float(ep[ep[:, 1] > 0, 1].mean()), "avg_loss": float(ep[ep[:, 1] <= 0, 1].mean()),
        "best_episode": float(ep[:, 1].max()), "worst_episode": float(ep[:, 1].min()),
        "monthly_positive_share": float((m > 0).mean()), "worst_month": float(m.min()), "best_month": float(m.max()),
        "yearly": {int(k.year): float(v) for k, v in y.items()},
        "rolling12_min": float(roll.min()), "rolling12_median": float(roll.median()), "rolling12_max": float(roll.max()),
        "liq_pos_over_adv_median_16L": float(np.median(liq)), "liq_pos_over_adv_p90_16L": float(np.percentile(liq, 90)),
        "liq_share_over_10pct_adv_16L": float((liq > 0.10).mean()),
        "liq_share_over_10pct_adv_1Cr": float((liq * (1e7 / CAP) > 0.10).mean()),
    }


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
    tiers_iv = {t: load_membership(db, idx, date.today()) for t, idx in TIERS}
    sector = {}
    for doc in db.index_meta.find({"Symbol": {"$in": ever}}, {"Symbol": 1, "Industry": 1, "_id": 0}):
        if doc.get("Industry") and doc["Industry"] not in ("ETF",):
            sector.setdefault(doc["Symbol"], doc["Industry"])
    adv = load_value(db, ever)
    n500 = []
    for c in [c for c in db.list_collection_names() if c.startswith("prices_") and c >= "prices_2020"]:
        n500 += list(db[c].find({"symbol": "Nifty 500"}, {"_id": 0, "date": 1, "close_price": 1}))
    nifty500 = pd.DataFrame(n500).assign(date=lambda d: pd.to_datetime(d.date).dt.normalize()) \
        .drop_duplicates("date").set_index("date")["close_price"].sort_index()

    books = {}
    a, b = [], []
    run(opens, closes, ret20, iv, date.fromisoformat(START), None, 20, "alpha", "quarterly+1", CAP / 2, SLIP, br, sr,
        eligible=elig, book_log=a)
    run(opens, closes, ret20, iv, date.fromisoformat(START), None, 20, "alpha", "quarterly_mid+1", CAP / 2, SLIP, br, sr,
        eligible=elig, book_log=b)
    books["Current quarterly 2x20"] = merge_books(a, b)
    for name, kw in [("Weekly top-5, 8wk", dict(weeks=8, slot_div=40)),
                     ("Bi-weekly top-5, 8wk", dict(every=2, phase=0, weeks=4, slot_div=20)),
                     ("Weekly top-5, 4wk", dict(weeks=4, slot_div=20))]:
        lg = []
        run_weekly(opens, closes, roc22, iv, elig, START, None, br, sr, capital=CAP, book_log=lg, **kw)
        books[name] = lg

    # breadth: Alpha-eligible names in the 750 at each month end
    me = pd.Series(closes.index, index=closes.index).loc[START:].groupby(closes.loc[START:].index.to_period("M")).last()
    breadth = [int(sum(bool(elig.at[d, s]) for s in members_as_of(iv, d.date()) if s in elig.columns)) for d in me]

    rep, snaps = {"breadth": {"eligible_avg": float(np.mean(breadth)), "eligible_min": int(min(breadth)),
                              "eligible_max": int(max(breadth)),
                              "months_below_40": int(sum(x < 40 for x in breadth)), "months": len(breadth)}}, {}
    for name, bk in books.items():
        sn, stats = profile(bk, tiers_iv, sector, adv, nifty500, closes)
        rep[name] = stats
        snaps[name] = sn
        print(f"{name} profiled", flush=True)
    (HERE / "n750_profile.json").write_text(json.dumps(rep, indent=2, default=float))

    # chart 1: cap mix over time (stacked area), one panel per design
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9.5, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, axes = plt.subplots(len(snaps), 1, figsize=(12, 11), sharex=True, facecolor=SURFACE)
    for ax, (name, sn) in zip(axes, snaps.items()):
        parts = ["Large", "Mid", "Small", "Micro", "Cash"]
        ax.stackplot(sn.index, *[sn[p] * 100 for p in parts], colors=[TIER_COL[p] for p in parts], labels=parts,
                     edgecolor=SURFACE, linewidth=0.6)
        t = rep[name]["tier_avg"]
        ax.set_title(f"{name} — average  Large {t['Large'] * 100:.0f}% · Mid {t['Mid'] * 100:.0f}% · "
                     f"Small {t['Small'] * 100:.0f}% · Micro {t['Micro'] * 100:.0f}% · Cash {t['Cash'] * 100:.0f}%",
                     loc="left", fontsize=10.5, color=INK)
        ax.set_ylim(0, 100)
        ax.set_facecolor(SURFACE)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
        ax.tick_params(length=0)
    axes[0].legend(loc="upper left", ncol=5, frameon=False, bbox_to_anchor=(0, 1.32), fontsize=9.5)
    axes[-1].xaxis.set_major_locator(mdates.YearLocator())
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.suptitle("Satellite book — market-cap mix over time (point-in-time tiers, month-end weights)", x=0.01, ha="left",
                 fontsize=12.5, color=INK, y=1.005)
    fig.tight_layout()
    fig.savefig(HERE / "n750_capmix.png", dpi=140, bbox_inches="tight", facecolor=SURFACE)

    # chart 2: names held per design vs Alpha-eligible breadth
    fig, ax = plt.subplots(figsize=(12, 4.4), facecolor=SURFACE)
    cols_ = ["#2a78d6", "#d55181", "#1baf7a", "#eb6834"]
    for (name, sn), col in zip(snaps.items(), cols_):
        ax.plot(sn.index, sn["names"], color=col, lw=1.8, label=f"{name} (avg {rep[name]['names_avg']:.0f})")
    ax.plot(me.values, breadth, color=MUTED, ls=":", lw=1.4, label=f"Alpha-eligible in the 750 (avg {np.mean(breadth):.0f})")
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=0.8)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0)
    ax.set_title("Stocks held (month end) vs how many stocks pass the Alpha filter", loc="left", fontsize=12, color=INK)
    ax.legend(frameon=False, fontsize=9, ncol=2, loc="upper left")
    fig.savefig(HERE / "n750_names.png", dpi=140, bbox_inches="tight", facecolor=SURFACE)
    print(json.dumps(rep, indent=1, default=float)[:200])


if __name__ == "__main__":
    main()
