"""Diagnose the Nifty 500 > SMA200 gate on NIFTY200 Growth-only 2-monthly, and credit idle cash with a yield.

1. Drawdown anatomy: the 3 deepest drawdown episodes of the UNGATED book (peak -> trough -> recovery), for each:
   depth, the book's drawdown on the day the gate switched off (if it did inside the episode), and what the gated
   book did over the same window. Also the gated book's own 3 deepest episodes.
2. Cash yield: idle cash accrues at a flat ASSUMED annual rate (0% / 4% / 6%) — not historical liquid-fund data.
Both phases of the 2-monthly schedule.

  .venv/Scripts/python.exe scripts/research_grid/n200_regime_dd.py
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
from n200_regime import START, above_sma  # noqa: E402
from stage1_fib import summ  # noqa: E402
from stage1_trail import run  # noqa: E402

RATES = [0.0, 0.04, 0.06]
OUT = HERE / "n200_regime_dd_results.json"


def episodes(eq: pd.Series, n: int = 3) -> list[dict]:
    """Non-overlapping drawdown episodes, deepest first."""
    dd = eq / eq.cummax() - 1
    out, i = [], 0
    while i < len(dd):
        if dd.iloc[i] < 0:
            j = i
            while j < len(dd) and dd.iloc[j] < 0:
                j += 1
            seg = dd.iloc[i:j]
            out.append({"peak": eq.index[i - 1] if i else eq.index[0], "trough": seg.idxmin(), "depth": float(seg.min()),
                        "recovered": eq.index[j] if j < len(dd) else None})
            i = j
        else:
            i += 1
    return sorted(out, key=lambda e: e["depth"])[:n]


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
    n500 = ix["Nifty 500"].dropna()
    gate = above_sma(n500).reindex(idx).ffill().fillna(False).astype(bool)
    offs = [d for d in cal[1:] if not gate.loc[d] and gate.loc[idx[idx.get_loc(d) - 1]]]   # first close below
    ons = [d for d in cal[1:] if gate.loc[d] and not gate.loc[idx[idx.get_loc(d) - 1]]]
    n500c = n500.reindex(cal)

    months = sorted(period_start_shifted(cal, "monthly", 0))
    out = {"cash": {}, "episodes": {}}
    print("== 2. cash yield (flat assumed rate on idle cash)")
    for ph in (0, 1):
        sched = months[ph::2]
        for rate in RATES:
            for name, g in (("no filter", None), ("G2 Nifty500>SMA200", gate)):
                r = run(cal, ohlc, cols, sched, growth, pool_of, {}, None, br, sr, gate=g, cash_rate=rate)
                s = summ(r)
                out["cash"][f"ph{ph} {name} cash {rate:.0%}"] = s
                print(f"  ph{ph} cash {rate:.0%}  {name:<20} CAGR {s['cagr']:6.1%}  Sharpe {s['sharpe']:.2f}  "
                      f"DD {s['maxdd']:6.1%}  H1 {s['h1']:6.1%}  H2 {s['h2']:6.1%}")
                if rate == 0.0:
                    out.setdefault("eq", {})[f"ph{ph} {name}"] = r["eq"]

    print("\n== 1. drawdown anatomy (cash 0%)")
    fmt = lambda d: d.date().isoformat() if d is not None else "not yet"
    for ph in (0, 1):
        base, gated = out["eq"][f"ph{ph} no filter"], out["eq"][f"ph{ph} G2 Nifty500>SMA200"]
        print(f"\n -- phase {ph}: ungated book's 3 deepest drawdowns")
        rows = []
        for e in episodes(base):
            pk, tr, rc = e["peak"], e["trough"], e["recovered"]
            off = next((d for d in offs if pk <= d <= tr), None)
            on = next((d for d in ons if off is not None and d > off), None)
            dd_at_off = float(base.loc[off] / base.loc[pk] - 1) if off is not None else None
            g_dd = float((gated.loc[pk:tr] / gated.loc[:tr].cummax().loc[pk:tr] - 1).min())
            n_dd = float(n500c.loc[pk:tr].min() / n500c.loc[pk] - 1)
            rows.append({**e, "gate_off": off, "gate_on": on, "book_dd_at_off": dd_at_off,
                         "gated_dd_same_window": g_dd, "nifty500_move": n_dd})
            print(f"  peak {fmt(pk)} trough {fmt(tr)} recovered {fmt(rc)}  depth {e['depth']:6.1%} "
                  f"(Nifty500 {n_dd:6.1%})  gate off {fmt(off)}"
                  + (f" at book DD {dd_at_off:6.1%}, back on {fmt(on)}" if off is not None else " — NEVER switched off")
                  + f"  -> gated book DD in window {g_dd:6.1%}")
        out["episodes"][f"ph{ph} ungated"] = rows
        print(f" -- phase {ph}: gated book's own 3 deepest drawdowns")
        for e in episodes(gated):
            print(f"  peak {fmt(e['peak'])} trough {fmt(e['trough'])} recovered {fmt(e['recovered'])}  "
                  f"depth {e['depth']:6.1%}")
        out["episodes"][f"ph{ph} gated"] = episodes(gated)
    print(f"\ngate switch-offs: {len(offs)} · {[fmt(d) for d in offs]}")
    out.pop("eq")
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print(f"saved {OUT.name}")


if __name__ == "__main__":
    main()
