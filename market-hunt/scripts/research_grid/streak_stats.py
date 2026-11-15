"""Trade-level and risk statistics for the streak book (daily rule, Jan 2008 -> latest) against Grid-10, EW NIFTY 50
and Nifty 50 — same inputs as meanlong_streak.py / grid10_2008.py.

Books: streak (shortest streak, the 10.5% book), streak_above30 (skip crosses that began with mean_long < 30,
fixed seats), grid10 (Monday = production weekday, the median of the five), EW NIFTY 50, Nifty 50.
Trade stats from the fill log (analyze_book.episodes): one episode = first buy -> full sale (open ones marked to the
last close). Risk stats from the daily equity curves.

  .venv/Scripts/python.exe scripts/research_grid/streak_stats.py
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
sys.path.insert(0, str(HERE.parent.parent))
from analyze_book import episodes  # noqa: E402
from crash_test import build_intervals, read_mcwb  # noqa: E402
from grid_meanrev import BENCH, charge_rates  # noqa: E402
from meanlong_slope import MA, START, load_ohlc, mean_long_series  # noqa: E402
from meanlong_streak import daily_picker, streaks  # noqa: E402
from meanlong_streak_levels import level_at_cross  # noqa: E402
from rotation_roc import run  # noqa: E402

N, SLIP, CAP = 10, 0.0005, 10_000_000.0


def trade_stats(ep: pd.DataFrame, yrs: float) -> dict:
    c = ep[ep.status == "closed"]
    wins, losses = c[c.ret > 0], c[c.ret <= 0]
    return {
        "episodes": int(len(ep)), "closed": int(len(c)), "open_at_end": int((ep.status == "open").sum()),
        "trades_per_year": len(c) / yrs,
        "hold_days_mean": float(c.days.mean()), "hold_days_median": float(c.days.median()),
        "hold_days_p25": float(c.days.quantile(0.25)), "hold_days_p75": float(c.days.quantile(0.75)),
        "win_rate": float((c.ret > 0).mean()),
        "avg_win": float(wins.ret.mean()), "avg_loss": float(losses.ret.mean()),
        "payoff_ratio": float(wins.ret.mean() / -losses.ret.mean()),
        "expectancy_per_trade": float(c.ret.mean()), "median_trade": float(c.ret.median()),
        "profit_factor": float(wins.pnl.sum() / -losses.pnl.sum()),
        "hold_days_winners": float(wins.days.median()), "hold_days_losers": float(losses.days.median()),
        "best_trades": c.nlargest(5, "ret")[["symbol", "start", "end", "days", "ret"]].astype(str).to_dict("records"),
        "worst_trades": c.nsmallest(5, "ret")[["symbol", "start", "end", "days", "ret"]].astype(str).to_dict("records"),
        "top_symbols_pnl": c.groupby("symbol").pnl.sum().nlargest(5).round(0).to_dict(),
        "bottom_symbols_pnl": c.groupby("symbol").pnl.sum().nsmallest(5).round(0).to_dict(),
        "share_of_pnl_top5_trades": float(c.pnl.nlargest(5).sum() / c.pnl.sum()),
    }


def risk_stats(eq: pd.Series, bench: pd.Series) -> dict:
    eq, bench = eq.dropna(), bench.reindex(eq.index).ffill()
    r, rb = eq.pct_change().dropna(), bench.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    dd = eq / eq.cummax() - 1
    under, longest, cur = (dd < 0).to_numpy(), 0, 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    m, mb = eq.resample("ME").last().pct_change().dropna(), bench.resample("ME").last().pct_change().dropna()
    y, yb = eq.resample("YE").last().pct_change().dropna(), bench.resample("YE").last().pct_change().dropna()
    up, dn = mb > 0, mb < 0
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    down = r[r < 0]
    return {
        "cagr": cagr, "vol": float(r.std() * np.sqrt(252)),
        "sharpe": float(r.mean() / r.std() * np.sqrt(252)),
        "sortino": float(r.mean() / down.std() * np.sqrt(252)),
        "max_dd": float(dd.min()), "calmar": float(cagr / -dd.min()),
        "longest_underwater_sessions": int(longest), "avg_dd": float(dd.mean()),
        "pct_positive_months": float((m > 0).mean()), "best_month": float(m.max()), "worst_month": float(m.min()),
        "pct_positive_years": float((y > 0).mean()), "best_year": float(y.max()), "worst_year": float(y.min()),
        "years_beating_nifty": f"{int((y > yb.reindex(y.index)).sum())}/{len(y)}",
        "beta_to_nifty": float(r.cov(rb) / rb.var()), "corr_to_nifty": float(r.corr(rb)),
        "upside_capture": float(m[up].mean() / mb[up].mean()), "downside_capture": float(m[dn].mean() / mb[dn].mean()),
        "calendar_years": {str(k.year): round(float(v) * 100, 1) for k, v in y.items()},
    }


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    ivs = build_intervals(read_mcwb(), START, date.today())
    syms = sorted({iv.symbol for iv in ivs})
    ohlc = load_ohlc(db, syms)
    ml = mean_long_series(ohlc, syms)
    closes = pd.DataFrame({s: g.set_index("date")["close_price"] for s, g in ohlc.items()}).sort_index()
    opens = pd.DataFrame({s: g.set_index("date")["open_price"] for s, g in ohlc.items()}).sort_index()
    ml = ml.reindex(closes.index)
    ma = ml.apply(lambda c: c.dropna().rolling(MA).mean().reindex(c.index))
    streak = streaks(ml, ma)
    br, sr = charge_rates()
    last_close = closes.ffill().iloc[-1]

    books, trades = {}, {}
    for name, score, fixed in (("streak", streak, False),
                               ("streak_above30", streak.where(level_at_cross(ml, streak) >= 30), True)):
        log = []
        eq, _ = run(opens, closes, score, ivs, START, None, N, "high", "daily", CAP, SLIP, br, sr,
                    pick_fn=daily_picker(score, "shortest"), fixed_seats=fixed, log=log)
        e = eq["equity"]
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        ep = episodes(pd.DataFrame(log), last_close, e.index[-1])
        ep.to_csv(HERE / "meanlong_streak" / f"episodes_{name}.csv", index=False)
        books[name], trades[name] = e, trade_stats(ep, yrs)
        print(name, f"{trades[name]['win_rate']:.3f}", flush=True)
    gd = pd.read_csv(HERE / "grid10_2008" / "equity.csv", index_col=0, parse_dates=True)
    books.update({"grid10": gd["grid10_k0"], "ew50": gd["ew50"], "nifty50": gd["nifty50"]})
    nifty = gd["nifty50"]
    rep = {"trades": trades, "risk": {k: risk_stats(v, nifty) for k, v in books.items()}}
    (HERE / "meanlong_streak" / "stats.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
