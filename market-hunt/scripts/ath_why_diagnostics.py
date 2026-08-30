"""Why does buying the names furthest below ATH (within the qualified universe)
generate alpha? Produce the evidence, not the story:

  1. Pool every (rebalance date, symbol) pair from the stored base universe with its
     ATH-distance ratio (close/running-ATH, PIT-safe) and its forward 1-month return
     (snapshot close -> next snapshot close).
  2. Aggregate: forward return by fixed ATH-distance band, overall and per year.
  3. Monthly bottom10-minus-universe spread series (equal-weight, no charges) —
     where the alpha actually accrues in time.

Writes scripts/output/ath_why_<tag>.json for visualization.
"""
import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from settings import MONGO_URI, DB_NAME  # noqa: E402
from scripts.ath_filter_sim import close_frame  # noqa: E402

BANDS = [(0.00, 0.01), (0.01, 0.05), (0.05, 0.10), (0.10, 0.20),
         (0.20, 0.30), (0.30, 0.50), (0.50, 1.00)]  # distance below ATH


def band_label(lo, hi):
    return f"{lo*100:.0f}–{hi*100:.0f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-key", required=True)
    ap.add_argument("--tag", default="n500")
    args = ap.parse_args()

    db = MongoClient(MONGO_URI)[DB_NAME]
    manifest = db.base_universe_runs.find_one({"run_key": args.run_key})
    snaps_docs = list(db.base_universe.find({"run_key": args.run_key}).sort("date", 1))
    snaps = [(pd.Timestamp(s["date"]), sorted(s["symbols"])) for s in snaps_docs]
    all_syms = sorted(set().union(*(set(s) for _, s in snaps)))
    end_dt = datetime.strptime(manifest["end"], "%Y-%m-%d")

    print(f"{len(snaps)} snapshots, {len(all_syms)} symbols — loading prices…")
    px = close_frame(db, all_syms, end_dt).ffill()
    ath_ratio = px / px.cummax()

    # 1. pooled records
    recs = []
    for k in range(len(snaps) - 1):
        d0, syms = snaps[k]
        d1 = snaps[k + 1][0]
        for s in syms:
            if s not in px.columns:
                continue
            p0, p1, r = px.at[d0, s], px.at[d1, s], ath_ratio.at[d0, s]
            if np.isnan(p0) or np.isnan(p1) or np.isnan(r) or p0 <= 0:
                continue
            recs.append((d0, s, 1.0 - r, p1 / p0 - 1.0))  # dist below ATH, fwd ret
    df = pd.DataFrame(recs, columns=["date", "symbol", "dist", "fwd"])
    print(f"pooled observations: {len(df)}")

    # 2. band aggregation, overall + by year
    def agg(sub):
        out = []
        for lo, hi in BANDS:
            m = sub[(sub.dist >= lo) & (sub.dist < hi)]
            if len(m) == 0:
                out.append({"band": band_label(lo, hi), "n": 0})
                continue
            out.append({
                "band": band_label(lo, hi), "n": int(len(m)),
                "mean_fwd_pct": round(m.fwd.mean() * 100, 3),
                "median_fwd_pct": round(m.fwd.median() * 100, 3),
                "hit_rate_pct": round((m.fwd > 0).mean() * 100, 1),
                "p10_pct": round(m.fwd.quantile(0.10) * 100, 2),
                "p90_pct": round(m.fwd.quantile(0.90) * 100, 2),
            })
        return out

    bands_overall = agg(df)
    bands_by_year = {str(y): agg(g) for y, g in df.groupby(df.date.dt.year)}

    # 3. monthly bottom10-minus-universe spread
    spread = []
    for d0, g in df.groupby("date"):
        bottom = g.nlargest(10, "dist")  # largest distance = furthest below ATH
        spread.append({
            "date": d0.strftime("%Y-%m-%d"),
            "bottom10_fwd_pct": round(bottom.fwd.mean() * 100, 3),
            "universe_fwd_pct": round(g.fwd.mean() * 100, 3),
            "spread_pct": round((bottom.fwd.mean() - g.fwd.mean()) * 100, 3),
            "bottom10_avg_dist_pct": round(bottom.dist.mean() * 100, 1),
            "n_universe": int(len(g)),
            "bottom10_syms": bottom.symbol.tolist(),
        })

    out = {
        "run_key": args.run_key, "universe": manifest["universe"],
        "window": [manifest["start"], manifest["end"]],
        "n_obs": int(len(df)), "n_snapshots": len(snaps),
        "bands_overall": bands_overall, "bands_by_year": bands_by_year,
        "monthly_spread": spread,
    }
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    path = os.path.join(out_dir, f"ath_why_{args.tag}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {path}")

    print("\nforward 1-month return by distance-below-ATH band (pooled):")
    for b in bands_overall:
        if b["n"]:
            print(f"  {b['band']:>8}: n={b['n']:>6}  mean={b['mean_fwd_pct']:>6.2f}%  "
                  f"median={b['median_fwd_pct']:>6.2f}%  hit={b['hit_rate_pct']:>5.1f}%")


if __name__ == "__main__":
    main()
