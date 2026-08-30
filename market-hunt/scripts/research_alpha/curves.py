# Compare engine sim outputs: CAGR/Sharpe/maxDD per variant + 50/50 first+mid combo.
import json, os
import numpy as np
import pandas as pd

OUT = os.path.dirname(os.path.abspath(__file__))

def load(name):
    p = os.path.join(OUT, f"sim_{name}.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        data = json.load(f)
    sim = data.get("simulation", data)
    res = sim.get("results", [])
    s = pd.Series({pd.Timestamp(r["date"]): r["portfolio_value"] for r in res}).sort_index()
    return s, sim.get("summary", {})

def stats(c):
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    cagr = (c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    dd = (c / c.cummax() - 1).min()
    vol = r.std() * np.sqrt(252)
    return cagr * 100, sharpe, dd * 100, vol * 100

names = ["base750_mid", "base750_first", "n500_mid", "n200_mid", "micro_mid", "all750_mid", "top50_750_mid"]
curves = {}
print(f"{'variant':16s} {'CAGR%':>7s} {'Sharpe':>7s} {'maxDD%':>8s} {'vol%':>6s} {'final':>12s} {'bench%':>8s}")
for n in names:
    out = load(n)
    if out is None:
        print(f"{n:16s} (missing)")
        continue
    c, summ = out
    curves[n] = c
    cg, sh, dd, vol = stats(c)
    print(f"{n:16s} {cg:7.1f} {sh:7.2f} {dd:8.1f} {vol:6.1f} {c.iloc[-1]:12.0f} {summ.get('benchmark_return', float('nan')):8.1f}")

if "base750_mid" in curves and "base750_first" in curves:
    a, b = curves["base750_mid"], curves["base750_first"]
    idx = a.index.intersection(b.index)
    a, b = a.loc[idx], b.loc[idx]
    combo = (a / a.iloc[0] + b / b.iloc[0]) / 2
    cg, sh, dd, vol = stats(combo)
    print(f"{'combo_50_50':16s} {cg:7.1f} {sh:7.2f} {dd:8.1f} {vol:6.1f}")
    ra, rb = a.pct_change().dropna(), b.pct_change().dropna()
    print(f"\ndaily corr(mid, first) = {ra.corr(rb):.3f}")
    ma = a.resample('ME').last().pct_change().dropna()
    mb = b.resample('ME').last().pct_change().dropna()
    print(f"monthly corr(mid, first) = {ma.corr(mb):.3f}")
