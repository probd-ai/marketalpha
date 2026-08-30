"""Discover natural clusters/regimes in the TrueVX indicator db.

Goal (user): find the REAL structure in the indicator space, and test the
hypothesis that the right partition is "5 equal zones of the 0-100 score."

Features per stock-date (the state vector the engine actually produces):
    level = truevx_score                (0-100, RS height vs Nifty 50)
    vel   = truevx_score - mean_short    (score above/below its 22d avg -> velocity)
    curv  = mean_short  - mean_long      (short avg vs long avg -> trend of the score)

We DON'T assume k=5 or equal-width bins. We let the data choose:
  - 1-D: where are the natural modes of `level`? (GMM BIC sweep on level alone)
  - equal-zone test: are 0-20/20-40/.../80-100 even populated?
  - multi-D: cluster standardized [level, vel, curv]; pick k by GMM-BIC and
    k-means silhouette; characterize the centroids as regimes.

Read-only on `indicators`. Prints a numeric report; writes a feature sample
to scratchpad for the visualization step.
"""
import sys
import numpy as np
import pandas as pd
from pymongo import MongoClient
from datetime import datetime
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.mixture import GaussianMixture
from sklearn.metrics import silhouette_score

SAMPLE_N = 80000
START = datetime(2020, 1, 1)
OUT_PARQUET = sys.argv[1] if len(sys.argv) > 1 else None

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
ind = db["indicators"]

# --- pull a representative random sample of stock-dates (>= 2020) -----------
pipeline = [
    {"$match": {"indicator_type": "truevx", "date": {"$gte": START}}},
    {"$sample": {"size": SAMPLE_N}},
    {"$project": {"_id": 0, "symbol": 1, "date": 1,
                  "s": "$data.truevx_score", "ms": "$data.mean_short",
                  "ml": "$data.mean_long", "struct": "$data.structural_score",
                  "trend": "$data.trend_score"}},
]
rows = list(ind.aggregate(pipeline, allowDiskUse=True))
df = pd.DataFrame(rows)
print(f"sampled stock-dates (>=2020): {len(df)}")
df = df.dropna(subset=["s", "ms", "ml"])
print(f"after dropping null mean_short/mean_long: {len(df)}")

df["level"] = df["s"].astype(float)
df["vel"] = df["s"].astype(float) - df["ms"].astype(float)
df["curv"] = df["ms"].astype(float) - df["ml"].astype(float)
X = df[["level", "vel", "curv"]].to_numpy()

# --- (1) distribution + the user's equal-5-zone test ------------------------
print("\n=== (1) score distribution & equal-5-zone populations ===")
lv = df["level"].to_numpy()
print(f"level: min={lv.min():.1f} p5={np.percentile(lv,5):.1f} "
      f"median={np.median(lv):.1f} mean={lv.mean():.1f} "
      f"p95={np.percentile(lv,95):.1f} max={lv.max():.1f} std={lv.std():.1f}")
edges = [0, 20, 40, 60, 80, 100]
for a, b in zip(edges, edges[1:]):
    n = int(((lv >= a) & (lv < b)).sum()) if b < 100 else int(((lv >= a) & (lv <= b)).sum())
    bar = "#" * int(60 * n / len(lv))
    print(f"  zone {a:>3}-{b:<3}: {n:>6} ({100*n/len(lv):4.1f}%) {bar}")

# --- (2) 1-D natural modes of `level` (GMM BIC sweep) -----------------------
print("\n=== (2) natural modes of the 0-100 score (GMM-BIC, 1-D) ===")
L = lv.reshape(-1, 1)
best = None
for k in range(1, 8):
    g = GaussianMixture(n_components=k, covariance_type="full",
                        random_state=0, n_init=3).fit(L)
    bic = g.bic(L)
    flag = ""
    if best is None or bic < best[1]:
        best = (k, bic, g); flag = " <-- best so far"
    print(f"  k={k}: BIC={bic:11.0f}{flag}")
k1, _, g1 = best
mus = np.sort(g1.means_.ravel())
print(f"  -> data prefers {k1} mode(s); centers at "
      f"{', '.join(f'{m:.1f}' for m in mus)}")

# --- (3) multi-D clustering on standardized [level, vel, curv] ---------------
print("\n=== (3) multi-D regimes on [level, vel, curv] (standardized) ===")
Xs = StandardScaler().fit_transform(X)
sub = Xs[np.random.RandomState(0).choice(len(Xs), min(10000, len(Xs)), replace=False)]
print("  k-means model selection (silhouette on 10k subsample):")
sil_best = None
for k in range(2, 9):
    km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(sub)
    sil = silhouette_score(sub, km.labels_)
    flag = ""
    if sil_best is None or sil > sil_best[1]:
        sil_best = (k, sil); flag = " <-- best"
    print(f"    k={k}: silhouette={sil:.3f}{flag}")
print("  GMM model selection (BIC, full sample):")
gmm_best = None
for k in range(2, 9):
    g = GaussianMixture(n_components=k, covariance_type="full",
                        random_state=0, n_init=2).fit(Xs)
    bic = g.bic(Xs)
    flag = ""
    if gmm_best is None or bic < gmm_best[1]:
        gmm_best = (k, bic); flag = " <-- best"
    print(f"    k={k}: BIC={bic:11.0f}{flag}")

k_use = sil_best[0]
print(f"\n  characterizing k={k_use} k-means regimes (centroids in real units):")
km = KMeans(n_clusters=k_use, n_init=10, random_state=0).fit(Xs)
df["cluster"] = km.labels_
cent = df.groupby("cluster")[["level", "vel", "curv"]].mean()
sizes = df["cluster"].value_counts(normalize=True).sort_index() * 100
hdr = f"  {'clus':>4} {'size%':>6} {'level':>7} {'vel':>7} {'curv':>7}  interpretation"
print(hdr)
for c in sorted(df["cluster"].unique()):
    L_, V_, Cu_ = cent.loc[c, "level"], cent.loc[c, "vel"], cent.loc[c, "curv"]
    lvl = "high" if L_ > 60 else "low" if L_ < 40 else "mid"
    mo = "rising" if V_ > 1 else "falling" if V_ < -1 else "flat"
    tr = "up-trend" if Cu_ > 1 else "down-trend" if Cu_ < -1 else "neutral"
    print(f"  {c:>4} {sizes.loc[c]:>6.1f} {L_:>7.1f} {V_:>7.2f} {Cu_:>7.2f}  "
          f"{lvl} RS, {mo}, {tr}")

if OUT_PARQUET:
    df[["symbol", "date", "level", "vel", "curv", "cluster"]].to_parquet(OUT_PARQUET)
    print(f"\nwrote feature sample -> {OUT_PARQUET}")
print("\n[discovery only — cluster value vs forward returns is a separate step]")
