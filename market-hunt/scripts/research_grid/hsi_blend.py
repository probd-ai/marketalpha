"""Add the Hang Seng to the 70/20/10 (Indian equity / gold / Nasdaq-100) book, 2008-01 -> 2026-09.

HSI = HSI:HSI price index x FX_IDC:HKDINR, minus 0.5%/yr fund cost. TradingView has no Hang Seng
total-return series, so every mix is run twice: price-only (dividends lost — a floor) and with an
assumed 3.5%/yr dividend yield added back (approximation, not measured). Other sleeves as in
multi_blend.py; monthly rebalance, 0.15% on rupees moved.

  .venv/Scripts/python.exe scripts/research_grid/hsi_blend.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from hedge_compare import rd, to_inr  # noqa: E402
from multi_blend import blend_multi, stats  # noqa: E402

HSI_DIV_PA = 0.035
MIXES = {  # IN equity, gold, NDX, HSI
    "70 / 20 / 10 NDX / 0 HSI (current pick)": (0.70, 0.20, 0.10, 0.00),
    "70 / 20 / 5 NDX / 5 HSI": (0.70, 0.20, 0.05, 0.05),
    "70 / 20 / 0 NDX / 10 HSI": (0.70, 0.20, 0.00, 0.10),
    "65 / 20 / 10 NDX / 5 HSI": (0.65, 0.20, 0.10, 0.05),
    "60 / 20 / 10 NDX / 10 HSI": (0.60, 0.20, 0.10, 0.10),
    "70 / 15 / 10 NDX / 5 HSI": (0.70, 0.15, 0.10, 0.05),
}


def main():
    eq = pd.read_csv(HERE / "rsma_rising_nifty50" / "equity.csv", index_col=0, parse_dates=True)
    idx = eq.index
    usd = rd("FX_IDC_USDINR.csv")
    hsi_px = to_inr(rd("HSI_HSI.csv"), rd("FX_IDC_HKDINR.csv"), idx)
    years = (idx - idx[0]).days / 365.25
    variants = {"price-only": hsi_px, f"+{HSI_DIV_PA:.1%} div": hsi_px * (1 + HSI_DIV_PA) ** years}
    rep = {}
    for vname, hsi in variants.items():
        prices = pd.DataFrame({"IN equity": eq["loser_plain"], "Gold": to_inr(rd("TVC_GOLD.csv"), usd, idx),
                               "NDX": to_inr(rd("NASDAQ_NDX.csv"), usd, idx), "HSI": hsi})
        rep[vname] = {"hsi_alone": stats(hsi), "corr": prices.pct_change().corr().round(2).to_dict(),
                      "mixes": {k: {"weights": w, **stats(blend_multi(prices, w))} for k, w in MIXES.items()}}
    out = HERE / "hsi_blend"
    out.mkdir(exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, indent=2, default=str))
    print("done")


if __name__ == "__main__":
    main()
