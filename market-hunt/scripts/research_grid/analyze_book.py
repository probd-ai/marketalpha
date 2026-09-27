"""Per-stock anatomy of the NIFTY50 22d-ROC reversal book, and the 'sell only when a winner' exit.

Configs (monthly, 22-session ROC, point-in-time NIFTY50, next-open fills, costs):
  A  buffer : hold the 20 lowest-ROC names; keep a holding while it ranks inside the lowest 30.
  B  winners: buy every lowest-20 name; sell a holding only when it ranks in the top-20 by ROC.
Random control for B uses the same mechanics with a random ranking (20 seeds).

Outputs: headline stats per window, trade counts, per-stock P&L, episode win/loss, current book.
  .venv/Scripts/python.exe scripts/research_grid/analyze_book.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid_meanrev import BENCH, WINDOWS, charge_rates, ew_index, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run, stats  # noqa: E402

LB, N, SLIP, CAP = 22, 20, 0.0005, 10_000_000
CONFIGS = {"A_buffer30": {"buffer": 30}, "B_sell_when_winner": {"exit_winners": 20}}
OUT = Path(__file__).with_name("book_anatomy")


def episodes(log: pd.DataFrame, last_close: pd.Series, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for sym, g in log.sort_values("date", kind="stable").groupby("symbol", sort=False):
        pos, flows, start, invested = 0.0, 0.0, None, 0.0
        for r in g.itertuples():
            if pos <= 1e-9:
                start, flows, invested = r.date, 0.0, 0.0
            pos += r.qty if r.side == "BUY" else -r.qty
            flows += r.cash_flow
            if r.side == "BUY":
                invested += -r.cash_flow
            if pos <= 1e-9:
                rows.append((sym, start, r.date, invested, flows, "closed"))
                pos = 0.0
        if pos > 1e-9:
            mv = pos * last_close[sym]
            rows.append((sym, start, end, invested, flows + mv, "open"))
    ep = pd.DataFrame(rows, columns=["symbol", "start", "end", "invested", "pnl", "status"])
    ep["ret"] = ep["pnl"] / ep["invested"]
    ep["days"] = (pd.to_datetime(ep["end"]) - pd.to_datetime(ep["start"])).dt.days
    return ep


def main():
    OUT.mkdir(exist_ok=True)
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    intervals = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(intervals, WINDOWS["full"][0], date.today()))
    opens, closes, _ = load_data(db, ever)
    roc = closes.drop(columns=[BENCH]).apply(lambda c: c / c.dropna().shift(LB).reindex(c.index) - 1)
    ew = ew_index(closes, intervals)
    br, sr = charge_rates()
    report = {}

    for name, kw in CONFIGS.items():
        rep = {"windows": {}}
        for w, (s, e) in WINDOWS.items():
            log = []
            eq, traded = run(opens, closes, roc, intervals, s, e, N, "low", "monthly", CAP, SLIP, br, sr,
                             log=log, **kw)
            yrs = (eq.index[-1] - eq.index[0]).days / 365.25
            rnd = [stats(run(opens, closes, roc, intervals, s, e, N, "random", "monthly", CAP, SLIP, br, sr,
                             rng=np.random.default_rng(k), **kw)[0]["equity"]) for k in range(20)]
            c = np.array([r["cagr"] for r in rnd])
            rep["windows"][w] = {
                "period": f"{eq.index[0].date()} -> {eq.index[-1].date()}",
                **stats(eq["equity"]), "turnover_pa": traded / eq["equity"].mean() / yrs,
                "avg_names": float(eq["n_held"].mean()), "max_names": int(eq["n_held"].max()),
                "random_cagr_mean": float(c.mean()), "random_cagr_p05": float(np.percentile(c, 5)),
                "random_cagr_p95": float(np.percentile(c, 95)),
                "ew50_cagr": stats(ew.reindex(eq.index).ffill())["cagr"],
            }
            if w != "full":
                continue
            lg = pd.DataFrame(log)
            last = eq.index[-1]
            ep = episodes(lg, closes.ffill().loc[last], last)
            per = ep.groupby("symbol").agg(pnl=("pnl", "sum"), episodes=("pnl", "size"),
                                           invested=("invested", "sum")).sort_values("pnl")
            total = per["pnl"].sum()
            closed = ep[ep.status == "closed"]
            held = ep[ep.status == "open"].sort_values("ret")
            rep["trades"] = {
                "distinct_stocks": int(lg["symbol"].nunique()), "fills": int(len(lg)),
                "buys": int((lg.side == "BUY").sum()), "sells": int((lg.side == "SELL").sum()),
                "episodes": int(len(ep)), "closed_episodes": int(len(closed)),
                "closed_win_rate": float((closed.ret > 0).mean()),
                "closed_avg_ret": float(closed.ret.mean()), "closed_median_days": float(closed.days.median()),
                "avg_win": float(closed.ret[closed.ret > 0].mean()),
                "avg_loss": float(closed.ret[closed.ret <= 0].mean()),
                "total_pnl": float(total),
                "top5_share_of_pnl": float(per["pnl"].nlargest(5).sum() / total),
                "stocks_net_losing": int((per["pnl"] < 0).sum()),
            }
            rep["top10_stocks"] = per.tail(10)[::-1].round(0).reset_index().to_dict("records")
            rep["bottom10_stocks"] = per.head(10).round(0).reset_index().to_dict("records")
            rep["worst10_episodes"] = ep.nsmallest(10, "ret")[["symbol", "start", "end", "ret", "pnl", "status"]] \
                .assign(start=lambda x: x.start.astype(str).str[:10], end=lambda x: x.end.astype(str).str[:10]) \
                .round(3).to_dict("records")
            rep["current_book"] = held[["symbol", "start", "ret", "days"]] \
                .assign(start=lambda x: x.start.astype(str).str[:10]).round(3).to_dict("records")
            rep["TCS_episodes"] = ep[ep.symbol == "TCS"][["start", "end", "ret", "pnl", "days", "status"]] \
                .assign(start=lambda x: x.start.astype(str).str[:10], end=lambda x: x.end.astype(str).str[:10]) \
                .round(3).to_dict("records")
            ep.to_csv(OUT / f"{name}_episodes.csv", index=False)
            per.to_csv(OUT / f"{name}_per_stock.csv")
            lg.to_csv(OUT / f"{name}_fills.csv", index=False)
        report[name] = rep

    # TCS context: its 22d ROC and rank at the last signal date
    d = roc.index[-1]
    r = roc.loc[d].dropna().sort_values()
    report["TCS_now"] = {"date": str(d.date()), "roc22": float(r.get("TCS", np.nan)),
                         "rank_low_to_high": int(list(r.index).index("TCS")) + 1 if "TCS" in r else None,
                         "of": int(len(r))}
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
