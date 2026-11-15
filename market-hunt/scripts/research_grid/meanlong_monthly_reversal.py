"""Monthly short-term reversal inside the streak universe (user idea, 2026-10-04).

Universe: point-in-time NIFTY 50 members whose mean_long is above its own 222-session SMA (the streak filter).
On the first session of each month, from the PREVIOUS calendar month's return (signal at the prior close, fills at
the open):
  1. sell every holding whose last-month return was positive (whole position);
  2. buy 2% of equity in each universe stock whose last-month return was negative — adding to an existing holding —
     never letting a holding exceed 5% of equity; when cash runs short the biggest losers go first.
Uninvested money is cash at 0%. Index exits are sold the next session (open, or last close if it stopped trading).
  variant A  a holding that leaves the universe is kept until a positive month sells it
  variant B  it is also sold at the month start when it is no longer in the universe
Controls: the same rule on ALL NIFTY 50 members (no streak filter), equal-weight of the streak universe (monthly),
equal-weight NIFTY 50, Nifty 50. Statutory charges + 5 bp; Jan 2008 -> latest; mean_long recomputed from prices.

  .venv/Scripts/python.exe scripts/research_grid/meanlong_monthly_reversal.py
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
from crash_test import CRASHES, build_intervals, read_mcwb, window_dd  # noqa: E402
from grid_meanrev import BENCH, charge_rates  # noqa: E402
from meanlong_slope import ERAS, MA, START, load_ohlc, mean_long_series, stats  # noqa: E402
from meanlong_streak import streaks  # noqa: E402
from membership import members_as_of  # noqa: E402

SLIP, CAP, ADD, MAXW = 0.0005, 10_000_000.0, 0.02, 0.05


def simulate(opens, closes, mret, universe, ivs, cal, br, sr, exit_on_leave: bool):
    """universe(prev_session) -> set of eligible symbols. mret: calendar-month returns indexed by month-end."""
    cash, qty = CAP, {}
    cclose = closes.ffill()
    last_bar = {s: closes[s].last_valid_index() for s in closes.columns}
    month_start = set(pd.Series(cal, index=cal).groupby(cal.to_period("M")).first())
    eq, inv, names, traded, pend = [], [], [], 0.0, set()

    def px_open(d, s):
        o = opens.at[d, s] if s in opens.columns else np.nan
        return o

    def sell(d, s, o):
        nonlocal cash, traded
        v = qty.pop(s) * o
        cash += v * (1 - SLIP) * (1 - sr)
        traded += v

    for i, d in enumerate(cal):
        # forced index exits queued yesterday
        for s in list(pend):
            if s not in qty:
                pend.discard(s); continue
            o = px_open(d, s)
            if pd.isna(o):
                lb = last_bar.get(s)
                if lb is None or lb >= d:
                    continue
                o = closes.at[lb, s]
            sell(d, s, o); pend.discard(s)
        if d in month_start and i > 0:
            prev = cal[i - 1]
            last_m = (d.to_period("M") - 1).to_timestamp("M")
            r = mret.loc[last_m] if last_m in mret.index else pd.Series(dtype=float)
            uni = universe(prev, d)
            for s in list(qty):
                o = px_open(d, s)
                if pd.isna(o):
                    continue
                if (not pd.isna(r.get(s, np.nan)) and r.get(s) > 0) or (exit_on_leave and s not in uni):
                    sell(d, s, o)
            equity = cash + sum(q * (px_open(d, s) if not pd.isna(px_open(d, s)) else cclose.at[prev, s])
                                for s, q in qty.items())
            losers = sorted((s for s in uni if not pd.isna(r.get(s, np.nan)) and r.get(s) < 0
                             and not pd.isna(px_open(d, s))), key=lambda s: r.get(s))
            for s in losers:
                o = px_open(d, s)
                room = MAXW * equity - qty.get(s, 0.0) * o
                spend = min(ADD * equity, room, cash)
                if spend <= equity * 1e-4:
                    continue
                qty[s] = qty.get(s, 0.0) + spend / (o * (1 + SLIP) * (1 + br))
                cash -= spend
                traded += spend
        held = sum(q * cclose.at[d, s] for s, q in qty.items())
        eq.append(cash + held); inv.append(held / (cash + held)); names.append(len(qty))
        if i + 1 < len(cal):
            nxt = set(members_as_of(ivs, cal[i + 1].date()))
            pend |= {s for s in qty if s not in nxt}
    return pd.Series(eq, index=cal), pd.Series(inv, index=cal), pd.Series(names, index=cal), traded


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
    cal = closes.index[(closes.index >= pd.Timestamp(START)) & closes[BENCH].notna().to_numpy()]
    stocks = closes.drop(columns=[BENCH])
    mret = stocks.resample("ME").last().pct_change(fill_method=None)       # calendar-month returns, own last close

    def streak_uni(prev, d):
        return {s for s in members_as_of(ivs, d.date()) if s in streak.columns and not pd.isna(streak.at[prev, s])}

    def all_uni(prev, d):
        return {s for s in members_as_of(ivs, d.date()) if s in stocks.columns}

    def era(eq):
        out = {"full": stats(eq)}
        out.update({n: stats(eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]) for n, (a, b) in ERAS.items()})
        return out

    rets = stocks.pct_change(fill_method=None)
    abv = streak.notna().shift(1, fill_value=False)
    ew_u, ew_50 = [], []
    for d in cal:
        mem = [s for s in members_as_of(ivs, d.date()) if s in rets.columns]
        up = [s for s in mem if bool(abv.at[d, s])]
        ew_u.append(rets.loc[d, up].mean() if up else 0.0)
        ew_50.append(rets.loc[d, mem].mean() if mem else 0.0)
    ewu = (1 + pd.Series(ew_u, index=cal).fillna(0)).cumprod() * CAP
    ew = (1 + pd.Series(ew_50, index=cal).fillna(0)).cumprod() * CAP
    nifty = closes[BENCH].reindex(cal) / closes[BENCH].reindex(cal).iloc[0] * CAP
    ewu_r = ewu.pct_change().fillna(0)
    ew_r = ew.pct_change().fillna(0)
    rep, curves = {"period": f"{cal[0].date()} -> {cal[-1].date()}", "rule": "buy 2%/month on a down month to a 5% cap, "
                   "sell on an up month"}, {}
    for name, uni, leave in (("A_streak_keep", streak_uni, False), ("B_streak_exit", streak_uni, True),
                             ("C_all50_keep", all_uni, False)):
        e, inv, nm, traded = simulate(opens, closes, mret, uni, ivs, cal, br, sr, leave)
        yrs = (cal[-1] - cal[0]).days / 365.25
        rep[name] = {"book": era(e), "invested": float(inv.mean()), "avg_names": float(nm.mean()),
                     "max_names": int(nm.max()), "turnover_per_year": traded / float(e.mean()) / yrs,
                     "crashes": {c: window_dd(e, a, b) for c, (a, b) in CRASHES.items()},
                     # same day-by-day invested share, put in the equal-weight streak universe / NIFTY 50 instead
                     "exposure_matched_ew_universe": era(CAP * (1 + inv.shift(1).fillna(0) * ewu_r).cumprod()),
                     "exposure_matched_ew50": era(CAP * (1 + inv.shift(1).fillna(0) * ew_r).cumprod())}
        curves[name] = e
        print(name, f"{stats(e)['cagr']:.4f} dd {stats(e)['max_dd']:.3f} invested {inv.mean():.2f}", flush=True)

    rep["ew_streak_universe_gross"] = era(ewu)
    rep["ew50"], rep["nifty50"] = era(ew), era(nifty)
    rep["crashes_bench"] = {c: {"ew50": window_dd(ew, a, b), "nifty50": window_dd(nifty, a, b)} for c, (a, b) in CRASHES.items()}
    curves.update({"ew_streak_universe": ewu, "ew50": ew, "nifty50": nifty})
    out = HERE / "meanlong_monthly_reversal"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
