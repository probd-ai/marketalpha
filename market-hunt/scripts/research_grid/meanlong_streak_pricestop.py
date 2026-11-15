"""Top-10 shortest-streak book with a second, PRICE exit.

Exit 1 (as before): mean_long drops below its own 222-session SMA (or the stock leaves the index).
Exit 2 (new): the stock has been below its 222-session midpoint — (highest high + lowest low) / 2 over 222 sessions —
for 5 consecutive sessions:
  close  the CLOSE is below the midpoint 5 sessions running
  bar    the whole bar is below it (HIGH below the midpoint) 5 sessions running
A stock stopped out by exit 2 stays ineligible until it makes a FRESH up-cross (a new streak), so it is not bought back
the next day. Entry, seats and sizing are the daily streak book's (meanlong_streak.py: 10 names, shortest streak,
retained names never trimmed). Point-in-time NIFTY 50, Jan 2008 -> latest, statutory charges + 5 bp.

  .venv/Scripts/python.exe scripts/research_grid/meanlong_streak_pricestop.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from analyze_book import episodes  # noqa: E402
from crash_test import CRASHES, build_intervals, read_mcwb, window_dd  # noqa: E402
from grid_meanrev import BENCH, charge_rates  # noqa: E402
from meanlong_slope import ERAS, MA, START, load_ohlc, mean_long_series, stats  # noqa: E402
from meanlong_streak import daily_picker, streaks  # noqa: E402
from rotation_roc import run  # noqa: E402

N, SLIP, CAP, MID, DAYS = 10, 0.0005, 10_000_000.0, 222, 5


def run_length(cond: pd.Series) -> pd.Series:
    """Consecutive True count ending at each row (0 when False)."""
    c = cond.astype(int)
    return c.groupby((c != c.shift()).cumsum()).cumsum() * c


def stop_mask(ohlc: dict, streak: pd.DataFrame, kind: str) -> pd.DataFrame:
    """True from the first price-stop trigger inside a streak until that streak ends (blocks holding and re-entry)."""
    out = {}
    for s in streak.columns:
        g = ohlc[s].set_index("date")
        mid = (g["high_price"].rolling(MID).max() + g["low_price"].rolling(MID).min()) / 2
        below = (g["close_price"] if kind == "close" else g["high_price"]) < mid
        trig = (run_length(below.where(mid.notna(), False)) >= DAYS).reindex(streak.index).fillna(False)
        st = streak[s]
        run_id = st.isna().cumsum()                      # a new id every time the streak breaks
        hit = trig.where(st.notna(), False).astype(int).groupby(run_id).cummax().astype(bool)
        out[s] = hit & st.notna()
    return pd.DataFrame(out)


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

    def era(eq):
        out = {"full": stats(eq)}
        out.update({n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()})
        return out

    rep, curves = {}, {}
    for name, kind in (("baseline", None), ("stop_close5", "close"), ("stop_bar5", "bar")):
        score = streak if kind is None else streak.where(~stop_mask(ohlc, streak, kind))
        log = []
        eq, traded = run(opens, closes, score, ivs, START, None, N, "high", "daily", CAP, SLIP, br, sr,
                         pick_fn=daily_picker(score, "shortest"), log=log)
        e = eq["equity"]
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        ep = episodes(pd.DataFrame(log), last_close, e.index[-1])
        c = ep[ep.status == "closed"]
        rep[name] = {"book": era(e), "turnover_per_year": traded / float(e.mean()) / yrs,
                     "trades_per_year": len(c) / yrs, "hold_days_median": float(c.days.median()),
                     "win_rate": float((c.ret > 0).mean()), "avg_win": float(c[c.ret > 0].ret.mean()),
                     "avg_loss": float(c[c.ret <= 0].ret.mean()),
                     "crashes": {k: window_dd(e, a, b) for k, (a, b) in CRASHES.items()}}
        curves[name] = e
        print(name, f"{stats(e)['cagr']:.4f} dd {stats(e)['max_dd']:.3f}", flush=True)
    out = HERE / "meanlong_streak_pricestop"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
