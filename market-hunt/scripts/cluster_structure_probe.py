"""Is the indicator really ONE axis? Correlation + PCA + off-diagonal regimes.

Reads the feature sample written by discover_indicator_clusters.py. Tests whether
[level, vel, curv] collapse to a single momentum factor, and counts the
strategically interesting OFF-diagonal states (high RS but falling = fading
leader; low RS but rising = early recovery) that a pure rank would miss.
"""
import sys
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA

df = pd.read_parquet(sys.argv[1])
F = df[["level", "vel", "curv"]]

print("=== correlation of the 3 features ===")
print(F.corr().round(3).to_string())

Xs = StandardScaler().fit_transform(F.to_numpy())
p = PCA().fit(Xs)
ev = p.explained_variance_ratio_
print("\n=== PCA explained variance ===")
for i, e in enumerate(ev):
    print(f"  PC{i+1}: {e*100:5.1f}%   (cum {ev[:i+1].sum()*100:5.1f}%)")
print("  PC1 loadings [level, vel, curv]:",
      np.round(p.components_[0], 3).tolist())

print("\n=== off-diagonal regimes (level x velocity quadrants) ===")
lv, ve = df["level"].to_numpy(), df["vel"].to_numpy()
hi, rise = lv >= 60, ve > 0
cells = {
    "high RS + rising  (fresh leaders)":  (hi & rise),
    "high RS + falling (fading leaders)": (hi & ~rise),
    "low RS + rising   (early recovery)": (~hi & rise),
    "low RS + falling  (laggards)":       (~hi & ~rise),
}
for name, mask in cells.items():
    n = int(mask.sum())
    print(f"  {name:38} {n:>6} ({100*n/len(df):4.1f}%)")
