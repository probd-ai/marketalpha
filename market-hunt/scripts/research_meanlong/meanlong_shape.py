"""Living shape of mean_long — full-universe movement patterns + price relation.

mean_long = SMA222 of truevx_score (0-100, relative to Nifty 50). This script
characterizes, across every stock with stored truevx:

  A. velocity bounds     — empirical daily/21d step distribution vs the 0.45/day theoretical cap
  B. occupancy           — time spent per 10-pt zone (pooled + per-stock)
  C. phases              — rising/falling segments (21d slope sign, short runs collapsed):
                           duration, amplitude, and the price move during each phase
  D. zone transitions    — 20-pt zone state machine: where moves go next + traverse times
  E. level crosses       — 20/40/50/60/80 up/down crosses: forward abs & rel returns (21/63/126/252d)
  F. lead/lag            — cross-correlation of Δ21 mean_long vs 21d relative return (lag scan)

Output: meanlong_shape_results.json next to this script + console summary.
NOTE: uses all symbols with stored indicators (computed universe) — mild
survivorship tilt in forward-return aggregates; direction of bias noted in report.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import settings

OUT = Path(__file__).with_name("meanlong_shape_results.json")
HORIZONS = [21, 63, 126, 252]
CROSS_LEVELS = [20, 40, 50, 60, 80]
ZONES5 = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100.01)]
MIN_PHASE_DAYS = 10          # collapse direction flickers shorter than this
SLOPE_WIN = 21               # slope window for direction


def load_series(db):
    """symbol -> DataFrame(date, ml, close); plus bench closes."""
    print("loading mean_long …", flush=True)
    rows = db.indicators.find(
        {"indicator_type": "truevx", "base_symbol": "Nifty 50"},
        {"symbol": 1, "date": 1, "data.mean_long": 1, "_id": 0})
    ml = defaultdict(list)
    for d in rows:
        v = d["data"].get("mean_long")
        if v is not None:
            ml[d["symbol"]].append((d["date"], float(v)))
    print(f"  {len(ml)} symbols", flush=True)

    print("loading closes …", flush=True)
    closes = defaultdict(list)
    wanted = set(ml) | {"Nifty 50"}
    for col in sorted(c for c in db.list_collection_names() if c.startswith("prices_")):
        for d in db[col].find({"symbol": {"$in": list(wanted)}},
                              {"symbol": 1, "date": 1, "close_price": 1, "_id": 0}):
            c = d.get("close_price")
            if c:
                closes[d["symbol"]].append((d["date"], float(c)))

    def to_series(pairs):
        """Price rows carry intraday timestamps (09:15); indicators are
        midnight — normalize both to date-only before joining."""
        s = pd.Series(dict(pairs)).sort_index()
        s.index = pd.DatetimeIndex(s.index).normalize()
        return s[~s.index.duplicated(keep="last")]

    bench = to_series(closes.pop("Nifty 50"))
    out = {}
    for sym, mlist in ml.items():
        if sym not in closes or len(mlist) < 260:
            continue
        s_ml = to_series(mlist)
        s_px = to_series(closes[sym])
        df = pd.concat({"ml": s_ml, "px": s_px}, axis=1, join="inner")
        df["bench"] = bench.reindex(df.index)
        df = df.dropna()
        if len(df) >= 260:
            out[sym] = df
    print(f"  {len(out)} symbols with aligned ml+price ≥260d", flush=True)
    return out


def fwd_ret(px: np.ndarray, i: int, h: int):
    j = i + h
    if j >= len(px):
        return None
    return px[j] / px[i] - 1.0


def zone5(v: float) -> int:
    for k, (lo, hi) in enumerate(ZONES5):
        if lo <= v < hi:
            return k
    return 4


def analyze(series: dict) -> dict:
    res = {
        "n_symbols": len(series),
        "daily_step": {"abs_p50": [], "abs_p99": [], "abs_max": []},
        "vel21": [],                       # pooled 21d Δml samples (subsampled)
        "occupancy_days": np.zeros(10),    # 10-pt zones, pooled days
        "phases": [],                      # per-phase dicts
        "crosses": {f"{lv}_{d}": {h: [] for h in HORIZONS + ["rel21", "rel63", "rel126", "rel252"]}
                    for lv in CROSS_LEVELS for d in ("up", "down")},
        "zone_fwd": {f"z{k}_{d}": {h: [] for h in HORIZONS} for k in range(5) for d in ("up", "down")},
        "zone_fwd_rel": {f"z{k}_{d}": {h: [] for h in HORIZONS} for k in range(5) for d in ("up", "down")},
        "leadlag": {},                     # lag -> corr samples accumulated
        "range_stats": [],                 # per-symbol min/max/span/traversals
    }
    leadlag_num, leadlag_den = defaultdict(float), defaultdict(int)
    lags = list(range(-126, 127, 21))

    for si, (sym, df) in enumerate(sorted(series.items())):
        ml = df["ml"].to_numpy()
        px = df["px"].to_numpy()
        bench = df["bench"].to_numpy()
        n = len(ml)

        # A. velocity
        d1 = np.diff(ml)
        res["daily_step"]["abs_p50"].append(float(np.median(np.abs(d1))))
        res["daily_step"]["abs_p99"].append(float(np.percentile(np.abs(d1), 99)))
        res["daily_step"]["abs_max"].append(float(np.max(np.abs(d1))))
        v21 = ml[SLOPE_WIN:] - ml[:-SLOPE_WIN]
        res["vel21"].extend(np.round(v21[::10], 3).tolist())

        # B. occupancy
        idx = np.clip((ml // 10).astype(int), 0, 9)
        for z in range(10):
            res["occupancy_days"][z] += int(np.sum(idx == z))

        # per-symbol range / traversals (full 20->80 or 80->20 trips)
        res["range_stats"].append({
            "sym": sym, "min": float(ml.min()), "max": float(ml.max()),
            "span": float(ml.max() - ml.min()),
            "trips_up": _count_trips(ml, 20, 80), "trips_down": _count_trips(-ml, -80, -20),
        })

        # C. phases from 21d slope sign, flickers collapsed
        sign = np.sign(np.concatenate([[0] * SLOPE_WIN, v21]))
        sign = _collapse_runs(sign, MIN_PHASE_DAYS)
        for a, b, s in _runs(sign):
            if s == 0 or b - a < MIN_PHASE_DAYS:
                continue
            amp = float(ml[b - 1] - ml[a])
            pret = float(px[b - 1] / px[a] - 1.0)
            bret = float(bench[b - 1] / bench[a] - 1.0)
            res["phases"].append({
                "dir": "up" if s > 0 else "down", "days": int(b - a),
                "amp": round(amp, 2), "start_ml": round(float(ml[a]), 1),
                "px_ret": round(pret, 4), "rel_ret": round(pret - bret, 4),
            })

        # D/E. zone-conditioned forward returns + level crosses
        for i in range(SLOPE_WIN, n - 21):
            direction = "up" if ml[i] > ml[i - SLOPE_WIN] else "down"
            zk = zone5(ml[i])
            if i % 5 == 0:  # subsample to decorrelate
                for h in HORIZONS:
                    r = fwd_ret(px, i, h)
                    if r is not None:
                        res["zone_fwd"][f"z{zk}_{direction}"][h].append(r)
                        rb = fwd_ret(bench, i, h)
                        if rb is not None:
                            res["zone_fwd_rel"][f"z{zk}_{direction}"][h].append(r - rb)
            for lv in CROSS_LEVELS:
                if ml[i - 1] < lv <= ml[i]:
                    key = f"{lv}_up"
                elif ml[i - 1] > lv >= ml[i]:
                    key = f"{lv}_down"
                else:
                    continue
                for h in HORIZONS:
                    r = fwd_ret(px, i, h)
                    rb = fwd_ret(bench, i, h)
                    if r is not None:
                        res["crosses"][key][h].append(r)
                        if rb is not None:
                            res["crosses"][key][f"rel{h}"].append(r - rb)

        # F. lead/lag: corr(Δ21 ml at t, 21d rel return at t+lag)
        rel = (px[21:] / px[:-21]) - (bench[21:] / bench[:-21])  # 21d rel return, index t = start
        dml = v21  # Δml over [t, t+21], index t = start
        m = min(len(rel), len(dml))
        rel, dml = rel[:m], dml[:m]
        for lag in lags:
            if lag >= 0:
                a_, b_ = dml[:m - lag] if lag else dml, rel[lag:] if lag else rel
            else:
                a_, b_ = dml[-lag:], rel[:m + lag]
            if len(a_) > 50 and np.std(a_) > 0 and np.std(b_) > 0:
                leadlag_num[lag] += float(np.corrcoef(a_, b_)[0, 1])
                leadlag_den[lag] += 1

        if (si + 1) % 200 == 0:
            print(f"  {si + 1}/{len(series)} symbols", flush=True)

    res["leadlag"] = {str(l): round(leadlag_num[l] / leadlag_den[l], 4)
                      for l in lags if leadlag_den[l]}
    res["occupancy_days"] = res["occupancy_days"].tolist()
    return res


def _count_trips(ml, lo, hi):
    trips, armed = 0, False
    for v in ml:
        if v <= lo:
            armed = True
        elif v >= hi and armed:
            trips += 1
            armed = False
    return trips


def _runs(sign):
    out, a = [], 0
    for i in range(1, len(sign) + 1):
        if i == len(sign) or sign[i] != sign[a]:
            out.append((a, i, sign[a]))
            a = i
    return out


def _collapse_runs(sign, min_len):
    sign = sign.copy()
    runs = _runs(sign)
    for a, b, s in runs:
        if b - a < min_len and a > 0:
            sign[a:b] = sign[a - 1]
    return sign


def summarize(res: dict) -> dict:
    def q(v, p):
        return round(float(np.percentile(v, p)), 4) if len(v) else None

    phases = res["phases"]
    ph_up = [p for p in phases if p["dir"] == "up"]
    ph_dn = [p for p in phases if p["dir"] == "down"]

    def phase_stats(ph):
        if not ph:
            return {}
        return {"n": len(ph),
                "days_p50": q([p["days"] for p in ph], 50),
                "days_p90": q([p["days"] for p in ph], 90),
                "amp_p50": q([p["amp"] for p in ph], 50),
                "amp_p90": q([p["amp"] for p in ph], 90),
                "px_ret_p50": q([p["px_ret"] for p in ph], 50),
                "rel_ret_p50": q([p["rel_ret"] for p in ph], 50),
                "rel_ret_pos_share": round(float(np.mean([p["rel_ret"] > 0 for p in ph])), 3)}

    cross_summary = {}
    for key, hs in res["crosses"].items():
        row = {}
        for h in HORIZONS:
            v = hs[h]
            if v:
                row[f"abs{h}_p50"] = q(v, 50)
                row[f"abs{h}_hit"] = round(float(np.mean(np.array(v) > 0)), 3)
            vr = hs.get(f"rel{h}", [])
            if vr:
                row[f"rel{h}_p50"] = q(vr, 50)
                row[f"rel{h}_hit"] = round(float(np.mean(np.array(vr) > 0)), 3)
        row["n"] = len(hs[21])
        cross_summary[key] = row

    zone_summary = {}
    for key in res["zone_fwd"]:
        row = {"n": len(res["zone_fwd"][key][21])}
        for h in HORIZONS:
            row[f"abs{h}_p50"] = q(res["zone_fwd"][key][h], 50)
            row[f"rel{h}_p50"] = q(res["zone_fwd_rel"][key][h], 50)
        zone_summary[key] = row

    spans = [r["span"] for r in res["range_stats"]]
    trips = [r["trips_up"] for r in res["range_stats"]]
    occ = np.array(res["occupancy_days"]); occ_pct = (occ / occ.sum() * 100).round(2)

    def hist(vals, edges):
        counts, _ = np.histogram(vals, bins=edges)
        return [{"bin": f"{edges[i]}-{edges[i+1]}", "lo": float(edges[i]), "n": int(counts[i])}
                for i in range(len(counts))]

    histograms = {
        "phase_days_up": hist([p["days"] for p in ph_up], [10, 21, 42, 63, 126, 189, 252, 378, 2000]),
        "phase_days_down": hist([p["days"] for p in ph_dn], [10, 21, 42, 63, 126, 189, 252, 378, 2000]),
        "phase_amp_up": hist([p["amp"] for p in ph_up], [0, 2, 5, 10, 20, 30, 45, 100]),
        "phase_amp_down": hist([-p["amp"] for p in ph_dn], [0, 2, 5, 10, 20, 30, 45, 100]),
        "vel21": hist(res["vel21"], [-15, -10, -6, -3, -1, 1, 3, 6, 10, 15]),
        "span": hist(spans, [0, 20, 30, 40, 50, 60, 70, 80, 90, 100]),
        "trips": hist(trips, [0, 1, 2, 3, 4, 10]),
    }

    return {
        "histograms": histograms,
        "n_symbols": res["n_symbols"],
        "velocity": {
            "theoretical_max_per_day": round(100 / 222, 3),
            "daily_abs_p50_median_across_stocks": q(res["daily_step"]["abs_p50"], 50),
            "daily_abs_p99_median_across_stocks": q(res["daily_step"]["abs_p99"], 50),
            "daily_abs_max_p99_across_stocks": q(res["daily_step"]["abs_max"], 99),
            "vel21_p05": q(res["vel21"], 5), "vel21_p50": q(res["vel21"], 50),
            "vel21_p95": q(res["vel21"], 95),
        },
        "occupancy_pct_by_decile": {f"{z*10}-{z*10+10}": float(occ_pct[z]) for z in range(10)},
        "phase_up": phase_stats(ph_up),
        "phase_down": phase_stats(ph_dn),
        "crosses": cross_summary,
        "zone_forward": zone_summary,
        "leadlag_corr": res["leadlag"],
        "range": {"span_p50": q(spans, 50), "span_p90": q(spans, 90),
                  "full_trips_20to80_mean": round(float(np.mean(trips)), 2),
                  "stocks_with_zero_trips_pct": round(float(np.mean(np.array(trips) == 0)) * 100, 1)},
    }


def main():
    db = MongoClient(settings.MONGO_URI)[settings.DB_NAME]
    series = load_series(db)
    res = analyze(series)
    summary = summarize(res)
    detail = {"summary": summary,
              "phases_sample": res["phases"][:5000],
              "range_stats": res["range_stats"]}
    OUT.write_text(json.dumps(detail, indent=1), encoding="utf-8")

    # Persist for the dashboard: computed ONCE here, served read-only by
    # GET /api/analytics/research/meanlong-shape. Re-run this script after a
    # data refresh to update the study.
    import datetime as _dt
    db.research_results.update_one(
        {"_id": "meanlong_shape"},
        {"$set": {"study": "meanlong_shape",
                  "generated_at": _dt.datetime.now().isoformat(),
                  "window": "2018-01-01..latest-indicator-date",
                  "summary": summary}},
        upsert=True)
    print("stored -> research_results/_id=meanlong_shape")
    print(json.dumps(summary, indent=2))
    print(f"\nwritten -> {OUT}")


if __name__ == "__main__":
    main()
