"""Anatomy of the VALUE book: NIFTY50 22d losers, N=20, buffer 30, monthly, rebalanced at month-turn (signal at the
last close of the month, fills at the first session's open = "monthly+0"). Oct 2020 -> Sep 2026, point-in-time.

Answers: where the return comes from (Nifty 50 cap-weighted -> equal-weight all 50 -> the value selection), what the
giants (HDFCBANK, RELIANCE, TCS, ICICIBANK, INFY, BHARTIARTL...) did and how the book traded them, holding periods
(min/max/mean/median/distribution), win rate, per-stock contribution, sector mix, drawdowns vs the market, monthly
turnover, and the 2008-2026 crash picture (NSE membership incl. delisted).

  .venv/Scripts/python.exe scripts/research_grid/value_anatomy.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from combine_all import stats  # noqa: E402
from crash_test import build_intervals, load_prices, read_mcwb, window_dd  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index, load_data  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402

CAP, SLIP = 1_260_000, 0.0005
GIANTS = ["HDFCBANK", "RELIANCE", "ICICIBANK", "TCS", "INFY", "BHARTIARTL", "LT", "SBIN", "ITC", "KOTAKBANK", "HINDUNILVR", "AXISBANK"]


def episodes_from_log(log, closes, end):
    """Holding episodes: first buy -> full exit, with rupee P&L from fills (open episodes marked to the last close)."""
    lg = pd.DataFrame(log)
    rows = []
    for sym, g in lg.sort_values("date", kind="stable").groupby("symbol"):
        pos, flows, invested, start = 0.0, 0.0, 0.0, None
        for r in g.itertuples():
            if pos <= 1e-9:
                pos, flows, invested, start = 0.0, 0.0, 0.0, r.date
            pos += r.qty if r.side == "BUY" else -r.qty
            flows += r.cash_flow
            invested += -r.cash_flow if r.side == "BUY" else 0.0
            if pos <= 1e-9:
                rows.append((sym, start, r.date, invested, flows, "closed"))
        if pos > 1e-9:
            rows.append((sym, start, end, invested, flows + pos * closes[sym].ffill().loc[end], "open"))
    ep = pd.DataFrame(rows, columns=["symbol", "start", "end", "invested", "pnl", "status"])
    ep["ret"] = ep["pnl"] / ep["invested"]
    ep["days"] = (pd.to_datetime(ep["end"]) - pd.to_datetime(ep["start"])).dt.days
    return ep


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, date(2020, 10, 1), date.today()))
    opens, closes, _ = load_data(db, ever)
    stocks = [c for c in closes.columns if c != BENCH]
    roc = closes[stocks].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    br, sr = charge_rates()
    log, hlog, book = [], [], []
    eq, traded = run(opens, closes, roc, iv, date(2020, 10, 1), date(2026, 9, 25), 20, "low", "monthly+0", CAP, SLIP,
                     br, sr, buffer=30, log=log, held_log=hlog, book_log=book)
    e = eq["equity"]
    idx = e.index
    end = idx[-1]
    ew = ew_index(closes, iv).reindex(idx).ffill()
    nifty = closes[BENCH].reindex(idx).ffill()

    # 1) return decomposition
    rep = {"decomposition": {"nifty50_cap_weighted": stats(nifty), "equal_weight_all_50": stats(ew), "value_book": stats(e)}}

    # 2) giants: stock return over the window, days held, episodes, P&L
    ep = episodes_from_log(log, closes, end)
    held_days = {}
    for d, held in hlog:
        for s in held:
            held_days[s] = held_days.get(s, 0) + 1
    ndays = len(hlog)
    giants = {}
    for s in GIANTS:
        if s not in closes.columns:
            continue
        g = ep[ep.symbol == s]
        c = closes[s].loc["2020-10-01":"2026-09-25"].dropna()
        giants[s] = {"stock_cagr_2020_26": float((c.iloc[-1] / c.iloc[0]) ** (365.25 / (c.index[-1] - c.index[0]).days) - 1),
                     "share_of_days_held": held_days.get(s, 0) / ndays, "episodes": int(len(g)),
                     "pnl_rupees": float(g.pnl.sum()), "avg_episode_ret": float(g.ret.mean()) if len(g) else None,
                     "avg_hold_days": float(g.days.mean()) if len(g) else None}
    rep["giants"] = giants
    # Nifty weight of the giants today (for context): from index_meta is not available -> skip; note equal weight 5%

    # 3) holding periods and trade stats
    closed = ep[ep.status == "closed"]
    rep["holding"] = {"episodes": int(len(ep)), "closed": int(len(closed)), "distinct_stocks": int(ep.symbol.nunique()),
                      "min_days": int(ep.days.min()), "max_days": int(ep.days.max()),
                      "mean_days": float(ep.days.mean()), "median_days": float(ep.days.median()),
                      "p25_days": float(ep.days.quantile(0.25)), "p75_days": float(ep.days.quantile(0.75)),
                      "share_le_35d": float((ep.days <= 35).mean()), "share_gt_90d": float((ep.days > 90).mean()),
                      "share_gt_180d": float((ep.days > 180).mean()),
                      "win_rate": float((closed.ret > 0).mean()), "avg_win": float(closed.ret[closed.ret > 0].mean()),
                      "avg_loss": float(closed.ret[closed.ret <= 0].mean()),
                      "best": ep.nlargest(1, "ret")[["symbol", "start", "end", "ret"]].astype(str).to_dict("records"),
                      "worst": ep.nsmallest(1, "ret")[["symbol", "start", "end", "ret"]].astype(str).to_dict("records"),
                      "longest": ep.nlargest(3, "days")[["symbol", "start", "end", "days", "ret"]].astype(str).to_dict("records"),
                      "turnover_pa": traded / e.mean() / ((idx[-1] - idx[0]).days / 365.25)}
    rep["hold_distribution_days"] = {f"<={b}": float((ep.days <= b).mean()) for b in (21, 35, 63, 91, 182, 365)}

    # 4) per-stock contribution
    per = ep.groupby("symbol").agg(pnl=("pnl", "sum"), episodes=("pnl", "size")).sort_values("pnl")
    tot = per.pnl.sum()
    rep["contribution"] = {"total_pnl": float(tot), "top10": per.tail(10)[::-1].round(0).reset_index().to_dict("records"),
                           "bottom10": per.head(10).round(0).reset_index().to_dict("records"),
                           "top5_share": float(per.pnl.nlargest(5).sum() / tot),
                           "stocks_net_negative": int((per.pnl < 0).sum()), "stocks_traded": int(len(per))}

    # 5) monthly swaps and what the book looks like
    names, swaps = [], []
    prev = None
    me = pd.Series(idx, index=idx).groupby(idx.to_period("M")).first()
    hd = dict(hlog)
    for d in me:
        cur = hd.get(d, frozenset())
        names.append(len(cur))
        if prev is not None:
            swaps.append(len(cur - prev))
        prev = cur
    rep["book"] = {"names_avg": float(np.mean(names)), "swaps_per_month_avg": float(np.mean(swaps)),
                   "swaps_per_month_range": [int(min(swaps)), int(max(swaps))]}
    # overlap with the "giants" at each month
    giant_share = [np.mean([s in hd.get(d, frozenset()) for s in GIANTS[:3]]) for d in me]
    rep["book"]["months_holding_HDFCBANK_RELIANCE_TCS_avg_count"] = float(np.mean(giant_share) * 3)

    # 6) sector mix (current classification)
    sector = {}
    for doc in db.index_meta.find({"Symbol": {"$in": ever}}, {"Symbol": 1, "Industry": 1, "_id": 0}):
        if doc.get("Industry") and doc["Industry"] != "ETF":
            sector.setdefault(doc["Symbol"], doc["Industry"])
    sec = {}
    for d, cash, bk in book:
        tot_v = cash + sum(bk.values())
        for s, v in bk.items():
            sec[sector.get(s, "Unknown")] = sec.get(sector.get(s, "Unknown"), 0.0) + v / tot_v
    rep["sector_avg"] = {k: v / len(book) for k, v in sorted(sec.items(), key=lambda t: -t[1])[:8]}

    # 7) drawdowns vs market (2020-26) and 2008-26 crash windows
    rep["drawdowns_2020_26"] = {"value": float((e / e.cummax() - 1).min()), "ew50": float((ew / ew.cummax() - 1).min()),
                                "nifty50": float((nifty / nifty.cummax() - 1).min())}
    dmonth = e.resample("ME").last().pct_change().dropna()
    nmonth = nifty.resample("ME").last().pct_change().dropna()
    j = pd.concat([dmonth, nmonth], axis=1).dropna()
    rep["monthly"] = {"beta_vs_nifty": float(np.cov(j.iloc[:, 0], j.iloc[:, 1])[0, 1] / np.var(j.iloc[:, 1])),
                      "corr_vs_nifty": float(j.corr().iloc[0, 1]),
                      "down_months_nifty": int((j.iloc[:, 1] < 0).sum()),
                      "avg_value_in_nifty_down_months": float(j[j.iloc[:, 1] < 0].iloc[:, 0].mean()),
                      "avg_nifty_in_nifty_down_months": float(j[j.iloc[:, 1] < 0].iloc[:, 1].mean()),
                      "avg_value_in_nifty_up_months": float(j[j.iloc[:, 1] > 0].iloc[:, 0].mean()),
                      "avg_nifty_in_nifty_up_months": float(j[j.iloc[:, 1] > 0].iloc[:, 1].mean())}

    ivb = build_intervals(read_mcwb(), date(2008, 1, 1), date(2026, 9, 25))
    ob, cb = load_prices(db, sorted({x.symbol for x in ivb}))
    ob, cb = ob.loc[:"2026-09-25"], cb.loc[:"2026-09-25"]
    rocb = cb[[c for c in cb.columns if c != BENCH]].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    eb, _ = run(ob, cb, rocb, ivb, date(2008, 1, 1), date(2026, 9, 25), 20, "low", "monthly+0", CAP, SLIP, br, sr, buffer=30)
    ebe = eb["equity"]
    ewb = ew_index(cb, ivb).reindex(ebe.index).ffill()
    nb = cb[BENCH].reindex(ebe.index).ffill()
    crashes = {"2008 GFC": ("2008-01-01", "2009-03-31"), "2010-11": ("2010-11-01", "2011-12-31"),
               "2015-16": ("2015-03-01", "2016-02-29"), "2020 COVID": ("2020-01-01", "2020-03-31"),
               "2024-25": ("2024-09-01", "2025-03-31")}
    rep["crashes_2008_26"] = {n: {"value": window_dd(ebe, a, b), "ew50": window_dd(ewb, a, b), "nifty50": window_dd(nb, a, b)}
                              for n, (a, b) in crashes.items()}
    rep["long_run_2008_26"] = {"value": stats(ebe), "ew50": stats(ewb), "nifty50": stats(nb)}
    (HERE / "value_anatomy.json").write_text(json.dumps(rep, indent=2, default=str))
    ep.to_csv(HERE / "value_anatomy_episodes.csv", index=False)
    print(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()
