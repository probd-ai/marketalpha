"""Equity strategy + fixed gold sleeve, rebalanced monthly, Jan 2008 -> Sep 2026.

Equity sleeves (daily equity curves from earlier runs, all costs already inside):
  loser20  : 22d-loser book, N=20, buffer 30      (rsma_rising_nifty50/equity.csv: loser_plain)
  tilt k=1 : reversal tilt on all 50              (tilt/equity_band0.005.csv: k=1)
  EW50     : equal-weight NIFTY 50 paying costs   (tilt/equity_band0.005.csv: k=0)
Gold sleeve: TradingView TVC:GOLD (USD/oz) x FX_IDC:USDINR, forward-filled onto NSE sessions, minus a
0.5%/yr holding cost (ETF expense). Blend: target weights restored on the first session of each month;
the rupees moved between sleeves pay 0.15%. Drift is allowed within the month.

  .venv/Scripts/python.exe scripts/research_grid/gold_blend.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rs_trend import CRASHES, WINDOWS  # noqa: E402

GOLD_WEIGHTS = [0.0, 0.1, 0.2, 0.3, 0.5]
GOLD_FEE_PA, SWITCH_COST = 0.005, 0.0015


def gold_inr(index: pd.DatetimeIndex) -> pd.Series:
    rd = lambda f: (lambda s: s.set_axis(s.index.normalize()).sort_index())(
        pd.read_csv(HERE / "gold" / f, index_col=0, parse_dates=True)["close"])
    g = rd("TVC_GOLD.csv") * rd("FX_IDC_USDINR.csv").reindex(rd("TVC_GOLD.csv").index, method="ffill")
    g = g.dropna()
    g = g[~g.index.duplicated()].reindex(index.union(g.index)).ffill().reindex(index)
    # ETF expense: the holding shrinks by GOLD_FEE_PA per calendar year
    years = (index - index[0]).days / 365.25
    return g * (1 - GOLD_FEE_PA) ** years


def blend(eq: pd.Series, gold: pd.Series, wg: float) -> pd.Series:
    re, rg = eq.pct_change().fillna(0), gold.pct_change().fillna(0)
    month = eq.index.to_period("M")
    ve, vg = 1 - wg, wg
    out = []
    for i, d in enumerate(eq.index):
        if i > 0:
            ve *= 1 + re.iloc[i]
            vg *= 1 + rg.iloc[i]
            if month[i] != month[i - 1]:                      # first session of a new month
                tot = ve + vg
                moved = abs(vg - wg * tot)
                tot -= moved * SWITCH_COST
                ve, vg = (1 - wg) * tot, wg * tot
        out.append(ve + vg)
    return pd.Series(out, index=eq.index) * eq.iloc[0]


def metrics(s: pd.Series) -> dict:
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return {"cagr": (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1, "max_dd": float((s / s.cummax() - 1).min()),
            "sharpe": float(r.mean() / r.std() * math.sqrt(252))}


def main():
    a = pd.read_csv(HERE / "rsma_rising_nifty50" / "equity.csv", index_col=0, parse_dates=True)
    t = pd.read_csv(HERE / "tilt" / "equity_band0.005.csv", index_col=0, parse_dates=True)
    sleeves = {"loser20": a["loser_plain"], "tilt k=1": t["k=1"].reindex(a.index).ffill(),
               "EW50": t["k=0"].reindex(a.index).ffill()}
    gold = gold_inr(a.index)
    rep, curves = {"gold_fee_pa": GOLD_FEE_PA, "switch_cost": SWITCH_COST, "gold_alone": metrics(gold)}, {}
    for name, eq in sleeves.items():
        for wg in GOLD_WEIGHTS:
            k = f"{name} + {int(wg * 100)}% gold"
            s = blend(eq, gold, wg)
            curves[k] = s
            m = metrics(s)
            m["windows"] = {w: (lambda x: (x.iloc[-1] / x.iloc[0]) ** (365.25 / (x.index[-1] - x.index[0]).days) - 1)
                            (s.loc[p:q]) for w, (p, q) in WINDOWS.items()}
            m["crashes"] = {c: float((lambda x: (x / x.cummax() - 1).min())(s.loc[p:q])) for c, (p, q) in CRASHES.items()}
            rep[k] = m
    out = HERE / "gold_blend"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
