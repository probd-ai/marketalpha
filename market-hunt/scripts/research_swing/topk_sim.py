"""Phase D: concentration + laddered top-K simulator with costs. Selection on IS only.

Model: each trading day, rank universe by signal; the day's cohort = top K.
Book = h equal cohorts (h-day ladder). Daily portfolio return = mean fr1 of the
h live cohorts' names. Cost = roundtrip_bps charged on cohort turnover
(entering names) spread over the day they enter.
"""
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
df = pd.read_parquet(os.path.join(HERE, "panel.parquet"))

WIN = {"IS": ("2018-07-01", "2022-12-31"), "OOS": ("2023-01-01", "2026-06-19")}
ROUNDTRIP_BPS = 30.0  # delivery: brokerage~0 + STT 0.1% is 10bps/side? use 30bps RT incl slippage

def ladder_sim(d, signal, K=20, h=10, window="IS", universe="in_n500",
               ascending=False, min_lvl=None, lvl_col=None):
    lo, hi = WIN[window]
    d = d[d[universe]].copy()
    d = d[(d["date"] >= lo) & (d["date"] <= hi)]
    d = d.dropna(subset=[signal, "fr1", "bfr1"])
    if min_lvl is not None:
        d = d[d[lvl_col] >= min_lvl]
    dates = np.sort(d["date"].unique())
    by_date = dict(tuple(d.groupby("date")))
    # daily top-K cohort
    cohorts = {}
    for t in dates:
        s = by_date[t].sort_values(signal, ascending=ascending)
        cohorts[t] = list(s["symbol"].head(K))
    # forward 1d returns lookup
    fr1 = d.set_index(["date", "symbol"])["fr1"]
    bfr = d.groupby("date")["bfr1"].first()
    rets, brets, turns = [], [], []
    live = []  # list of cohorts (sets)
    prev_book = set()
    for t in dates:
        live.append(set(cohorts[t]))
        if len(live) > h:
            live.pop(0)
        book = set().union(*live)
        # daily return of book (equal-weight names)
        vals = [fr1.get((t, sym), np.nan) for sym in book]
        vals = [v for v in vals if not np.isnan(v)]
        if not vals:
            continue
        gross = float(np.mean(vals))
        entering = len(book - prev_book)
        turn = entering / max(len(book), 1)
        cost = turn * (ROUNDTRIP_BPS / 1e4)
        rets.append(gross - cost)
        brets.append(float(bfr.get(t, np.nan)))
        turns.append(turn)
        prev_book = book
    r = np.array(rets); b = np.array([x for x in brets if not np.isnan(x)])
    yrs = len(r) / 252
    cagr = ((1 + r).prod()) ** (1 / yrs) - 1
    bcagr = ((1 + b).prod()) ** (1 / max(len(b) / 252, 1e-9)) - 1
    vol = r.std() * np.sqrt(252)
    sharpe = ((1 + r.mean()) ** 252 - 1 - 0.06) / vol if vol > 0 else 0
    eq = (1 + r).cumprod(); peak = np.maximum.accumulate(eq)
    mdd = ((eq / peak) - 1).min()
    return {"cagr": cagr * 100, "bench_cagr": bcagr * 100, "alpha": (cagr - bcagr) * 100,
            "sharpe": sharpe, "maxDD": mdd * 100, "avg_turn/day": np.mean(turns) * 100,
            "avg_book": np.mean([len(set().union(*live))])}

pd.set_option("display.float_format", lambda v: f"{v:+.2f}")

print("=== decile monotonicity: mm -> mean xr10 (bps), IS ===")
d = df[df["in_n500"] & (df["date"] >= "2018-07-01") & (df["date"] <= "2022-12-31")].dropna(subset=["mm", "xr10"]).copy()
d["D"] = d.groupby("date")["mm"].transform(lambda s: pd.qcut(s.rank(method="first"), 10, labels=False))
print((d.groupby("D")["xr10"].mean() * 1e4).round(1).to_string())
d100 = d[d["in_n100"]].copy()
if len(d100):
    d100["D"] = d100.groupby("date")["mm"].transform(lambda s: pd.qcut(s.rank(method="first"), 10, labels=False, duplicates="drop"))
    print("--- same, N100 only ---")
    print((d100.groupby("D")["xr10"].mean() * 1e4).round(1).to_string())

print("\n=== aligned-stack cell at 10d (score>90&ms>80&mm>60&ml>50), IS mean xr10 bps ===")
al = d[(d["score"] > 90) & (d["ms"] > 80) & (d["mm"] > 60) & (d["ml"] > 50)]
print(f"n/day={len(al)/d['date'].nunique():.1f}  mean xr10={al['xr10'].mean()*1e4:+.1f} bps  (universe {d['xr10'].mean()*1e4:+.1f})")

print("\n=== ladder sims (IS 2018-07..2022-12, costs 30bps RT) ===")
combo_cols = None
runs = [
    ("top20 mm, h=10, N500", dict(signal="mm", K=20, h=10)),
    ("top20 mm, h=10, N100", dict(signal="mm", K=20, h=10, universe="in_n100")),
    ("top50 mm, h=10, N500", dict(signal="mm", K=50, h=10)),
    ("top20 mm, h=5,  N500", dict(signal="mm", K=20, h=5)),
    ("top20 ms, h=10, N500", dict(signal="ms", K=20, h=10)),
    ("top20 roc20, h=10, N500 (baseline)", dict(signal="roc20", K=20, h=10)),
]
# composite: rank(mm) + reversal tilt
df["_r_mm"] = df.groupby("date")["mm"].rank(pct=True)
df["_r_rev"] = df.groupby("date")["d5_score"].rank(pct=True, ascending=False)
df["combo"] = 0.75 * df["_r_mm"] + 0.25 * df["_r_rev"]
runs.append(("top20 combo(.75mm+.25rev), h=10, N500", dict(signal="combo", K=20, h=10)))
rows = []
for name, kw in runs:
    r = ladder_sim(df, window="IS", **kw)
    rows.append({"strategy": name, **r})
print(pd.DataFrame(rows).to_string(index=False))
