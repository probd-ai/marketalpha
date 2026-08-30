"""Final composite: top-K combo(0.75*mm + 0.25*dip) + 200d SMA gate.
Both components frozen before OOS. Report IS, OOS, and full-period.
Also: internal-breadth gate diagnostic (EXPLORATORY, not validated).
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
df = pd.read_parquet(os.path.join(HERE, "panel.parquet"))
df["_r_mm"] = df.groupby("date")["mm"].rank(pct=True)
df["_r_rev"] = df.groupby("date")["d5_score"].rank(pct=True, ascending=False)
df["combo"] = 0.75 * df["_r_mm"] + 0.25 * df["_r_rev"]
ROUNDTRIP_BPS = 30.0

def ladder_sim(d, signal, K, h, lo, hi, universe="in_n500", gate=None):
    d = d[d[universe]].copy()
    d = d[(d["date"] >= lo) & (d["date"] <= hi)].dropna(subset=[signal, "fr1", "bfr1"])
    dates = np.sort(d["date"].unique())
    by_date = dict(tuple(d.groupby("date")))
    fr1 = d.set_index(["date", "symbol"])["fr1"]
    bfr = d.groupby("date")["bfr1"].first()
    bench_close = d.groupby("date")["bench"].first().sort_index()
    sma200 = bench_close.rolling(200, min_periods=100).mean()
    price_on = (bench_close >= sma200)
    # internal breadth: fraction of universe with mm > 50 (uses full panel to warm up)
    breadth = d.groupby("date").apply(lambda s: (s["mm"] > 50).mean(), include_groups=False)
    breadth_on = breadth >= 0.4

    rets, brets, years = [], [], []
    live, prev_book = [], set()
    for t in dates:
        s = by_date[t].sort_values(signal, ascending=False)
        live.append(set(s["symbol"].head(K)))
        if len(live) > h:
            live.pop(0)
        book = set().union(*live)
        vals = [fr1.get((t, sym), np.nan) for sym in book]
        vals = [v for v in vals if not np.isnan(v)]
        if not vals:
            continue
        gross = float(np.mean(vals))
        turn = len(book - prev_book) / max(len(book), 1)
        r_day = gross - turn * (ROUNDTRIP_BPS / 1e4)
        if gate == "price" and not bool(price_on.get(t, True)):
            r_day = 0.0
        elif gate == "breadth" and not bool(breadth_on.get(t, True)):
            r_day = 0.0
        rets.append(r_day); brets.append(float(bfr.get(t, np.nan))); years.append(pd.Timestamp(t).year)
        prev_book = book
    r, b = np.array(rets), np.array(brets)
    yrs = len(r) / 252
    cagr = ((1 + r).prod()) ** (1 / yrs) - 1
    bcagr = ((1 + b[~np.isnan(b)]).prod()) ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(252)
    sharpe = ((1 + r.mean()) ** 252 - 1 - 0.06) / vol if vol > 0 else 0
    eq = (1 + r).cumprod(); peak = np.maximum.accumulate(eq)
    mdd = ((eq / peak) - 1).min()
    ys = pd.Series(r, index=years)
    yearly = ((1 + ys).groupby(level=0).prod() - 1) * 100
    return {"cagr": cagr * 100, "bench": bcagr * 100, "alpha": (cagr - bcagr) * 100,
            "sharpe": sharpe, "maxDD": mdd * 100}, yearly

pd.set_option("display.float_format", lambda v: f"{v:+.2f}")
rows = {}
for period, (lo, hi) in {"IS 2018-22": ("2018-07-01", "2022-12-31"),
                          "OOS 2023-26": ("2023-01-01", "2026-06-19"),
                          "FULL 2018-26": ("2018-07-01", "2026-06-19")}.items():
    stats, yearly = ladder_sim(df, signal="combo", K=50, h=10, lo=lo, hi=hi, gate="price")
    rows[period] = stats
    if period == "FULL 2018-26":
        fy = yearly
print("=== FINAL: top50 combo(.75mm+.25dip) h10 N500 + 200d gate (net 30bps) ===")
print(pd.DataFrame(rows).T.to_string())
print("\nyearly % (full period):")
print(fy.to_string())

# exploratory: breadth gate instead of price gate (OOS only, diagnostic)
stats_b, _ = ladder_sim(df, signal="combo", K=50, h=10, lo="2023-01-01", hi="2026-06-19", gate="breadth")
print("\n[exploratory] breadth-gate (mm>50 frac>=0.4) OOS:", {k: round(v, 2) for k, v in stats_b.items()})
