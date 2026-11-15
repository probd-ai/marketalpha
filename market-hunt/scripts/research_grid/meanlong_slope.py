"""mean_long vs its own 222-session SMA ("slope" signal) on point-in-time NIFTY 50, Jan 2008 -> latest.

Rule (user, 2026-10-04): buy a member when its TrueVX mean_long crosses ABOVE the 222-session SMA of mean_long;
sell when it crosses BELOW. Signals at the close of day t, traded at the open of t+1. A stock that leaves the index
is sold (at its open, or its last close if it stopped trading).

  A "cross": enter only on an up-cross while a member (the literal rule).
  B "above": hold whenever mean_long > its SMA (also takes members that are already above).
Sizing: each position is 1/50 of equity at entry; uninvested money is cash at 0%. Costs: statutory charges
(BrokerageCalculator) + 5 bp slippage per side.

mean_long is RECOMPUTED from prices with indicator_engine.calculate_truevx_ranking (stored TrueVX starts 2018), and
checked against the stored values where they overlap. Membership: NSE month-end NIFTY 50 files (crash_test.py).
Prices: Mongo (continuing names, from 2005) + crash_prices/ for delisted names (OHLC, mostly from 2007).

Benchmarks: equal-weight PIT NIFTY 50 (daily rebalanced, no costs), the same scaled by the book's exposure, and the
Nifty 50 price index.

  .venv/Scripts/python.exe scripts/research_grid/meanlong_slope.py
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
from indicator_engine import calculate_truevx_ranking  # noqa: E402
from membership import members_as_of  # noqa: E402

START = date(2008, 1, 1)
LOAD_FROM = "2005-01-01"
MA = 222
SLOTS = 50
SLIP = 0.0005
CAP = 10_000_000.0
ERAS = {"2008-2013": ("2008-01-01", "2013-12-31"), "2014-2019": ("2014-01-01", "2019-12-31"),
        "2020-2026": ("2020-01-01", "2030-01-01")}


def load_ohlc(db, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """symbol -> DataFrame[date, open_price, high_price, low_price, close_price]; local files win for delisted."""
    rows = []
    for c in [c for c in db.list_collection_names() if c.startswith("prices_")]:
        rows += list(db[c].find({"symbol": {"$in": symbols + [BENCH]}, "date": {"$gte": pd.Timestamp(LOAD_FROM)}},
                                {"_id": 0, "symbol": 1, "date": 1, "open_price": 1, "high_price": 1, "low_price": 1,
                                 "close_price": 1}))
    px = pd.DataFrame(rows)
    px["date"] = pd.to_datetime(px["date"]).dt.normalize()
    out = {s: g.drop(columns="symbol").drop_duplicates("date").sort_values("date").reset_index(drop=True)
           for s, g in px.groupby("symbol")}
    for s in symbols:
        f = HERE / "crash_prices" / f"{s}.csv"
        if f.exists():
            d = pd.read_csv(f)
            dc = "date" if "date" in d.columns else d.columns[0]
            d = pd.DataFrame({"date": pd.to_datetime(d[dc]).dt.normalize(), "open_price": d["open"],
                              "high_price": d["high"], "low_price": d["low"], "close_price": d["close"]})
            out[s] = d.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return out


def mean_long_series(ohlc: dict[str, pd.DataFrame], symbols: list[str]) -> pd.DataFrame:
    bench = ohlc[BENCH].to_dict("records")
    cols = {}
    for s in symbols:
        if s not in ohlc or len(ohlc[s]) < 300:
            continue
        res = calculate_truevx_ranking(ohlc[s].to_dict("records"), bench)
        if res:
            r = pd.DataFrame(res)
            cols[s] = pd.Series(r["mean_long"].values, index=pd.to_datetime(r["date"])).astype(float)
    return pd.DataFrame(cols).sort_index()


def check_against_stored(db, ml: pd.DataFrame, symbols=("RELIANCE", "HDFCBANK", "INFY", "ITC", "SBIN")) -> dict:
    out = {}
    for s in symbols:
        docs = list(db.indicators.find({"symbol": s, "indicator_type": "truevx", "base_symbol": BENCH,
                                        "date": {"$gte": pd.Timestamp("2019-06-01")}}, {"_id": 0, "date": 1,
                                                                                         "data.mean_long": 1}))
        st = pd.Series({pd.Timestamp(d["date"]).normalize(): (d.get("data") or {}).get("mean_long") for d in docs},
                       dtype=float).dropna()
        both = pd.concat([st, ml[s]], axis=1, keys=["stored", "recomputed"]).dropna()
        out[s] = {"days": int(len(both)), "max_abs_diff": float((both.stored - both.recomputed).abs().max()),
                  "corr": float(both.stored.corr(both.recomputed))}
    return out


def stats(eq: pd.Series) -> dict:
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    return {"cagr": float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1),
            "max_dd": float((eq / eq.cummax() - 1).min()),
            "sharpe": float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else None}


def run(variant: str, opens, closes, ml, ma, ivs, cal, br, sr):
    above = ml > ma
    cross_up = above & ~above.shift(1, fill_value=False)
    cash, pos, cost = CAP, {}, {}
    eq, expo, trips, traded = [], [], [], 0.0
    entry_day = {}
    last_close = closes.ffill()
    for i, d in enumerate(cal):
        p = cal[i - 1] if i else None
        mem = set(members_as_of(ivs, d.date()))
        # sells first: below the SMA at yesterday's close, or out of the index
        for s in list(pos):
            out_idx = s not in mem
            below = p is not None and s in above.columns and not pd.isna(ml.at[p, s]) and not bool(above.at[p, s])
            if not (out_idx or below):
                continue
            o = opens.at[d, s] if s in opens.columns else np.nan
            if pd.isna(o):
                if not out_idx:
                    continue                      # halted: try again tomorrow
                o = last_close.at[d, s]           # left the index and stopped trading: last close
            v = pos[s] * o * (1 - SLIP) * (1 - sr)
            cash += v
            traded += v
            trips.append({"symbol": s, "entry": entry_day[s], "exit": d, "ret": v / cost[s] - 1,
                          "why": "index exit" if out_idx else "cross below"})
            pos.pop(s), cost.pop(s), entry_day.pop(s)
        # buys
        if p is not None:
            val = cash + sum(q * last_close.at[d, s] for s, q in pos.items())
            slot = val / SLOTS
            sig = cross_up if variant == "cross" else above
            for s in sorted(mem):
                if s in pos or s not in sig.columns or pd.isna(ml.at[p, s]) or pd.isna(ma.at[p, s]):
                    continue
                if not bool(sig.at[p, s]):
                    continue
                o = opens.at[d, s] if s in opens.columns else np.nan
                if pd.isna(o) or cash < slot * 0.5:
                    continue
                spend = min(slot, cash)
                q = spend / (o * (1 + SLIP) * (1 + br))
                pos[s], cost[s], entry_day[s] = q, spend, d
                cash -= spend
                traded += spend
        held = sum(q * last_close.at[d, s] for s, q in pos.items())
        eq.append(cash + held)
        expo.append(held / (cash + held))
    return (pd.Series(eq, index=cal), pd.Series(expo, index=cal), pd.DataFrame(trips), traded)


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    ivs = build_intervals(read_mcwb(), START, date.today())
    syms = sorted({iv.symbol for iv in ivs})
    ohlc = load_ohlc(db, syms)
    ml = mean_long_series(ohlc, syms)
    check = check_against_stored(db, ml)
    closes = pd.DataFrame({s: g.set_index("date")["close_price"] for s, g in ohlc.items()}).sort_index()
    opens = pd.DataFrame({s: g.set_index("date")["open_price"] for s, g in ohlc.items()}).sort_index()
    cal = closes.index[(closes.index >= pd.Timestamp(START)) & closes[BENCH].notna().to_numpy()]
    ml = ml.reindex(closes.index)
    # SMA of mean_long over the stock's own sessions
    ma = ml.apply(lambda c: c.dropna().rolling(MA).mean().reindex(c.index))
    br, sr = charge_rates()

    # coverage: member-days with a usable signal (mean_long and its SMA live)
    tot = ok = 0
    for d in cal[::5]:
        for s in members_as_of(ivs, d.date()):
            tot += 1
            ok += int(s in ma.columns and not pd.isna(ma.at[d, s]))
    # equal-weight PIT NIFTY 50
    rets = closes.drop(columns=[BENCH]).pct_change(fill_method=None)
    ew_r = pd.Series([rets.loc[d, [s for s in members_as_of(ivs, d.date()) if s in rets.columns]].mean()
                      for d in cal], index=cal).fillna(0)
    ew = (1 + ew_r).cumprod()
    nifty = closes[BENCH].reindex(cal)

    rep = {"period": f"{cal[0].date()} -> {cal[-1].date()}", "ma": MA, "slots": SLOTS,
           "mean_long_check_vs_stored": check, "signal_coverage_member_days": ok / tot}
    out = HERE / "meanlong_slope"
    out.mkdir(exist_ok=True)
    curves = {"ew50": ew * CAP, "nifty50": nifty / nifty.iloc[0] * CAP}
    for variant in ("cross", "above"):
        eq, expo, trips, traded = run(variant, opens, closes, ml, ma, ivs, cal, br, sr)
        matched = CAP * (1 + expo.shift(1).fillna(0) * ew_r).cumprod()
        yrs = (cal[-1] - cal[0]).days / 365.25
        r = {"full": {"book": stats(eq), "ew50": stats(ew), "ew50_exposure_matched": stats(matched),
                      "nifty50": stats(nifty)},
             "avg_exposure": float(expo.mean()), "trades_per_year": len(trips) / yrs,
             "turnover_per_year": traded / float(eq.mean()) / yrs,
             "round_trips": int(len(trips)), "hit_rate": float((trips.ret > 0).mean()) if len(trips) else None,
             "avg_trade_ret": float(trips.ret.mean()) if len(trips) else None,
             "avg_hold_days": float((trips.exit - trips.entry).dt.days.mean()) if len(trips) else None,
             "eras": {}, "crashes": {}}
        for name, (a, b) in ERAS.items():
            w = (cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b))
            r["eras"][name] = {"book": stats(eq[w]), "ew50": stats(ew[w]), "ew50_exposure_matched": stats(matched[w]),
                               "nifty50": stats(nifty[w]), "avg_exposure": float(expo[w].mean())}
        for name, (a, b) in CRASHES.items():
            r["crashes"][name] = {"book": window_dd(eq, a, b), "ew50": window_dd(ew, a, b),
                                  "nifty50": window_dd(nifty, a, b), "exposure_at_start": float(expo[expo.index >= a].iloc[0])}
        rep[variant] = r
        curves[variant] = eq
        trips.to_csv(out / f"trips_{variant}.csv", index=False)
    # selection test (gross, daily rebalanced, fully invested): equal-weight the members that are above their SMA at
    # yesterday's close vs all members — isolates whether the signal PICKS better stocks, apart from the cash it holds
    abv = (ml > ma).shift(1)
    sel_r, below_r = [], []
    for d in cal:
        mem = [s for s in members_as_of(ivs, d.date()) if s in rets.columns and s in abv.columns]
        up = [s for s in mem if abv.at[d, s] is True or abv.at[d, s] == 1]
        dn = [s for s in mem if s not in up and not pd.isna(ma.shift(1).at[d, s])]
        sel_r.append(rets.loc[d, up].mean() if up else 0.0)
        below_r.append(rets.loc[d, dn].mean() if dn else 0.0)
    sel = (1 + pd.Series(sel_r, index=cal).fillna(0)).cumprod()
    blw = (1 + pd.Series(below_r, index=cal).fillna(0)).cumprod()
    rep["selection_gross"] = {"above_ew": stats(sel), "below_ew": stats(blw), "all_ew": stats(ew),
                              "eras": {n: {"above_ew": stats(sel[(cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b))]),
                                           "below_ew": stats(blw[(cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b))]),
                                           "all_ew": stats(ew[(cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b))])}
                                       for n, (a, b) in ERAS.items()}}
    curves["above_ew_gross"], curves["below_ew_gross"] = sel * CAP, blw * CAP
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
