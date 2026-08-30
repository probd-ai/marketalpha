"""One-shot OOS validation (2023-01..2026-06) of IS-selected configs.

Configs frozen from IS: top50 mm h=10; top20 mm h=10; top20 combo h=10.
Variant: classic 200d-SMA regime gate on Nifty 500 (pre-registered, untuned):
book scales to cash while bench < SMA200.
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

def ladder_sim(d, signal, K, h, lo, hi, universe="in_n500", regime_gate=False):
    d = d[d[universe]].copy()
    d = d[(d["date"] >= lo) & (d["date"] <= hi)].dropna(subset=[signal, "fr1", "bfr1"])
    dates = np.sort(d["date"].unique())
    by_date = dict(tuple(d.groupby("date")))
    fr1 = d.set_index(["date", "symbol"])["fr1"]
    bfr = d.groupby("date")["bfr1"].first()
    bench_close = d.groupby("date")["bench"].first().sort_index()
    sma200 = bench_close.rolling(200, min_periods=100).mean()
    risk_on = (bench_close >= sma200)

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
        if regime_gate and not bool(risk_on.get(t, True)):
            r_day = 0.0  # in cash (0% for simplicity; rf would add ~6% ann on gated days)
        rets.append(r_day)
        brets.append(float(bfr.get(t, np.nan)))
        years.append(pd.Timestamp(t).year)
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
    bys = pd.Series(b, index=years)
    yearly = ((1 + ys).groupby(level=0).prod() - 1) * 100
    byearly = ((1 + bys).groupby(level=0).prod() - 1) * 100
    return {"cagr": cagr * 100, "bench": bcagr * 100, "alpha": (cagr - bcagr) * 100,
            "sharpe": sharpe, "maxDD": mdd * 100}, yearly, byearly

LO, HI = "2023-01-01", "2026-06-19"
pd.set_option("display.float_format", lambda v: f"{v:+.2f}")
rows, yearlies = [], {}
for name, kw in [
    ("top50 mm h10",            dict(signal="mm", K=50, h=10)),
    ("top20 mm h10",            dict(signal="mm", K=20, h=10)),
    ("top20 combo h10",         dict(signal="combo", K=20, h=10)),
    ("top50 mm h10 +200dSMA",   dict(signal="mm", K=50, h=10, regime_gate=True)),
]:
    stats, yearly, byearly = ladder_sim(df, lo=LO, hi=HI, **kw)
    rows.append({"strategy": name, **stats})
    yearlies[name] = yearly
print("=== OOS 2023-01..2026-06 (net 30bps RT) ===")
print(pd.DataFrame(rows).to_string(index=False))
print("\nyearly returns % (top50 mm h10):")
out = pd.DataFrame({"strat": yearlies["top50 mm h10"], "bench": byearly})
print(out.to_string())
