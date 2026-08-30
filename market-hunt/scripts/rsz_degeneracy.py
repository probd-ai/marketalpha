#!/usr/bin/env python3
"""Measure rank degeneracy the way it actually bites.

Ranking happens WITHIN one date (and, for RSZ, within one segment). Pooling every
observation across all dates conflates "two names tied today" with "the same value
occurred on two different days", which is not a problem at all. This measures the
real thing: on a given date, if you sort the cross-section and try to take the top
N names, how many of them are tied on the boundary and therefore chosen arbitrarily?

Updates research_results/_id=rsz_validation in place.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from pymongo import MongoClient  # noqa: E402

import settings  # noqa: E402
from rsz_compute import COLLECTION  # noqa: E402
from rsz_engine import RszParams  # noqa: E402

TOP_N = 10   # a realistic book size


def tie_profile(by_date: dict[str, list[float]]) -> dict:
    """For each date: sort desc, look at the value at the TOP_N boundary, count how
    many names share it. Those are the picks the sort cannot order."""
    boundary_ties, top_decile_tied, distinct_frac = [], [], []
    for _d, vals in by_date.items():
        v = np.array([x for x in vals if np.isfinite(x)])
        if len(v) < 40:
            continue
        sv = np.sort(v)[::-1]
        cutoff = sv[TOP_N - 1]
        boundary_ties.append(int((v == cutoff).sum()))
        k = max(1, len(v) // 10)
        td = sv[:k]
        u = len(np.unique(np.round(td, 9)))
        top_decile_tied.append((len(td) - u) / len(td) * 100)
        distinct_frac.append(u / len(td) * 100)
    if not boundary_ties:
        return {}
    bt = np.array(boundary_ties)
    return {
        "dates_measured": len(bt),
        "mean_names_tied_at_topN_boundary": round(float(bt.mean()), 2),
        "median_names_tied_at_topN_boundary": int(np.median(bt)),
        "worst_names_tied_at_topN_boundary": int(bt.max()),
        "pct_dates_topN_boundary_ambiguous": round(float((bt > 1).mean() * 100), 2),
        "mean_pct_of_top_decile_tied": round(float(np.mean(top_decile_tied)), 2),
        "mean_pct_of_top_decile_distinct": round(float(np.mean(distinct_frac)), 2),
    }


def main() -> int:
    db = MongoClient(settings.MONGO_URI)[settings.DB_NAME]
    ph = RszParams().hash

    # RSZ: cross-section is (date, segment)
    rsz_by: dict[str, list[float]] = {}
    syms = set()
    for d in db[COLLECTION].find({"param_hash": ph},
                                 {"symbol": 1, "date": 1, "segment": 1, "rsz_score": 1}):
        key = d["date"].strftime("%Y-%m-%d") + "|" + d["segment"]
        rsz_by.setdefault(key, []).append(d["rsz_score"])
        syms.add(d["symbol"])

    # truevx: cross-section is the date (one benchmark for everyone by design)
    tvx_by: dict[str, list[float]] = {}
    ml_by: dict[str, list[float]] = {}
    for d in db.indicators.find(
            {"symbol": {"$in": sorted(syms)}, "indicator_type": "truevx"},
            {"date": 1, "data.truevx_score": 1, "data.mean_long": 1}):
        dd = d.get("data") or {}
        k = d["date"].strftime("%Y-%m-%d")
        if dd.get("truevx_score") is not None:
            tvx_by.setdefault(k, []).append(dd["truevx_score"])
        if dd.get("mean_long") is not None:
            ml_by.setdefault(k, []).append(dd["mean_long"])

    prof = {"rsz_score": tie_profile(rsz_by),
            "truevx_score": tie_profile(tvx_by),
            "mean_long": tie_profile(ml_by)}

    print(f"top-{TOP_N} selection ambiguity (per cross-section)\n")
    hdr = ("indicator", "tied@boundary(mean)", "worst", "% dates ambiguous",
           "% of top decile tied")
    print(f"{hdr[0]:15s} {hdr[1]:>20s} {hdr[2]:>7s} {hdr[3]:>19s} {hdr[4]:>22s}")
    for k, v in prof.items():
        if not v:
            continue
        print(f"{k:15s} {v['mean_names_tied_at_topN_boundary']:>20.2f} "
              f"{v['worst_names_tied_at_topN_boundary']:>7d} "
              f"{v['pct_dates_topN_boundary_ambiguous']:>18.2f}% "
              f"{v['mean_pct_of_top_decile_tied']:>21.2f}%")

    db.research_results.update_one(
        {"_id": "rsz_validation"},
        {"$set": {"degeneracy_per_cross_section": prof,
                  "degeneracy_top_n": TOP_N,
                  "degeneracy_updated_at": datetime.now().isoformat()}})
    print("\nupdated research_results/_id=rsz_validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
