"""Multi-asset book: Indian equity sleeve + gold + US dollar + Nasdaq-100, monthly rebalance, 2008-01 -> 2026-09.

Sleeves (all in rupees):
  IN equity : NIFTY50 22d-loser book, N=20, buffer 30 (rsma_rising_nifty50/equity.csv: loser_plain; costs inside)
  Gold      : TVC:GOLD x FX_IDC:USDINR, minus 0.5%/yr
  USD       : FX_IDC:USDINR (plain dollars, no interest)
  NDX       : NASDAQ:NDX price index x USDINR, minus 0.5%/yr (no dividends)
Target weights restored on the first session of each month; rupees moved between sleeves pay 0.15%.
Runs a named set of mixes plus a grid, and reports CAGR, max drawdown, Sharpe, era CAGRs and crash
drawdowns (incl. 2022, when Nasdaq fell with the rupee strong-ish).

  .venv/Scripts/python.exe scripts/research_grid/multi_blend.py
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from hedge_compare import rd, to_inr  # noqa: E402

SWITCH_COST = 0.0015
WINDOWS = {"2008-13": ("2008-01-01", "2013-12-31"), "2014-20": ("2014-01-01", "2020-09-30"),
           "2020-26": ("2020-10-01", "2026-09-25")}
CRASHES = {"GFC": ("2008-01-01", "2009-03-31"), "2010-11": ("2010-11-01", "2011-12-31"),
           "2015-16": ("2015-03-01", "2016-02-29"), "COVID": ("2020-01-01", "2020-03-31"),
           "2022": ("2021-10-01", "2022-12-31"), "2024-25": ("2024-09-01", "2025-03-31")}
NAMED = {  # IN equity, gold, USD, NDX
    "100% Indian equity":            (1.00, 0.00, 0.00, 0.00),
    "70 / 30 gold":                  (0.70, 0.30, 0.00, 0.00),
    "70 / 20 gold / 5 USD / 5 NDX":  (0.70, 0.20, 0.05, 0.05),
    "70 / 20 gold / 10 NDX":         (0.70, 0.20, 0.00, 0.10),
    "70 / 15 gold / 15 NDX":         (0.70, 0.15, 0.00, 0.15),
    "60 / 20 gold / 20 NDX":         (0.60, 0.20, 0.00, 0.20),
    "60 / 20 gold / 10 USD / 10 NDX": (0.60, 0.20, 0.10, 0.10),
    "50 / 25 gold / 25 NDX":         (0.50, 0.25, 0.00, 0.25),
    "70 / 0 / 0 / 30 NDX":           (0.70, 0.00, 0.00, 0.30),
}


def blend_multi(prices: pd.DataFrame, w: tuple) -> pd.Series:
    r = prices.pct_change().fillna(0).to_numpy()
    month = prices.index.to_period("M")
    v = list(w)
    out = [1.0]
    for i in range(1, len(prices)):
        v = [v[j] * (1 + r[i, j]) for j in range(len(v))]
        tot = sum(v)
        if month[i] != month[i - 1]:
            moved = sum(abs(v[j] - w[j] * tot) for j in range(len(v))) / 2
            tot -= moved * SWITCH_COST
            v = [w[j] * tot for j in range(len(v))]
        out.append(tot)
    return pd.Series(out, index=prices.index)


def stats(s: pd.Series) -> dict:
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    dd = lambda x: float((x / x.cummax() - 1).min())
    sub = lambda x: (x.iloc[-1] / x.iloc[0]) ** (365.25 / (x.index[-1] - x.index[0]).days) - 1
    return {"cagr": (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1, "max_dd": dd(s),
            "sharpe": float(r.mean() / r.std() * math.sqrt(252)),
            "windows": {k: sub(s.loc[a:b]) for k, (a, b) in WINDOWS.items()},
            "crashes": {k: dd(s.loc[a:b]) for k, (a, b) in CRASHES.items()}}


def main():
    eq = pd.read_csv(HERE / "rsma_rising_nifty50" / "equity.csv", index_col=0, parse_dates=True)
    idx = eq.index
    fx = rd("FX_IDC_USDINR.csv")
    prices = pd.DataFrame({"IN equity": eq["loser_plain"],
                           "Gold": to_inr(rd("TVC_GOLD.csv"), fx, idx),
                           "USD": to_inr(pd.Series(1.0, index=fx.index), fx, idx, fee=0.0),
                           "NDX": to_inr(rd("NASDAQ_NDX.csv"), fx, idx)})
    rep = {"sleeves_alone": {c: stats(prices[c]) for c in prices.columns},
           "corr_daily": prices.pct_change().corr().round(2).to_dict(), "named": {}, "grid": []}
    curves = {}
    for name, w in NAMED.items():
        s = blend_multi(prices, w)
        curves[name] = s
        rep["named"][name] = {"weights": w, **stats(s)}
    for e, g, u, n in itertools.product([0.5, 0.6, 0.7, 0.8], [0, .1, .2, .3], [0, .05, .1], [0, .05, .1, .15, .2, .25]):
        if abs(e + g + u + n - 1) < 1e-9:
            m = stats(blend_multi(prices, (e, g, u, n)))
            rep["grid"].append({"w": (e, g, u, n), "cagr": m["cagr"], "max_dd": m["max_dd"], "sharpe": m["sharpe"],
                                "worst_era": min(m["windows"].values())})
    out = HERE / "multi_blend"
    out.mkdir(exist_ok=True)
    pd.DataFrame(curves).to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print("done", len(rep["grid"]), "grid mixes")


if __name__ == "__main__":
    main()
