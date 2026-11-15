"""NIFTY200 Growth-only, 2-monthly rebalance, + a market downtrend filter (stay in cash while the market is down).

Book: point-in-time NIFTY200 -> Growth/Alpha stack (truevx_score>90, mean_short>80, mean_mid>60, mean_long>50) on the
signal close -> hold all, equal weight; rebalance every 2nd month's first session (both phases run).
Filters (fixed a priori, nothing tuned), evaluated at every CLOSE:
  G1  Nifty 50 close > SMA200
  G2  Nifty 500 close > SMA200
  G3  Nifty Midcap 150 close > SMA200
  G4  Nifty 500 SMA50 > SMA200
  G5  breadth: > 50% of point-in-time NIFTY200 members close above their own SMA200
Off -> everything sold at the next open, cash; back on -> rebalance into the names eligible at that close, next open.
Costs: statutory charges + 5 bp slippage. Rf = 0 (cash earns nothing — understates the gated books slightly).

  .venv/Scripts/python.exe scripts/research_grid/n200_regime.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from fund.data import BENCH, charge_rates, load_prices, load_truevx  # noqa: E402
from fund.engine.calendar import period_start_shifted, trading_days  # noqa: E402
from fund.engine.signals import alpha_eligible  # noqa: E402
from lowrange_breakout import load_ohlc  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402
from stage1_fib import summ  # noqa: E402
from stage1_trail import run  # noqa: E402

START = date(2020, 10, 1)
OUT = HERE / "n200_regime_results.json"


def above_sma(c: pd.Series, n: int = 200) -> pd.Series:
    c = c.dropna()
    return c > c.rolling(n, min_periods=n).mean()


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    today = date.today()
    iv = load_membership(db, "NIFTY200", today)
    syms = sorted(union_over(iv, START, today))
    since = datetime(2019, 1, 1)
    ohlc = load_ohlc(db, syms, since)
    cols = [c for c in ohlc["close"].columns if c != BENCH]
    idx = ohlc["close"].index
    elig = alpha_eligible(load_truevx(db, cols, since), idx, cols)
    cal = trading_days(ohlc["close"][BENCH], START)
    pool_of = lambda day: members_as_of(iv, day)
    br, sr = charge_rates()

    def growth(d, held=None):
        p = idx[idx.get_loc(d) - 1]
        return [s for s in sorted(pool_of(d.date())) if s in elig.columns and bool(elig.at[p, s])]

    _, ix = load_prices(db, ["Nifty 500", "Nifty Midcap 150"], datetime(2018, 1, 1))
    n500 = ix["Nifty 500"].dropna()
    above = ohlc["close"][cols].apply(above_sma)                     # each stock vs its own SMA200
    breadth = pd.Series({d: above.loc[d, [s for s in pool_of(d.date()) if s in above.columns]].mean()
                         for d in idx[idx >= pd.Timestamp(START) - pd.Timedelta(days=10)]})
    raw = {
        "G1 Nifty50>SMA200": above_sma(ix[BENCH]),
        "G2 Nifty500>SMA200": above_sma(n500),
        "G3 Mid150>SMA200": above_sma(ix["Nifty Midcap 150"]),
        "G4 Nifty500 SMA50>SMA200": n500.rolling(50).mean() > n500.rolling(200).mean(),
        "G5 breadth>50%": breadth > 0.5,
    }
    gates = {k: v.reindex(idx).ffill().fillna(False).astype(bool) for k, v in raw.items()}

    months = sorted(period_start_shifted(cal, "monthly", 0))
    out = {}
    for ph in (0, 1):
        sched = months[ph::2]
        print(f"\n=== 2-monthly phase {ph} ({sched[0].strftime('%b')}, {sched[1].strftime('%b')}, ...)")
        for name, g in [("no filter", None)] + list(gates.items()):
            s = summ(run(cal, ohlc, cols, sched, growth, pool_of, {}, None, br, sr, gate=g))
            if g is not None:
                gc = g.loc[cal]
                s["risk_on"] = float(gc.mean())
                s["switch_offs"] = int((gc.astype(int).diff() == -1).sum())
            out[f"ph{ph} {name}"] = s
            extra = f"  on {s['risk_on']:.0%}  offs {s['switch_offs']:>2}" if g is not None else ""
            print(f"  {name:<26} CAGR {s['cagr']:6.1%}  Sharpe {s['sharpe']:.2f}  DD {s['maxdd']:6.1%}  "
                  f"H1 {s['h1']:6.1%}  H2 {s['h2']:6.1%}  turnover {s['turnover_yr']:.1f}x{extra}")
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print(f"\nsaved {OUT.name}")


if __name__ == "__main__":
    main()
