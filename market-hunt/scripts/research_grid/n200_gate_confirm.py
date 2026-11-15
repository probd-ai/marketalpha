"""Stop the Nifty 500 > SMA200 gate from whipsawing: confirmation and band variants.

Book: NIFTY200 Growth-only, 2-monthly (both phases), gate checked at every close (off -> all out next open, cash;
back on -> rebalance into the names eligible then). Gates on Nifty 500 vs its SMA200:
  plain     on while close > SMA (the G2 baseline)
  confirm3  switches off after 3 consecutive closes below the SMA, back on after 3 consecutive closes above
  band2     switches off on a close < 0.98 * SMA, back on on a close > 1.02 * SMA, otherwise keeps its state
Fixed a priori (2 variants, nothing swept). Idle cash at a flat ASSUMED 0% and 6%.

  .venv/Scripts/python.exe scripts/research_grid/n200_gate_confirm.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

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
from n200_regime import START  # noqa: E402
from n200_regime_dd import episodes  # noqa: E402
from stage1_fib import summ  # noqa: E402
from stage1_trail import run  # noqa: E402

OUT = HERE / "n200_gate_confirm_results.json"


def hysteresis(close: pd.Series, off_when, on_when) -> pd.Series:
    """State machine: starts ON once the SMA exists; off_when / on_when are boolean Series on the same index."""
    state, out = True, {}
    for d in close.index:
        if pd.isna(off_when.loc[d]) and pd.isna(on_when.loc[d]):
            out[d] = False
            continue
        if state and bool(off_when.loc[d]):
            state = False
        elif not state and bool(on_when.loc[d]):
            state = True
        out[d] = state
    return pd.Series(out)


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

    _, ix = load_prices(db, ["Nifty 500"], datetime(2018, 1, 1))
    c = ix["Nifty 500"].dropna()
    sma = c.rolling(200, min_periods=200).mean()
    valid = sma.notna()
    below, above = (c < sma).where(valid), (c > sma).where(valid)
    raw = {
        "plain": (c > sma).where(valid, False),
        "confirm3": hysteresis(c, below.rolling(3).sum() == 3, above.rolling(3).sum() == 3),
        "band2": hysteresis(c, (c < 0.98 * sma).where(valid), (c > 1.02 * sma).where(valid)),
    }
    gates = {k: v.reindex(idx).ffill().fillna(False).astype(bool) for k, v in raw.items()}
    for k, g in gates.items():
        gc = g.loc[cal]
        offs = [d for d in gc.index[1:] if not gc.loc[d] and gc.shift(1).loc[d]]
        print(f"gate {k:<9} risk-on {gc.mean():.0%}  switch-offs {len(offs):>2}: {[d.date().isoformat() for d in offs]}")

    months = sorted(period_start_shifted(cal, "monthly", 0))
    out = {}
    for ph in (0, 1):
        sched = months[ph::2]
        print(f"\n=== 2-monthly phase {ph}")
        for rate in (0.0, 0.06):
            for name, g in [("no filter", None)] + list(gates.items()):
                r = run(cal, ohlc, cols, sched, growth, pool_of, {}, None, br, sr, gate=g, cash_rate=rate)
                s = summ(r)
                s["worst3"] = [round(e["depth"], 3) for e in episodes(r["eq"])]
                out[f"ph{ph} cash{rate:.0%} {name}"] = s
                print(f"  cash {rate:.0%}  {name:<9} CAGR {s['cagr']:6.1%}  Sharpe {s['sharpe']:.2f}  DD {s['maxdd']:6.1%}  "
                      f"H1 {s['h1']:6.1%}  H2 {s['h2']:6.1%}  turnover {s['turnover_yr']:.1f}x  "
                      f"worst-3 {[f'{x:.1%}' for x in s['worst3']]}")
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print(f"\nsaved {OUT.name}")


if __name__ == "__main__":
    main()
