"""Concentration risk of the no-trim Grid-10 book (weekly+0 and monthly+0), point-in-time NIFTY50, Oct 2020 -> Sep 2026.

  1) the day the largest holding peaked: every holding's weight that day
  2) how often the book is concentrated (largest weight / top-3 weight distribution)
  3) every stock that ever grew past 15%: its peak weight, then how far it fell while still held and what that fall
     cost the whole portfolio (% of equity at the stock's peak)
  4) worst single-stock hits to the portfolio: 1-day and 22-day contribution (% of portfolio), with the weight
  5) the book's 3 deepest drawdowns: peak -> trough, loss attributed per stock (weight at the peak, stock move, cost)

Per-stock daily contribution = value at the previous close x the stock's move to today's close (to today's open if it
was sold at the open), divided by portfolio equity at the previous close — close-to-close attribution, approximate.

  .venv/Scripts/python.exe scripts/research_grid/grid10_concentration.py
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
from grid_meanrev import BENCH, charge_rates, load_data  # noqa: E402
from membership import load_membership, union_over  # noqa: E402
from rotation_roc import run  # noqa: E402
from value_grid_filter import grid_state  # noqa: E402

CAP, SLIP, N = 1_260_000, 0.0005, 10
START, END = date(2020, 10, 1), date(2026, 9, 25)


def drawdowns(eq: pd.Series, k: int = 3):
    """k deepest non-overlapping peak->trough episodes."""
    out, s = [], eq.copy()
    dd = s / s.cummax() - 1
    used = pd.Series(False, index=s.index)
    for _ in range(k):
        cand = dd[~used]
        if cand.empty or cand.min() >= 0:
            break
        tr = cand.idxmin()
        pk = s.loc[:tr].idxmax()
        rec = s.loc[tr:][s.loc[tr:] >= s.loc[pk]]
        end = rec.index[0] if len(rec) else s.index[-1]
        out.append((pk, tr, end, float(dd.loc[tr])))
        used.loc[pk:end] = True
    return out


def analyse(eq, book, opens, closes):
    idx = eq.index
    V = pd.DataFrame({d: b for d, _, b in book}).T.reindex(idx).fillna(0.0)
    equity = eq["equity"]
    W = V.div(equity, axis=0)
    rc = closes[V.columns].ffill().reindex(idx)
    ro = opens[V.columns].reindex(idx)
    prevV, prevC = V.shift(1), rc.shift(1)
    move = rc / prevC - 1
    sold_move = (ro / prevC - 1).fillna(move)
    move = move.where(V > 0, sold_move)
    contrib = (prevV * move).fillna(0.0)                      # rupees
    contrib_pct = contrib.div(equity.shift(1), axis=0) * 100

    rep = {}
    # 1) peak concentration day
    lw = W.max(axis=1)
    d = lw.idxmax()
    row = W.loc[d][W.loc[d] > 0].sort_values(ascending=False)
    rep["peak_day"] = {"date": str(d.date()), "equity_L": round(equity.loc[d] / 1e5, 2),
                       "cash_pct": round(float(1 - row.sum()) * 100, 1),
                       "weights_pct": {s: round(v * 100, 1) for s, v in row.items()}}
    # 2) distribution
    top3 = W.apply(lambda r: r.nlargest(3).sum(), axis=1)
    live = lw.index >= idx[60]
    rep["distribution"] = {"largest_median": float(lw[live].median() * 100), "largest_p90": float(lw[live].quantile(.9) * 100),
                           "days_largest_gt15_pct": float((lw[live] > .15).mean() * 100),
                           "days_largest_gt20_pct": float((lw[live] > .20).mean() * 100),
                           "top3_median": float(top3[live].median() * 100), "top3_max": float(top3.max() * 100)}
    # 3) stocks that grew past 15%
    big = []
    for s in W.columns[(W > .15).any()]:
        w = W[s]
        held = V[s] > 0
        # split into holding spells, keep spells that crossed 15%
        spell = (held != held.shift()).cumsum()[held]
        for _, days in spell.groupby(spell):
            ix = days.index
            if w.loc[ix].max() <= .15:
                continue
            pk = w.loc[ix].idxmax()
            after = ix[ix >= pk]
            px = rc.loc[after, s]
            tr = px.idxmin()
            fall = float(px.loc[tr] / px.iloc[0] - 1)
            cost = float(contrib.loc[after[1:] if len(after) > 1 else after, s].loc[:tr].sum())
            big.append({"symbol": s, "peak_date": str(pk.date()), "peak_weight_pct": round(w.loc[pk] * 100, 1),
                        "fall_while_held_pct": round(fall * 100, 1), "trough": str(tr.date()),
                        "portfolio_cost_pct": round(cost / equity.loc[pk] * 100, 2),
                        "portfolio_cost_L": round(cost / 1e5, 2), "exit": str(ix[-1].date())})
    rep["grew_past_15"] = sorted(big, key=lambda x: x["portfolio_cost_pct"])
    # 4) worst single-stock hits
    one = contrib_pct.stack()
    worst1 = one.nsmallest(5)
    rep["worst_1d"] = [{"date": str(d.date()), "symbol": s, "weight_pct": round(W.shift(1).at[d, s] * 100, 1),
                        "stock_move_pct": round(move.at[d, s] * 100, 1), "portfolio_hit_pct": round(v, 2)}
                       for (d, s), v in worst1.items()]
    c22 = contrib.rolling(22).sum().div(equity.shift(22), axis=0) * 100
    hits, seen = [], set()
    for (d, s), v in c22.stack().sort_values().items():
        key = (s, d.year, d.month)
        if key in seen or any(h["symbol"] == s and abs((pd.Timestamp(h["end"]) - d).days) < 60 for h in hits):
            continue
        seen.add(key)
        st = idx[max(idx.get_loc(d) - 22, 0)]
        hits.append({"end": str(d.date()), "symbol": s, "weight_at_start_pct": round(W.at[st, s] * 100, 1),
                     "stock_move_pct": round((rc.at[d, s] / rc.at[st, s] - 1) * 100, 1), "portfolio_hit_pct": round(v, 2)})
        if len(hits) == 5:
            break
    rep["worst_22d"] = hits
    # 5) deepest drawdowns, attributed
    dds = []
    for pk, tr, end, depth in drawdowns(equity):
        c = contrib.loc[pk:tr].iloc[1:].sum() / equity.loc[pk] * 100
        c = c[c.abs() > 0.05].sort_values()
        dds.append({"peak": str(pk.date()), "trough": str(tr.date()), "recovered": str(end.date()),
                    "depth_pct": round(depth * 100, 1),
                    "largest_weight_at_peak": f"{W.loc[pk].idxmax()} {W.loc[pk].max() * 100:.1f}%",
                    "by_stock_pct": [{"symbol": s, "weight_at_peak_pct": round(W.at[pk, s] * 100, 1),
                                      "cost_pct": round(v, 2)} for s, v in c.head(6).items()],
                    "biggest_gainer": {"symbol": c.index[-1], "pct": round(float(c.iloc[-1]), 2)} if c.iloc[-1] > 0 else None})
    rep["drawdowns"] = dds
    return rep


def main():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(iv, START, date.today()))
    opens, closes, ml = load_data(db, ever, field="mean_long")
    cols = [c for c in closes.columns if c != BENCH]
    state = grid_state(ml.reindex(index=closes.index, columns=cols))
    roc22 = closes[cols].apply(lambda c: c / c.dropna().shift(22).reindex(c.index) - 1)
    br, sr = charge_rates()

    def pick(prev, pool, held):
        sells = {s for s in held if s in pool and state.at[prev, s] == -1
                 and not pd.isna(roc22.at[prev, s]) and roc22.at[prev, s] > 0}
        keep = [s for s in held if s not in sells]
        filtered = [s for s in pool if state.at[prev, s] == 1 and not pd.isna(roc22.at[prev, s]) and s not in keep]
        cands = list(roc22.loc[prev, filtered].sort_values().index)
        return sorted(keep + cands[: max(0, N - len(keep))])

    out = {}
    for name, fq in {"Grid-10 weekly": "weekly+0", "Grid-10 monthly": "monthly+0"}.items():
        book = []
        eq, _ = run(opens, closes, roc22, iv, START, END, N, "low", fq, CAP, SLIP, br, sr, pick_fn=pick, book_log=book)
        out[name] = analyse(eq, book, opens, closes)
        print(f"=== {name} ({fq})")
        for k, v in out[name].items():
            if isinstance(v, list):
                print(f" {k}:")
                for x in v:
                    print("   ", x)
            else:
                print(f" {k}: {v}")
    (HERE / "grid10_concentration.json").write_text(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
