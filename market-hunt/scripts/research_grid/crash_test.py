"""Crash stress test of the NIFTY50 22d-loser book (N=20, buffer 30), Jan 2008 -> Sep 2020.

Membership: NSE month-end NIFTY 50 files (nifty50_mcwb/, fetched by fetch_mcwb.py). A stock is a
member during month M if it is in the file for month M-1 (the list known when M starts); the first
month uses its own file; missing months carry the last list forward. Old tickers are mapped to the
continuing price series (ALIAS). Prices: Mongo for continuing names; crash_prices/<SYM>.csv for
delisted/merged names (NSE bhavcopy rebuild via fetch_bhav.py, TradingView for RCOM/UNITECH).

Reports: full-period and per-crash stats for the book, a 20-seed random-20 control (same buffer
mechanics), equal-weight PIT NIFTY50 and Nifty 50; coverage of member-months with prices; and the
book's P&L on the famous blow-ups (Satyam, Yes Bank, RCOM, JP Associates, Unitech, Reliance Capital).

  .venv/Scripts/python.exe scripts/research_grid/crash_test.py
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from analyze_book import episodes  # noqa: E402
from grid_meanrev import BENCH, charge_rates, ew_index  # noqa: E402
from membership import MembershipInterval  # noqa: E402
from rotation_roc import run, stats  # noqa: E402

START, END = date(2008, 1, 1), date(2020, 9, 30)
ALIAS = {"HEROHONDA": "HEROMOTOCO", "INFOSYSTCH": "INFY", "REL": "RELINFRA", "SESAGOA": "VEDL",
         "SSLT": "VEDL", "VSNL": "TATACOMM", "MCDOWELL-N": "UNITDSPR", "IBULHSGFIN": "SAMMAANCAP",
         "INFRATEL": "INDUSTOWER", "TATAMOTORS": "TMPV"}
CRASHES = {  # name: (window start, window end) — drawdowns are measured peak->trough inside
    "2008 GFC": ("2008-01-01", "2009-03-31"),
    "2010-11 slowdown": ("2010-11-01", "2011-12-31"),
    "2015-16 correction": ("2015-03-01", "2016-02-29"),
    "2018 IL&FS": ("2018-08-01", "2018-10-31"),
    "2020 COVID": ("2020-01-01", "2020-03-31"),
}
BLOWUPS = ["SATYAMCOMP", "YESBANK", "RCOM", "JPASSOCIAT", "UNITECH", "RELCAPITAL", "SUZLON", "IDEA",
           "RPOWER", "ZEEL"]
N, BUF, LB, SLIP, CAP, SEEDS = 20, 30, 22, 0.0005, 10_000_000, 20
# price history loaded from here: 222-session SMAs (+22 for a slope) must be live before START
WARMUP_FROM = "2006-01-01"


def read_mcwb() -> pd.DataFrame:
    rows = []
    for f in sorted((HERE / "nifty50_mcwb").glob("*.csv")):
        rd = list(csv.reader(io.StringIO(f.read_bytes().decode("latin-1"))))
        hi = next(i for i, r in enumerate(rd) if any("Symbol" in c for c in r))
        si = next(i for i, c in enumerate(rd[hi]) if "Symbol" in c)
        for r in rd[hi + 1:]:
            if len(r) > si and re.match(r"^[A-Z0-9&\-\.]+$", r[si].strip()):
                rows.append((pd.Period(f.stem, "M"), r[si].strip()))
    return pd.DataFrame(rows, columns=["month", "symbol"])


def build_intervals(snap: pd.DataFrame, start: date = START, end: date = END) -> list[MembershipInterval]:
    months = pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M")
    by = {m: set(g.symbol.map(lambda s: ALIAS.get(s, s))) for m, g in snap.groupby("month")}
    first = min(by)
    live, members = None, {}
    for m in months:
        src = by.get(m - 1) or (by.get(m) if m == first else None)
        live = src if src else live            # a missing file carries the last list forward
        members[m] = live or set()
    ivs, open_from = [], {}
    prev: set[str] = set()
    for m in months:
        cur = members[m]
        for s in cur - prev:
            open_from[s] = m.start_time.date()
        for s in prev - cur:
            ivs.append(MembershipInterval(s, open_from.pop(s), m.start_time.date()))
        prev = cur
    ivs += [MembershipInterval(s, f, None) for s, f in open_from.items()]
    return ivs


def load_prices(db, symbols: list[str]):
    pcs = [c for c in db.list_collection_names() if c.startswith("prices_")]
    rows = []
    for c in pcs:
        rows += list(db[c].find({"symbol": {"$in": symbols + [BENCH]}, "date": {"$gte": pd.Timestamp(WARMUP_FROM)}},
                                {"_id": 0, "symbol": 1, "date": 1, "open_price": 1, "close_price": 1}))
    px = pd.DataFrame(rows).rename(columns={"open_price": "open", "close_price": "close"})
    px["date"] = pd.to_datetime(px["date"]).dt.normalize()
    for s in symbols:                                    # local files win for delisted names
        f = HERE / "crash_prices" / f"{s}.csv"
        if f.exists():
            d = pd.read_csv(f)
            d = d.rename(columns={d.columns[0]: "date"}) if "date" not in d.columns else d
            d["date"] = pd.to_datetime(d["date"]).dt.normalize()
            px = pd.concat([px[px.symbol != s], d.assign(symbol=s)[["symbol", "date", "open", "close"]]])
    px = px.drop_duplicates(["symbol", "date"])
    return (px.pivot(index="date", columns="symbol", values="open").sort_index(),
            px.pivot(index="date", columns="symbol", values="close").sort_index())


def window_dd(s: pd.Series, a: str, b: str) -> float:
    w = s[(s.index >= a) & (s.index <= b)]
    return float((w / w.cummax() - 1).min())


def main():
    snap = read_mcwb()
    ivs = build_intervals(snap)
    syms = sorted({iv.symbol for iv in ivs})
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    opens, closes = load_prices(db, syms)
    cal = closes.index[(closes.index >= pd.Timestamp(START)) & (closes.index <= pd.Timestamp(END))]
    closes, opens = closes.loc[: pd.Timestamp(END)], opens.loc[: pd.Timestamp(END)]

    # coverage: member-months that have a price on the month's first session
    from membership import members_as_of
    firsts = pd.Series(cal, index=cal).groupby(cal.to_period("M")).first()
    tot = cov = 0
    missing = {}
    for d in firsts:
        for s in members_as_of(ivs, d.date()):
            tot += 1
            ok = s in closes.columns and not pd.isna(closes[s].loc[:d].tail(5)).all()
            cov += ok
            if not ok:
                missing[s] = missing.get(s, 0) + 1

    stocks = [c for c in closes.columns if c != BENCH]
    roc = closes[stocks].apply(lambda c: c / c.dropna().shift(LB).reindex(c.index) - 1)
    ew = ew_index(closes, ivs)
    br, sr = charge_rates()

    log = []
    eq, traded = run(opens, closes, roc, ivs, START, END, N, "low", "monthly", CAP, SLIP, br, sr,
                     buffer=BUF, log=log)
    rnd = [run(opens, closes, roc, ivs, START, END, N, "random", "monthly", CAP, SLIP, br, sr,
               rng=np.random.default_rng(k), buffer=BUF)[0]["equity"] for k in range(SEEDS)]
    ewc = ew.reindex(eq.index).ffill()
    nifty = closes[BENCH].reindex(eq.index).ffill()

    rep = {"period": f"{eq.index[0].date()} -> {eq.index[-1].date()}",
           "coverage_member_months": {"total": tot, "priced": cov, "pct": cov / tot, "missing": missing},
           "full": {"book": stats(eq["equity"]),
                    "random_cagr_mean": float(np.mean([stats(r)["cagr"] for r in rnd])),
                    "random_cagr_p05": float(np.percentile([stats(r)["cagr"] for r in rnd], 5)),
                    "random_cagr_p95": float(np.percentile([stats(r)["cagr"] for r in rnd], 95)),
                    "random_maxdd_mean": float(np.mean([stats(r)["max_dd"] for r in rnd])),
                    "ew50": stats(ewc), "nifty50": stats(nifty),
                    "turnover_pa": traded / eq["equity"].mean() / ((eq.index[-1] - eq.index[0]).days / 365.25)},
           "crashes": {}}
    for name, (a, b) in CRASHES.items():
        rep["crashes"][name] = {"book": window_dd(eq["equity"], a, b),
                                "random_mean": float(np.mean([window_dd(r, a, b) for r in rnd])),
                                "random_worst": float(np.min([window_dd(r, a, b) for r in rnd])),
                                "ew50": window_dd(ewc, a, b), "nifty50": window_dd(nifty, a, b)}
    # recovery time after the GFC low: first date the book regains its pre-crash peak
    e = eq["equity"]
    pk = e[: "2009-03-31"].cummax()
    low_day = (e[: "2009-03-31"] / pk - 1).idxmin()
    peak_val = pk.loc[low_day]
    back = e[e.index > low_day]
    rec = back[back >= peak_val].index.min()
    rep["gfc_recovery"] = {"low_day": str(low_day.date()), "recovered_on": str(rec.date()) if pd.notna(rec) else None}

    lg = pd.DataFrame(log)
    ep = episodes(lg, closes.ffill().loc[eq.index[-1]], eq.index[-1])
    rep["blowups"] = {}
    for s in BLOWUPS:
        g = ep[ep.symbol == s]
        rep["blowups"][s] = {"episodes": int(len(g)), "pnl_rupees": float(g.pnl.sum()),
                             "pnl_pct_of_start_capital": float(g.pnl.sum() / CAP),
                             "worst_episode": (g.nsmallest(1, "ret")[["start", "end", "ret"]]
                                               .assign(start=lambda x: x.start.astype(str).str[:10],
                                                       end=lambda x: x.end.astype(str).str[:10])
                                               .round(3).to_dict("records") if len(g) else [])}
    per = ep.groupby("symbol").pnl.sum().sort_values()
    rep["worst10_stocks"] = per.head(10).round(0).to_dict()
    rep["best10_stocks"] = per.tail(10)[::-1].round(0).to_dict()

    out = HERE / "crash_test"
    out.mkdir(exist_ok=True)
    pd.DataFrame({"book": eq["equity"], "ew50": CAP * ewc / ewc.iloc[0], "nifty50": CAP * nifty / nifty.iloc[0]}) \
        .to_csv(out / "equity.csv")
    ep.to_csv(out / "episodes.csv", index=False)
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
