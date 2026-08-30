"""Phase B: cross-sectional rank-IC of candidate swing signals vs forward relative returns.

Universe: PIT NIFTY500 members only. Discovery window 2018-2022 drives selection;
2023-2026 shown for robustness (not used to pick).
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
df = pd.read_parquet(os.path.join(HERE, "panel.parquet"))
df = df[df["in_n500"]].copy()
df["year"] = df["date"].dt.year

SIGNALS = ["score", "ms", "mm", "ml", "gap_sms", "d5_score", "d5_ms", "roc20", "roc5"]
HORIZONS = [5, 10, 20]

def daily_ic(sub, sig, target):
    x = sub[[sig, target]].dropna()
    if len(x) < 50:
        return np.nan
    return x[sig].rank().corr(x[target].rank())

# Pre-group by date once
print(f"universe rows: {len(df):,}  dates: {df['date'].nunique()}")
results = []
for sig in SIGNALS:
    for h in HORIZONS:
        tgt = f"xr{h}"
        ics = df.groupby("date", sort=True).apply(
            lambda s: daily_ic(s, sig, tgt), include_groups=False)
        ics = ics.dropna()
        # non-overlapping subset for honest t-stat
        no = ics.iloc[::h]
        t = no.mean() / no.std() * np.sqrt(len(no)) if len(no) > 10 else np.nan
        yearly = ics.groupby(ics.index.year).mean()
        results.append({
            "signal": sig, "h": h,
            "IC": ics.mean(), "t(no-ovl)": t,
            **{str(y): yearly.get(y, np.nan) for y in range(2018, 2027)},
        })
res = pd.DataFrame(results)
pd.set_option("display.width", 250, "display.float_format", lambda v: f"{v:+.3f}")
is_cols = ["2018", "2019", "2020", "2021", "2022"]
oos_cols = ["2023", "2024", "2025", "2026"]
res["IC_IS"] = res[is_cols].mean(axis=1)
res["IC_OOS"] = res[oos_cols].mean(axis=1)
print(res[["signal", "h", "IC", "t(no-ovl)", "IC_IS", "IC_OOS"] + is_cols + oos_cols]
      .sort_values(["h", "IC_IS"], ascending=[True, False]).to_string(index=False))
