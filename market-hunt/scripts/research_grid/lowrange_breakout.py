"""Stage 2: from all Growth-eligible names in NIFTY200 ∩ NIFTYMIDCAP150, buy the one with the lowest monthly range and
manage it with a range-based stop and two targets.

Entry (monthly; signal = last session of the previous month, trade = first session of the month at the open):
  pool      point-in-time members of NIFTY200 AND NIFTYMIDCAP150 on the trade day
  eligible  Growth/Alpha filter on the signal close (truevx_score>90, mean_short>80, mean_mid>60, mean_long>50),
            not already held
  pick      lowest (high - low) / close of the last COMPLETED calendar month (the month ending at the signal day)
  size      equity / SLOTS at the open; no trade that month if cash is short of a full slot
Trade management (R = 2 * (monthly high - monthly low) of that candle, in rupees):
  stop      entry - R, triggered by a DAILY CLOSE below it, filled at the next open
  +1R       intraday high >= entry + R  -> stop moves to entry (break-even)
  T1 = +2R  intraday high >= T1         -> sell 1/2 at T1 (at the open if it gaps above), stop moves to entry + R
  T2 = +3R  intraday high >= T2         -> sell the rest at T2 (at the open if it gaps above)
  a stock that stops trading is sold at its last close; leaving the index is NOT an exit (not in the rules)
Controls with identical management: RANDOM eligible pick (SEEDS draws) and HIGHEST-range pick.
Costs: statutory charges (BrokerageCalculator) + 5 bp slippage on every fill. Rf = 0.

  .venv/Scripts/python.exe scripts/research_grid/lowrange_breakout.py
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
sys.path.insert(0, str(HERE.parent.parent))
from fund.data import BENCH, charge_rates, load_from, load_truevx  # noqa: E402
from fund.engine.calendar import period_start_shifted, trading_days  # noqa: E402
from fund.engine.signals import alpha_eligible  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402

START = date(2020, 10, 1)
UNIVERSES = ("NIFTY200", "NIFTYMIDCAP150")
SLOTS = 10
R_MULT = 2.0
SLIP = 0.0005
SEEDS = 100
SPLIT = pd.Timestamp("2023-10-01")
OUT = HERE / "lowrange_breakout_results.json"


def load_ohlc(db, symbols, since):
    rows = []
    for c in (c for c in db.list_collection_names() if c.startswith("prices_")):
        rows += list(db[c].find({"symbol": {"$in": symbols + [BENCH]}, "date": {"$gte": since}},
                                {"_id": 0, "symbol": 1, "date": 1, "open_price": 1, "high_price": 1,
                                 "low_price": 1, "close_price": 1}))
    px = pd.DataFrame(rows)
    px["date"] = pd.to_datetime(px["date"]).dt.normalize()
    px = px.drop_duplicates(["symbol", "date"])
    return {k: px.pivot(index="date", columns="symbol", values=f"{k}_price").sort_index()
            for k in ("open", "high", "low", "close")}


def stats(eq: pd.Series) -> dict:
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    vol = r.std() * np.sqrt(252)
    return {"cagr": (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1, "vol": vol,
            "sharpe": r.mean() * 252 / vol if vol > 0 else float("nan"), "maxdd": float((eq / eq.cummax() - 1).min())}


def cagr_between(eq, a=None, b=None):
    s = eq[(eq.index >= a) if a is not None else slice(None)]
    s = s[s.index < b] if b is not None else s
    return stats(s)["cagr"]


class Sim:
    """Event simulation on numpy arrays (day x symbol)."""

    def __init__(self, cal, ohlc, cols, rebal, cands, mrange, mhl, buy_rate, sell_rate):
        idx = {d: i for i, d in enumerate(ohlc["close"].index)}
        self.ci = np.array([idx[d] for d in cal])
        self.cal = cal
        self.col = {s: j for j, s in enumerate(cols)}
        self.O, self.H, self.L, self.C = (ohlc[k][cols].to_numpy() for k in ("open", "high", "low", "close"))
        self.Cf = ohlc["close"][cols].ffill().to_numpy()
        lastv = ohlc["close"][cols].apply(lambda c: c.last_valid_index())
        self.last = {s: idx[lastv[s]] for s in cols if lastv[s] is not None}
        self.rebal, self.cands, self.mrange, self.mhl = set(rebal), cands, mrange, mhl
        self.br, self.sr = buy_rate, sell_rate

    def run(self, chooser, slots: int = SLOTS, trail: bool = False) -> dict:
        """chooser(d, candidates) -> symbols in priority order; each is bought while cash covers a full slot.
        trail=True: no T2 — after T1, every new +kR high (k>=3) moves the stop to +(k-1)R."""
        cash, pos, trades = 1.0, {}, []
        eq, invested = [], []
        pending = set()                                     # stop hit on yesterday's close -> sell at today's open

        def close_out(s, q, px, why, k):
            nonlocal cash
            p = pos[s]
            cash += q * px * (1 - SLIP) * (1 - self.sr)
            p["q"] -= q
            p["pnl"] += q * (px * (1 - SLIP) * (1 - self.sr) - p["cost"])
            p["exits"].append(why)
            if p["q"] <= 1e-12:
                trades.append({"sym": s, "entry_day": str(self.cal[p["k0"]].date()), "exit_day": str(self.cal[k].date()),
                               "days": int(k - p["k0"]), "R_pct": p["R"] / p["entry"], "exits": p["exits"],
                               "ret": p["pnl"] / (p["q0"] * p["cost"])})
                pos.pop(s)

        for k, d in enumerate(self.cal):
            i = self.ci[k]
            # 1) stop exits signalled on yesterday's close
            for s in sorted(pending):
                j = self.col[s]
                o = self.O[i, j]
                if np.isnan(o):
                    if self.last[s] < i:
                        close_out(s, pos[s]["q"], self.C[self.last[s], j], pos[s]["stop_why"], k)
                        pending.discard(s)
                    continue
                close_out(s, pos[s]["q"], o, pos[s]["stop_why"], k)
                pending.discard(s)
            # 2) monthly entry
            if d in self.rebal:
                equity = cash + sum(p["q"] * self.Cf[i - 1, self.col[s]] for s, p in pos.items())
                slot = equity / slots
                for pick in chooser(d, [s for s in self.cands[d] if s not in pos and not np.isnan(self.O[i, self.col[s]])]):
                    if cash < slot:
                        break
                    o = self.O[i, self.col[pick]]
                    unit = o * (1 + SLIP) * (1 + self.br)
                    q = slot / unit
                    cash -= slot
                    R = R_MULT * self.mhl[d][pick]
                    pos[pick] = {"q": q, "q0": q, "entry": o, "cost": unit, "R": R, "stop": o - R, "stage": 0,
                                 "k0": k, "pnl": 0.0, "exits": [], "stop_why": "SL"}
            # 3) intraday targets, then the close-based stop
            for s in sorted(pos):
                if s in pending:
                    continue
                p, j = pos[s], self.col[s]
                o, h, c = self.O[i, j], self.H[i, j], self.C[i, j]
                if np.isnan(h):
                    if s in self.last and self.last[s] < i:  # stopped trading: out at the last close
                        close_out(s, p["q"], self.C[self.last[s], j], "DELIST", k)
                    continue
                e, R = p["entry"], p["R"]
                if p["stage"] == 0 and h >= e + R:
                    p["stage"], p["stop"], p["stop_why"] = 1, e, "BE"
                if p["stage"] == 1 and h >= e + 2 * R:
                    close_out(s, p["q"] / 2, max(o, e + 2 * R), "T1", k)
                    p["stage"], p["stop"], p["stop_why"] = 2, e + R, "SL+1R"
                if p["stage"] >= 2 and trail:
                    top = int(np.floor((h - e) / R)) if R > 0 else 0
                    if top >= 3 and top > p["stage"]:
                        p["stage"], p["stop"], p["stop_why"] = top, e + (top - 1) * R, f"TR+{top - 1}R"
                elif p["stage"] == 2 and h >= e + 3 * R:
                    close_out(s, p["q"], max(o, e + 3 * R), "T2", k)
                    continue
                if c < p["stop"]:
                    pending.add(s)
            mv = sum(p["q"] * self.Cf[i, self.col[s]] for s, p in pos.items())
            eq.append(cash + mv)
            invested.append(mv / (cash + mv))
        e = pd.Series(eq, index=self.cal)
        return {"eq": e, "trades": trades, "open": sorted(pos), "invested": float(np.mean(invested))}


def summarize(res) -> dict:
    t = res["trades"]
    rets = np.array([x["ret"] for x in t]) if t else np.array([0.0])
    path = Counter("/".join(x["exits"]) for x in t)
    return {**stats(res["eq"]), "h1": cagr_between(res["eq"], b=SPLIT), "h2": cagr_between(res["eq"], a=SPLIT),
            "closed": len(t), "open": len(res["open"]), "win_rate": float((rets > 0).mean()),
            "avg_ret": float(rets.mean()), "median_days": float(np.median([x["days"] for x in t])) if t else 0,
            "avg_R_pct": float(np.mean([x["R_pct"] for x in t])) if t else 0, "invested": res["invested"],
            "exit_paths": dict(path.most_common())}


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
    # last completed calendar month's candle per symbol: (high - low) and close
    mk = idx.to_period("M")
    mh = ohlc["high"][cols].groupby(mk).max()
    ml = ohlc["low"][cols].groupby(mk).min()
    mc = ohlc["close"][cols].groupby(mk).last()
    cands, mrange, mhl = {}, {}, {}
    for d in rebal:
        prev = idx[idx < d][-1]
        pm = prev.to_period("M")
        assert pm < d.to_period("M"), "signal month must be complete"
        pool = members_as_of(ivs[UNIVERSES[0]], d.date()) & members_as_of(ivs[UNIVERSES[1]], d.date())
        hl = (mh.loc[pm] - ml.loc[pm])
        rg = hl / mc.loc[pm]
        cands[d] = sorted(s for s in pool if s in elig.columns and bool(elig.at[prev, s]) and not pd.isna(rg.get(s)))
        mrange[d], mhl[d] = rg.to_dict(), hl.to_dict()
    br, sr = charge_rates()
    sim = Sim(cal, ohlc, cols, rebal, cands, mrange, mhl, br, sr)
    print(f"data {cal[0].date()} -> {cal[-1].date()} · {len(rebal)} entries possible · "
          f"avg candidates {np.mean([len(cands[d]) for d in rebal]):.1f}")

    lo = lambda n: (lambda d, c: sorted(c, key=lambda s: (mrange[d][s], s))[:n])
    hi = lambda n: (lambda d, c: sorted(c, key=lambda s: (-mrange[d][s], s))[:n])

    def rnd(n, seed, **kw):
        rng = np.random.default_rng(seed)
        res = [summarize(sim.run(lambda d, c: list(rng.permutation(c))[:n] if c else [], **kw)) for _ in range(SEEDS)]
        rc = np.array([r["cagr"] for r in res])
        return {"cagr_p5": float(np.percentile(rc, 5)), "cagr_p50": float(np.median(rc)),
                "cagr_p95": float(np.percentile(rc, 95)), "rc": rc,
                "sharpe_p50": float(np.median([r["sharpe"] for r in res])),
                "dd_p50": float(np.median([r["maxdd"] for r in res]))}

    variants = {  # name: (chooser, kwargs, random-control n or None)
        "base: lowest-1, /10, T2": (lo(1), {}, 1),
        "A: all eligible, /20, T2": (lo(10**6), {"slots": 20}, None),
        "B: lowest-3, /10, T2": (lo(3), {}, 3),
        "C: lowest-1, /10, trail": (lo(1), {"trail": True}, 1),
        "B+C: lowest-3, /10, trail": (lo(3), {"trail": True}, 3),
        "A+C: all eligible, /20, trail": (lo(10**6), {"slots": 20, "trail": True}, None),
    }
    out, runs = {}, {}
    for name, (ch, kw, rn) in variants.items():
        res = sim.run(ch, **kw)
        runs[name] = res
        r = summarize(res)
        if rn:
            x = rnd(rn, 11, **kw)
            r["random"] = {k: v for k, v in x.items() if k != "rc"}
            r["random"]["beats"] = float((x["rc"] < r["cagr"]).mean())
        out[name] = r
        print()
        print(f"{name}: CAGR {r['cagr']:6.1%}  Sharpe {r['sharpe']:.2f}  DD {r['maxdd']:6.1%}  H1 {r['h1']:6.1%}  "
              f"H2 {r['h2']:6.1%}  invested {r['invested']:.0%}")
        print(f"  trades {r['closed']} closed + {r['open']} open · win {r['win_rate']:.0%} · avg {r['avg_ret']:+.1%}/trade · "
              f"median hold {r['median_days']:.0f} · avg R {r['avg_R_pct']:.1%}")
        print(f"  exit paths {dict(list(r['exit_paths'].items())[:6])}")
        if rn:
            x = r["random"]
            print(f"  random pick: CAGR p5 {x['cagr_p5']:.1%} p50 {x['cagr_p50']:.1%} p95 {x['cagr_p95']:.1%} -> beats "
                  f"{x['beats']:.0%} · Sharpe p50 {x['sharpe_p50']:.2f} · DD p50 {x['dd_p50']:.1%}")
    hres = summarize(sim.run(hi(3)))
    print()
    print(f"highest-3 /10 T2: CAGR {hres['cagr']:.1%} Sharpe {hres['sharpe']:.2f} DD {hres['maxdd']:.1%}")
    out["highest-3"] = hres
    out["base_trades"] = runs["base: lowest-1, /10, T2"]["trades"]
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print(f"\nsaved {OUT.name}")


if __name__ == "__main__":
    main()
