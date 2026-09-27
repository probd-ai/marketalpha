"""Which hedge works for a NIFTY 50 book? Commodities (and USD) in rupees, Jan 2008 -> Sep 2026.

Each asset = TradingView series x FX_IDC:USDINR, forward-filled onto NSE sessions, minus 0.5%/yr holding
cost. Assets: gold, silver, platinum, Brent (TVC:UKOIL — TVC:USOIL went negative in Apr-2020), copper
(COMEX:HG1! continuous), CRB broad index (TVC:TRJEFFCRB), two baskets, and plain USD (USDINR) for
reference. Reports each asset's return over every Nifty 50 fall >= 10% (peak -> bottom), its correlation
with Nifty, standalone CAGR/drawdown, and a 70% loser-book / 30% hedge blend (monthly rebalance).
⚠ Continuous futures/spot prices ignore roll yield, so crude/copper/CRB returns are approximate.

  .venv/Scripts/python.exe scripts/research_grid/hedge_compare.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gold_blend import GOLD_FEE_PA, blend, metrics  # noqa: E402

FALLS = [("2008-01-08", "2008-10-27"), ("2010-11-05", "2011-12-20"), ("2015-03-03", "2016-02-25"),
         ("2018-01-29", "2018-03-23"), ("2018-08-28", "2018-10-26"), ("2019-06-03", "2019-09-19"),
         ("2020-01-14", "2020-03-23"), ("2021-10-18", "2022-06-17"), ("2024-09-26", "2025-03-04"),
         ("2026-01-02", "2026-03-30")]
BIG = {"2008-01-08", "2010-11-05", "2015-03-03", "2020-01-14"}
FILES = {"Gold": "TVC_GOLD.csv", "Silver": "TVC_SILVER.csv", "Platinum": "TVC_PLATINUM.csv",
         "Brent crude": "TVC_UKOIL.csv", "Copper": "COMEX_HG1_.csv", "CRB broad index": "TVC_TRJEFFCRB.csv"}


def rd(f: str) -> pd.Series:
    s = pd.read_csv(HERE / "gold" / f, index_col=0, parse_dates=True)["close"]
    s = s.set_axis(s.index.normalize()).sort_index()
    return s[~s.index.duplicated()]


def to_inr(usd: pd.Series, fx: pd.Series, idx: pd.DatetimeIndex, fee=GOLD_FEE_PA) -> pd.Series:
    s = (usd * fx.reindex(usd.index, method="ffill")).dropna()
    s = s.reindex(idx.union(s.index)).ffill().reindex(idx)
    return s * (1 - fee) ** ((idx - idx[0]).days / 365.25)


def main():
    eqs = pd.read_csv(HERE / "rsma_rising_nifty50" / "equity.csv", index_col=0, parse_dates=True)
    idx, loser, nifty = eqs.index, eqs["loser_plain"], eqs["Nifty50"]
    fx = rd("FX_IDC_USDINR.csv")
    assets = {k: to_inr(rd(f), fx, idx) for k, f in FILES.items()}
    norm = {k: v / v.iloc[0] for k, v in assets.items()}
    assets["Gold+Silver 50/50"] = (norm["Gold"] + norm["Silver"]) / 2            # buy & hold basket
    assets["All 6 commodities EW"] = sum(norm[k] for k in FILES) / len(FILES)
    assets["US dollar (USDINR)"] = to_inr(pd.Series(1.0, index=fx.index), fx, idx, fee=0.0)

    rep = {}
    for k, s in assets.items():
        falls = {a: float(s.asof(pd.Timestamp(b)) / s.asof(pd.Timestamp(a)) - 1) for a, b in FALLS}
        m = metrics(s)
        bl = blend(loser, s, 0.30)
        bm = metrics(bl)
        rep[k] = {"cagr": m["cagr"], "max_dd": m["max_dd"],
                  "corr_nifty": float(pd.concat([nifty.pct_change(), s.pct_change()], axis=1).dropna().corr().iloc[0, 1]),
                  "avg_all_falls": sum(falls.values()) / len(falls),
                  "avg_big_falls": sum(v for a, v in falls.items() if a in BIG) / len(BIG),
                  "up_in_falls": sum(v > 0 for v in falls.values()), "falls": falls,
                  "blend30": {**bm, "gfc": float((lambda x: (x / x.cummax() - 1).min())(bl.loc["2008-01-01":"2009-03-31"])),
                              "covid": float((lambda x: (x / x.cummax() - 1).min())(bl.loc["2020-01-01":"2020-03-31"]))}}
    base = metrics(loser)
    rep["(no hedge) loser book"] = {"blend30": {**base,
                                                "gfc": float((lambda x: (x / x.cummax() - 1).min())(loser.loc["2008-01-01":"2009-03-31"])),
                                                "covid": float((lambda x: (x / x.cummax() - 1).min())(loser.loc["2020-01-01":"2020-03-31"]))}}
    out = HERE / "hedge_compare"
    out.mkdir(exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
