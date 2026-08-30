"""Phase C: double-sorts — RS-strength level x short-term reversal. IS=2018-2022 only."""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
df = pd.read_parquet(os.path.join(HERE, "panel.parquet"))
df = df[df["in_n500"]].copy()

IS = df[(df["date"] >= "2018-07-01") & (df["date"] <= "2022-12-31")].copy()

def qcut_by_date(d, col, q=5):
    return d.groupby("date")[col].transform(
        lambda s: pd.qcut(s.rank(method="first"), q, labels=False, duplicates="drop"))

def cell_table(d, lev, chg, tgt, q=5):
    d = d.dropna(subset=[lev, chg, tgt]).copy()
    d["L"] = qcut_by_date(d, lev, q)
    d["C"] = qcut_by_date(d, chg, q)
    tab = d.pivot_table(index="L", columns="C", values=tgt, aggfunc="mean") * 1e4  # bps
    cnt = d.pivot_table(index="L", columns="C", values=tgt, aggfunc="count")
    return tab, cnt

pd.set_option("display.width", 200, "display.float_format", lambda v: f"{v:+.0f}")

for lev, chg, tgt in [("mm", "d5_score", "xr10"), ("mm", "roc5", "xr10"),
                      ("ml", "roc5", "xr10"), ("mm", "d5_score", "xr5"),
                      ("mm", "roc5", "xr5")]:
    tab, cnt = cell_table(IS, lev, chg, tgt)
    print(f"\n=== IS mean {tgt} (bps): rows={lev} quintile (0=low), cols={chg} quintile (0=most-negative) ===")
    print(tab.to_string())
    print(f"corner (hi {lev}, lo {chg}): {tab.iloc[-1, 0]:+.0f} bps | universe mean: {IS[tgt].mean()*1e4:+.0f} bps | corner n/day: {cnt.iloc[-1, 0] / IS['date'].nunique():.1f}")
