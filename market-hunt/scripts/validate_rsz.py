#!/usr/bin/env python3
"""Head-to-head validation: RSZ vs truevx, on identical dates and symbols.

Writes the result to research_results/_id=rsz_validation so the analytics page
serves it precomputed. Nothing here is allowed to flatter RSZ:
  * both indicators are scored on the SAME (date, symbol) intersection
  * forward returns are identical inputs for both
  * significance uses a stationary block bootstrap over DATES, which keeps the
    cross-sectional correlation of a trading day intact and handles the heavy
    overlap of forward windows
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from pymongo import MongoClient  # noqa: E402
from scipy.stats import rankdata  # noqa: E402

import settings  # noqa: E402
from rsz_compute import COLLECTION  # noqa: E402
from rsz_engine import RszParams  # noqa: E402

HORIZONS = (5, 10, 22, 66)
BOOT = 2000
rng = np.random.default_rng(17)


def block_boot_t(series: np.ndarray, L: int) -> tuple[float, float]:
    """Bootstrap mean + two-sided p for a daily series with autocorrelation."""
    s = series[np.isfinite(series)]
    if len(s) < 30:
        return float("nan"), float("nan")
    obs = s.mean()
    n = len(s)
    out = np.empty(BOOT)
    for i in range(BOOT):
        picks = []
        while len(picks) < n:
            st = rng.integers(0, n)
            ln = min(rng.geometric(1 / L), n - st)
            picks.extend(range(st, st + ln))
        out[i] = s[np.array(picks[:n])].mean()
    p = (np.sum(np.abs(out - out.mean()) >= abs(obs)) + 1) / (BOOT + 1)
    return float(obs), float(p)


def main() -> int:
    db = MongoClient(settings.MONGO_URI)[settings.DB_NAME]
    ph = RszParams().hash
    print(f"loading rsz (param_hash={ph}) ...")

    rsz = {}
    for d in db[COLLECTION].find({"param_hash": ph},
                                 {"symbol": 1, "date": 1, "rsz_score": 1, "z_comp": 1,
                                  "aligned": 1, "segment": 1}):
        rsz[(d["symbol"], d["date"].strftime("%Y-%m-%d"))] = (
            d["rsz_score"], d["z_comp"], d["aligned"], d["segment"])
    print(f"  {len(rsz):,} rsz rows")

    syms = sorted({k[0] for k in rsz})
    tvx = {}
    for d in db.indicators.find(
            {"symbol": {"$in": syms}, "indicator_type": "truevx"},
            {"symbol": 1, "date": 1, "data.truevx_score": 1, "data.mean_long": 1}):
        dd = d.get("data") or {}
        if dd.get("truevx_score") is not None:
            tvx[(d["symbol"], d["date"].strftime("%Y-%m-%d"))] = (
                dd["truevx_score"], dd.get("mean_long"))
    print(f"  {len(tvx):,} truevx rows")

    both = sorted(set(rsz) & set(tvx))
    dates = sorted({k[1] for k in both})
    usyms = sorted({k[0] for k in both})
    print(f"  intersection: {len(both):,} obs · {len(dates)} dates · {len(usyms)} symbols")

    closes = {}
    lo = datetime.strptime(dates[0], "%Y-%m-%d")
    hi = datetime.strptime(dates[-1], "%Y-%m-%d")
    for coll in [c for c in db.list_collection_names() if c.startswith("prices_")]:
        for d in db[coll].find({"symbol": {"$in": usyms},
                                "date": {"$gte": lo, "$lte": hi}},
                               {"symbol": 1, "date": 1, "close_price": 1}):
            closes.setdefault(d["symbol"], {})[d["date"].strftime("%Y-%m-%d")] = \
                float(d["close_price"])

    di = {d: i for i, d in enumerate(dates)}
    sj = {s: j for j, s in enumerate(usyms)}
    D, S = len(dates), len(usyms)
    C = np.full((D, S), np.nan)
    for s, m in closes.items():
        j = sj.get(s)
        if j is None:
            continue
        for d, v in m.items():
            if d in di:
                C[di[d], j] = v

    RS = np.full((D, S), np.nan); TV = np.full((D, S), np.nan)
    ML = np.full((D, S), np.nan); AL = np.full((D, S), np.nan)
    for (s, d), (score, _z, al, _seg) in rsz.items():
        if (s, d) in tvx and d in di and s in sj:
            RS[di[d], sj[s]] = score
            AL[di[d], sj[s]] = al
    for (s, d), (score, ml) in tvx.items():
        if (s, d) in rsz and d in di and s in sj:
            TV[di[d], sj[s]] = score
            if ml is not None:
                ML[di[d], sj[s]] = ml

    out = {"_id": "rsz_validation", "generated_at": datetime.now().isoformat(),
           "param_hash": ph, "window": f"{dates[0]} .. {dates[-1]}",
           "n_obs": len(both), "n_dates": D, "n_symbols": S, "horizons": list(HORIZONS),
           "metrics": {}, "deciles": {}, "alignment": {}, "degeneracy": {}}

    # ── degeneracy: can the top of the book even be ordered? ────────────────
    for name, M in (("rsz_score", RS), ("truevx_score", TV), ("mean_long", ML)):
        v = M[np.isfinite(M)]
        top = np.percentile(v, 90)
        tie_at_max = float((v == v.max()).mean() * 100)
        # share of the top decile that is stuck on a single value
        td = v[v >= top]
        vals, cnt = np.unique(np.round(td, 6), return_counts=True)
        out["degeneracy"][name] = {
            "pct_at_single_max_value": round(tie_at_max, 3),
            "distinct_values_in_top_decile": int(len(vals)),
            "largest_tie_block_in_top_decile_pct": round(float(cnt.max() / len(td) * 100), 2),
            "p50": round(float(np.percentile(v, 50)), 2),
            "p90": round(float(top), 2), "p99": round(float(np.percentile(v, 99)), 2),
        }

    # ── IC + decile spread per horizon ─────────────────────────────────────
    for H in HORIZONS:
        FR = np.full((D, S), np.nan)
        FR[:D - H] = (C[H:] / C[:D - H] - 1) * 100
        for name, M in (("rsz_score", RS), ("truevx_score", TV), ("mean_long", ML)):
            ics = np.full(D, np.nan); spr = np.full(D, np.nan)
            for t in range(D):
                m = np.isfinite(M[t]) & np.isfinite(FR[t])
                if m.sum() < 40:
                    continue
                x, y = M[t, m], FR[t, m]
                ics[t] = np.corrcoef(rankdata(x), rankdata(y))[0, 1]
                q = np.quantile(x, [0.1, 0.9])
                a, b = y[x <= q[0]], y[x >= q[1]]
                if len(a) >= 3 and len(b) >= 3:
                    spr[t] = b.mean() - a.mean()
            ic_m, ic_p = block_boot_t(ics, H)
            sp_m, sp_p = block_boot_t(spr, H)
            key = f"{name}@{H}"
            out["metrics"][key] = {
                "indicator": name, "horizon": H,
                "ic": round(ic_m, 5), "ic_p": round(ic_p, 4),
                "spread_pct": round(sp_m, 4), "spread_p": round(sp_p, 4),
                "spread_annualized_pct": round(sp_m * (252 / H), 2),
                "n_days": int(np.isfinite(ics).sum()),
            }
            print(f"  {key:22s} IC={ic_m:+.4f} (p={ic_p:.3f})  "
                  f"spread={sp_m:+.3f}% (p={sp_p:.3f})  ann={sp_m * (252 / H):+.2f}%")

        # decile curve at this horizon, for the chart
        for name, M in (("rsz_score", RS), ("truevx_score", TV)):
            rows = []
            for dec in range(10):
                lo_q, hi_q = dec / 10, (dec + 1) / 10
                vals = []
                for t in range(D):
                    m = np.isfinite(M[t]) & np.isfinite(FR[t])
                    if m.sum() < 40:
                        continue
                    x, y = M[t, m], FR[t, m]
                    a, b = np.quantile(x, [lo_q, hi_q])
                    sel = (x >= a) & (x <= b) if dec == 9 else (x >= a) & (x < b)
                    if sel.sum():
                        vals.append(y[sel].mean())
                rows.append({"decile": dec + 1,
                             "mean_fwd_pct": round(float(np.mean(vals)), 4) if vals else None,
                             "n_days": len(vals)})
            out["deciles"][f"{name}@{H}"] = rows

        # alignment: forward return by how many horizons agree
        rows = []
        for a in range(4):
            vals = []
            for t in range(D):
                m = (AL[t] == a) & np.isfinite(FR[t])
                if m.sum():
                    vals.append(FR[t, m].mean())
            base = []
            for t in range(D):
                m = np.isfinite(AL[t]) & np.isfinite(FR[t])
                if m.sum():
                    base.append(FR[t, m].mean())
            rows.append({"aligned": a,
                         "mean_fwd_pct": round(float(np.mean(vals)), 4) if vals else None,
                         "excess_vs_all_pct": round(float(np.mean(vals) - np.mean(base)), 4)
                         if vals and base else None,
                         "share_pct": round(float(np.nansum(AL == a) /
                                                  np.isfinite(AL).sum() * 100), 2)})
        out["alignment"][f"H{H}"] = rows

    db.research_results.replace_one({"_id": "rsz_validation"}, out, upsert=True)
    print("\nstored research_results/_id=rsz_validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
