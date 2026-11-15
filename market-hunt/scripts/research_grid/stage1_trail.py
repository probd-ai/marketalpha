"""Stage 1 (hold EVERY Growth-eligible name in NIFTY200 ∩ NIFTYMIDCAP150, equal weight, monthly) + a trailing stop.

Book: as eligible_all.py — monthly (signal = last session of the previous month, trade at the first session's open),
equal weight over all eligible names, names no longer eligible are sold at the rebalance, a name that leaves the pool
mid-month is sold at the next open.
Trailing stop per position (no partial bookings):
  R      = MULT * (high - low) of the last completed calendar month before the ENTRY (fixed while held)
  stop   = entry - R; intraday high >= entry + k*R (k >= 1) -> stop = entry + (k-1)*R (only ever rises)
  exit   daily close < stop -> whole position sold at the next open; cash waits for the next monthly rebalance,
         where the name may re-enter (fresh entry, fresh R) if it is still eligible
A held name keeps its entry / R / stop through monthly resizing. MULT = None is the plain stage-1 book (regression
check against eligible_all.py: 32.2% CAGR). Costs: statutory charges + 5 bp slippage. Rf = 0.

  .venv/Scripts/python.exe scripts/research_grid/stage1_trail.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from datetime import datetime  # noqa: E402

from fund.data import BENCH, charge_rates, load_from, load_prices, load_truevx  # noqa: E402
from fund.engine.calendar import period_start_shifted, trading_days  # noqa: E402
from fund.engine.signals import alpha_eligible  # noqa: E402
from lowrange_breakout import SPLIT, cagr_between, load_ohlc, stats  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402

START = date(2020, 10, 1)
UNIVERSES = ("NIFTY200", "NIFTYMIDCAP150")
SLIP = 0.0005
MULTS = [2.0]                    # 1.5 / 2.5 / 3.0 already swept (all ≈ stage 1); 2.0 kept as the reference
GATES = ["Nifty 50", "Nifty Midcap 150"]
SMA = 200
CADENCES = [1, 2, 4]             # rebalance every N months
OUT = HERE / "stage1_cadence_results.json"


def run(cal, ohlc, cols, rebal, picks, pool_of, mhl, mult, br, sr, elig=None, daily_exit=False, gate=None,
        cash_rate: float = 0.0) -> dict:
    """picks(d) -> eligible names to hold from the open of d (signal = previous session).
    daily_exit: a holding that is not eligible at a close is sold at the next open (entries stay monthly).
    gate: bool Series by date, True = risk on at that CLOSE; off -> everything sold at the next open and no buying;
          back on -> a rebalance into picks(d) at the next open."""
    col = {s: j for j, s in enumerate(cols)}
    di = {d: i for i, d in enumerate(ohlc["close"].index)}
    O, H, C = (ohlc[k][cols].to_numpy() for k in ("open", "high", "close"))
    Cf = ohlc["close"][cols].ffill().to_numpy()
    lastv = ohlc["close"][cols].apply(lambda c: c.last_valid_index())
    last = {s: di[lastv[s]] for s in cols if lastv[s] is not None}
    rset = set(rebal)
    cash, pos = 1.0, {}            # sym -> {"q", "entry", "R", "stop"}
    pending, eq, inv, exits, turnover = set(), [], [], Counter(), 0.0

    def sell(s, q, px, why=None):
        nonlocal cash, turnover
        cash += q * px * (1 - SLIP) * (1 - sr)
        turnover += q * px
        pos[s]["q"] -= q
        if pos[s]["q"] <= 1e-12:
            pos.pop(s)
            pending.discard(s)
            if why:
                exits[why] += 1

    def px_or_last(s, i):
        o = O[i, col[s]]
        if not np.isnan(o):
            return o
        return C[last[s], col[s]] if s in last and last[s] < i else None   # stopped trading -> last close

    all_idx = ohlc["close"].index
    was_on = True
    for k, d in enumerate(cal):
        i = di[d]
        prev = all_idx[i - 1]
        if k > 0 and cash_rate:                                     # idle cash accrues per session (assumed rate)
            cash *= (1 + cash_rate) ** (1 / 252)
        on = True if gate is None else bool(gate.get(prev, False))
        for s in sorted(pending):                                   # 1) stop exits at the open
            p = px_or_last(s, i)
            if p is not None:
                sell(s, pos[s]["q"], p, "STOP")
        if not on:                                                  # 1b) gate off: everything out
            for s in sorted(pos):
                p = px_or_last(s, i)
                if p is not None:
                    sell(s, pos[s]["q"], p, "GATE")
        if daily_exit and pos and d not in rset:                    # 1c) lost eligibility at yesterday's close
            for s in [s for s in sorted(pos) if not bool(elig.at[prev, s])]:
                p = px_or_last(s, i)
                if p is not None:
                    sell(s, pos[s]["q"], p, "DAILY_INELIG")
        reenter = on and not was_on
        was_on = on
        if d not in rset and pos:                                   # 2) left the pool mid-month
            pool = pool_of(d.date())
            for s in [s for s in sorted(pos) if s not in pool]:
                p = px_or_last(s, i)
                if p is not None:
                    sell(s, pos[s]["q"], p, "INDEX")
        if on and (d in rset or reenter):                           # 3) monthly (or gate re-entry) rebalance
            tgt = [s for s in picks(d, set(pos)) if not np.isnan(O[i, col[s]])]
            frozen = [s for s in pos if np.isnan(O[i, col[s]])]
            frozen_v = sum(pos[s]["q"] * Cf[i, col[s]] for s in frozen)
            equity = cash + frozen_v + sum(pos[s]["q"] * O[i, col[s]] for s in pos if s not in frozen)
            w = (equity - frozen_v) / len(tgt) if tgt else 0.0
            want = {s: w / O[i, col[s]] for s in tgt}
            for s in sorted(pos):
                if s in frozen:
                    continue
                dq = pos[s]["q"] - want.get(s, 0.0)
                if dq > 1e-12:
                    sell(s, dq, O[i, col[s]], None if s in want else "INELIGIBLE")
            buys = {s: (want[s] - (pos[s]["q"] if s in pos else 0.0)) * O[i, col[s]] for s in tgt}
            buys = {s: v for s, v in buys.items() if v > 1e-12}
            need = sum(buys.values()) * (1 + SLIP) * (1 + br)
            scale = min(1.0, cash / need) if need > 0 else 0.0
            for s, v in buys.items():
                v *= scale
                cash -= v * (1 + SLIP) * (1 + br)
                turnover += v
                o = O[i, col[s]]
                if s in pos:
                    pos[s]["q"] += v / o
                else:
                    R = mult * mhl[d].get(s, np.nan) if mult and d in mhl else np.nan
                    pos[s] = {"q": v / o, "entry": o, "R": R, "stop": o - R if mult else -np.inf}
        if mult:                                                    # 4) trail on the high, stop on the close
            for s, p in pos.items():
                h, c = H[i, col[s]], C[i, col[s]]
                if np.isnan(h) or not p["R"] > 0:
                    continue
                kk = int(np.floor((h - p["entry"]) / p["R"]))
                if kk >= 1:
                    p["stop"] = max(p["stop"], p["entry"] + (kk - 1) * p["R"])
                if c < p["stop"]:
                    pending.add(s)
        mv = sum(p["q"] * Cf[i, col[s]] for s, p in pos.items())
        eq.append(cash + mv)
        inv.append(mv / (cash + mv))
    e = pd.Series(eq, index=cal)
    yrs = (cal[-1] - cal[0]).days / 365.25
    return {"eq": e, "invested": float(np.mean(inv)), "exits": dict(exits), "turnover_yr": turnover / yrs / e.mean()}


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    today = date.today()
    ivs = {u: load_membership(db, u, today) for u in UNIVERSES}
    syms = sorted(union_over(ivs[UNIVERSES[0]], START, today) & union_over(ivs[UNIVERSES[1]], START, today))
    since = load_from(START)
    ohlc = load_ohlc(db, syms, since)
    cols = [c for c in ohlc["close"].columns if c != BENCH]
    elig = alpha_eligible(load_truevx(db, cols, since), ohlc["close"].index, cols)
    cal = trading_days(ohlc["close"][BENCH], START)
    rebal = sorted(period_start_shifted(cal, "monthly", 0))
    idx = ohlc["close"].index
    mk = idx.to_period("M")
    mh, ml = ohlc["high"][cols].groupby(mk).max(), ohlc["low"][cols].groupby(mk).min()
    pool_of = lambda day: members_as_of(ivs[UNIVERSES[0]], day) & members_as_of(ivs[UNIVERSES[1]], day)
    picks, mhl = {}, {}
    for d in rebal:
        prev = idx[idx < d][-1]
        pm = prev.to_period("M")
        assert pm < d.to_period("M")
        picks[d] = sorted(s for s in pool_of(d.date()) if s in elig.columns and bool(elig.at[prev, s]))
        mhl[d] = (mh.loc[pm] - ml.loc[pm]).to_dict()
    br, sr = charge_rates()
    print(f"data {cal[0].date()} -> {cal[-1].date()} · {len(rebal)} rebalances · "
          f"avg eligible {np.mean([len(v) for v in picks.values()]):.1f}")
    def picks_at(d, held=None):
        if d in picks:
            return picks[d]
        prev = idx[idx < d][-1]
        return sorted(s for s in pool_of(d.date()) if s in elig.columns and bool(elig.at[prev, s]))

    gates = {}
    for g in GATES:
        _, gc = load_prices(db, [g], datetime(2019, 1, 1))
        c = gc[g].dropna()
        on = c > c.rolling(SMA).mean()
        gates[g] = on.reindex(idx).ffill().fillna(False).astype(bool)
        print(f"gate {g} > SMA{SMA}: risk-on {gates[g].loc[cal].mean():.0%} of sessions, "
              f"{int((gates[g].loc[cal].astype(int).diff() == -1).sum())} switch-offs")

    # cadence study: rebalance every N months, every phase (start month offset) run separately
    experiments = {}
    for n in CADENCES:
        for ph in range(n):
            sched = [d for j, d in enumerate(rebal) if j % n == ph]
            experiments[f"{n}M ph{ph}"] = {"sched": sched}
            experiments[f"{n}M ph{ph} + gate Mid150"] = {"sched": sched, "gate": gates["Nifty Midcap 150"]}
    out = {}
    for name, kw in experiments.items():
        kw = {**kw}
        m = kw.pop("mult", None)
        sched = kw.pop("sched", rebal)
        r = run(cal, ohlc, cols, sched, picks_at, pool_of, mhl, m, br, sr, elig=elig, **kw)
        s = {**stats(r["eq"]), "h1": cagr_between(r["eq"], b=SPLIT), "h2": cagr_between(r["eq"], a=SPLIT),
             "invested": r["invested"], "exits": r["exits"], "turnover_yr": r["turnover_yr"]}
        out[name] = s
        print(f"\n{name}: CAGR {s['cagr']:6.1%}  Sharpe {s['sharpe']:.2f}  DD {s['maxdd']:6.1%}  H1 {s['h1']:6.1%}  "
              f"H2 {s['h2']:6.1%}  invested {s['invested']:.0%}  turnover {s['turnover_yr']:.1f}x")
        print(f"  exits {s['exits']}")
    print("\nsummary by cadence (median [min..max] over phases):")
    for n in CADENCES:
        for tag in ("", " + gate Mid150"):
            rows = [out[f"{n}M ph{p}{tag}"] for p in range(n)]
            f = lambda k: (np.median([r[k] for r in rows]), min(r[k] for r in rows), max(r[k] for r in rows))
            c, s_, dd, h2 = f("cagr"), f("sharpe"), f("maxdd"), f("h2")
            print(f"  {n}M{tag:<15} CAGR {c[0]:6.1%} [{c[1]:.1%}..{c[2]:.1%}]  Sharpe {s_[0]:.2f} [{s_[1]:.2f}..{s_[2]:.2f}]"
                  f"  DD {dd[0]:6.1%} [{dd[1]:.1%}..{dd[2]:.1%}]  H2 {h2[0]:6.1%} [{h2[1]:.1%}..{h2[2]:.1%}]")
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print(f"\nsaved {OUT.name}")


if __name__ == "__main__":
    main()
