"""RSZ computation + storage.

Two implementations of the core statistic live in the codebase on purpose:
`rsz_engine.info_ratio` is the readable reference; `info_ratio_vec` here is the
numpy path that actually runs (the reference is O(T*n) per symbol per horizon —
~1.2B operations over the full universe). `tests/test_rsz_vectorized.py` pins them
to each other, so the fast path can never silently drift from the spec.

Point-in-time by construction: a symbol's SEGMENT is resolved per date from
`index_reconstitution` replay, so a name that was a smallcap in 2021 and a midcap
in 2025 is ranked against the right peers on each date. Because reconstitution
events begin in 2020, RSZ is only emitted from START_DATE onward; prices are loaded
from WARMUP_FROM so the 222-session window is already full on day one.

Storage is append-only: _id = "symbol|YYYYMMDD|param_hash". Nothing is ever deleted,
a crashed run cannot erase history, and two parameter sets coexist.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime

import numpy as np
from pymongo import ASCENDING, DESCENDING, UpdateOne

from membership import load_membership, members_as_of
from rsz_engine import (
    BENCHMARK, SEGMENT_BENCHMARK, SEGMENT_PRIORITY, RszParams, aligned_count,
    percentile_ranks,
)

logger = logging.getLogger(__name__)

COLLECTION = "rsz"
START_DATE = "2020-01-01"     # first date PIT segment membership is trustworthy
WARMUP_FROM = "2018-06-01"    # >= 222 sessions of run-up before START_DATE
# A stock that simply did not trade on an exchange day (halt, suspension, a bar the
# vendor missed) leaves one hole in the relative line. Because every window that
# contains a hole is undefined, ONE missing bar would otherwise blank 222 sessions
# of output for that name. Carry the last relative level across short gaps; leave
# anything longer as a genuine hole, and never backfill before a listing.
MAX_GAP_FILL = 5


def ffill_gaps(a: np.ndarray, limit: int = MAX_GAP_FILL) -> np.ndarray:
    """Carry the last finite value forward across runs of at most `limit` NaNs.

    Leading NaNs (pre-listing) are never filled: a stock has no relative level
    before it exists, and inventing one would fabricate history.
    """
    out = a.copy()
    last = np.nan
    run = 0
    for i in range(out.shape[0]):
        if np.isfinite(out[i]):
            last = out[i]
            run = 0
        else:
            run += 1
            if np.isfinite(last) and run <= limit:
                out[i] = last
    return out


def info_ratio_vec(rel: np.ndarray, n: int) -> np.ndarray:
    """Vectorized twin of rsz_engine.info_ratio. Same NaN semantics."""
    T = rel.shape[0]
    out = np.full(T, np.nan)
    if n < 2 or T <= n:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.where((rel > 0) & np.isfinite(rel), np.log(rel), np.nan)
    d = np.full(T, np.nan)
    d[1:] = lr[1:] - lr[:-1]

    # rolling mean / sample-variance of d over the trailing n points ending at t
    x = np.nan_to_num(d, nan=0.0)
    bad = (~np.isfinite(d)).astype(np.float64)
    cs = np.concatenate(([0.0], np.cumsum(x)))
    cs2 = np.concatenate(([0.0], np.cumsum(x * x)))
    csb = np.concatenate(([0.0], np.cumsum(bad)))
    t = np.arange(n, T)
    lo = t - n + 1
    s1 = cs[t + 1] - cs[lo]
    s2 = cs2[t + 1] - cs2[lo]
    nb = csb[t + 1] - csb[lo]
    mean = s1 / n
    var = (s2 - n * mean * mean) / (n - 1)
    var = np.where(var > 0, var, np.nan)
    sigma = np.sqrt(var)

    num = lr[t] - lr[t - n]
    with np.errstate(divide="ignore", invalid="ignore"):
        z = num / (sigma * math.sqrt(n))
    z = np.where(nb > 0, np.nan, z)                       # any NaN in window -> NaN
    z = np.where(np.isfinite(z), z, np.nan)
    out[n:] = z
    return out


def annualized_vol_vec(closes: np.ndarray, n: int = 66) -> np.ndarray:
    """Rolling annualized volatility (%) of the stock's own daily log returns.

    Deliberately computed on the RAW price, not the benchmark-relative line: a
    swing trade is executed against the stock's own high/low, so its own swing
    size is what matters. NaN until the window is full.
    """
    T = closes.shape[0]
    out = np.full(T, np.nan)
    if T <= n:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.where((closes > 0) & np.isfinite(closes), np.log(closes), np.nan)
    d = np.full(T, np.nan)
    d[1:] = lr[1:] - lr[:-1]
    x = np.nan_to_num(d, nan=0.0)
    bad = (~np.isfinite(d)).astype(np.float64)
    cs = np.concatenate(([0.0], np.cumsum(x)))
    cs2 = np.concatenate(([0.0], np.cumsum(x * x)))
    csb = np.concatenate(([0.0], np.cumsum(bad)))
    t = np.arange(n, T)
    lo = t - n + 1
    mean = (cs[t + 1] - cs[lo]) / n
    var = ((cs2[t + 1] - cs2[lo]) - n * mean * mean) / (n - 1)
    nb = csb[t + 1] - csb[lo]
    var = np.where((var > 0) & (nb == 0), var, np.nan)
    out[n:] = np.sqrt(var) * math.sqrt(252) * 100.0
    return out


def _segment_timeline(db, dates: list[str]) -> dict[str, dict[str, str]]:
    """date -> {symbol: segment}, resolved point-in-time, most-specific index wins."""
    anchor = date.today()
    intervals = {}
    for seg in SEGMENT_PRIORITY:
        try:
            intervals[seg] = load_membership(db, seg, anchor)
        except ValueError:
            logger.warning("no PIT membership for %s - segment skipped", seg)
    timeline: dict[str, dict[str, str]] = {}
    for ds in dates:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        assign: dict[str, str] = {}
        # iterate LOWEST priority first so the most specific index overwrites it
        for seg in reversed(SEGMENT_PRIORITY):
            if seg not in intervals:
                continue
            for sym in members_as_of(intervals[seg], d):
                assign[sym] = seg
        timeline[ds] = assign
    return timeline


def _load_closes(db, symbols: list[str], lo: str, hi: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    lo_dt = datetime.strptime(lo, "%Y-%m-%d")
    hi_dt = datetime.strptime(hi, "%Y-%m-%d")
    for coll in [c for c in db.list_collection_names() if c.startswith("prices_")]:
        cur = db[coll].find(
            {"symbol": {"$in": symbols}, "date": {"$gte": lo_dt, "$lte": hi_dt}},
            {"symbol": 1, "date": 1, "close_price": 1})
        for doc in cur:
            c = doc.get("close_price")
            if c:
                out.setdefault(doc["symbol"], {})[doc["date"].strftime("%Y-%m-%d")] = float(c)
    return out


def ensure_indexes(db) -> None:
    c = db[COLLECTION]
    c.create_index([("date", DESCENDING), ("segment", ASCENDING),
                    ("rsz_score", DESCENDING)], name="leaderboard")
    c.create_index([("symbol", ASCENDING), ("date", ASCENDING)], name="symbol_history")
    c.create_index([("param_hash", ASCENDING)], name="params")


def compute_rsz(db, params: RszParams | None = None, start: str = START_DATE,
                end: str | None = None, progress=None) -> dict:
    """Compute and store RSZ for every PIT member of the segment indices."""
    params = params or RszParams()
    end = end or datetime.now().strftime("%Y-%m-%d")
    say = progress or (lambda *_: None)
    ensure_indexes(db)

    anchor = date.today()
    symset: set[str] = set()
    for seg in SEGMENT_PRIORITY:
        try:
            iv = load_membership(db, seg, anchor)
        except ValueError:
            continue
        symset |= {i.symbol for i in iv}
    symbols = sorted(symset)
    benches = sorted(set(SEGMENT_BENCHMARK.values()))
    say(f"universe: {len(symbols)} symbols across {len(SEGMENT_PRIORITY)} segments")

    closes = _load_closes(db, symbols + benches, WARMUP_FROM, end)
    bench_closes = {b: closes.get(b, {}) for b in benches}
    say(f"prices loaded for {len(closes)} series")

    # Trading calendar: dates where the broadest benchmark traded.
    cal = sorted(bench_closes["Nifty 500"].keys())
    if not cal:
        raise RuntimeError("no Nifty 500 closes - cannot build a trading calendar")
    emit_from = max(start, WARMUP_FROM)
    emit_dates = [d for d in cal if emit_from <= d <= end]
    seg_tl = _segment_timeline(db, emit_dates)
    say(f"calendar {cal[0]}..{cal[-1]} - emitting {len(emit_dates)} dates")

    di = {d: i for i, d in enumerate(cal)}
    bench_arr = {b: np.array([bench_closes[b].get(d, np.nan) for d in cal]) for b in benches}

    # z per (symbol, segment) - a symbol changing segment gets a separate series,
    # because the benchmark it is measured against changes with it.
    zmap: dict[tuple[str, str], tuple] = {}
    volmap: dict[str, np.ndarray] = {}
    needed: dict[str, set[str]] = {}
    for ds, assign in seg_tl.items():
        for sym, seg in assign.items():
            needed.setdefault(sym, set()).add(seg)

    for k, sym in enumerate(symbols):
        if sym not in needed:
            continue
        s = closes.get(sym)
        if not s:
            continue
        arr = np.array([s.get(d, np.nan) for d in cal])
        volmap[sym] = annualized_vol_vec(ffill_gaps(arr), params.vol_window)
        for seg in needed[sym]:
            b = bench_arr[BENCHMARK]
            with np.errstate(divide="ignore", invalid="ignore"):
                rel = np.where((b > 0) & np.isfinite(b) & (arr > 0) & np.isfinite(arr),
                               arr / b, np.nan)
            rel = ffill_gaps(rel)
            zmap[(sym, seg)] = (info_ratio_vec(rel, params.horizons[0]),
                                info_ratio_vec(rel, params.horizons[1]),
                                info_ratio_vec(rel, params.horizons[2]))
        if k % 200 == 0:
            say(f"  z computed for {k}/{len(symbols)} symbols")

    ws, wm, wl = params.weights
    wtot = ws + wm + wl
    ops: list[UpdateOne] = []
    written = skipped_thin = 0
    now = datetime.now()

    for ds in emit_dates:
        i = di[ds]
        by_seg: dict[str, dict[str, tuple]] = {}
        for sym, seg in seg_tl[ds].items():
            zz = zmap.get((sym, seg))
            if zz is None:
                continue
            a, bb, c = zz[0][i], zz[1][i], zz[2][i]
            if not (np.isfinite(a) and np.isfinite(bb) and np.isfinite(c)):
                continue
            by_seg.setdefault(seg, {})[sym] = (float(a), float(bb), float(c))

        dt = datetime.strptime(ds, "%Y-%m-%d")
        # One global cross-section: every stock is measured against the same
        # benchmark, so the z values share a yardstick and can be pooled.
        if True:
            merged: dict[str, tuple] = {}
            seg_of: dict[str, str] = {}
            for seg, members in by_seg.items():
                for sym, v in members.items():
                    merged[sym] = v
                    seg_of[sym] = seg
            by_seg = {"__ALL__": merged}
        for seg, members in by_seg.items():
            if len(members) < params.min_universe_size:
                skipped_thin += len(members)
                continue
            rs = percentile_ranks({s: v[0] for s, v in members.items()})
            rm = percentile_ranks({s: v[1] for s, v in members.items()})
            rl = percentile_ranks({s: v[2] for s, v in members.items()})
            comp = {s: (ws * v[0] + wm * v[1] + wl * v[2]) / wtot
                    for s, v in members.items()}
            rc = percentile_ranks(comp)
            for sym, (a, bb, c) in members.items():
                ops.append(UpdateOne(
                    {"_id": sym + "|" + ds.replace("-", "") + "|" + params.hash},
                    {"$set": {
                        "symbol": sym, "date": dt,
                        "segment": seg_of.get(sym, seg),
                        "benchmark": BENCHMARK,
                        "param_hash": params.hash,
                        "z_short": a, "z_mid": bb, "z_long": c, "z_comp": comp[sym],
                        "r_short": rs[sym], "r_mid": rm[sym], "r_long": rl[sym],
                        "rsz_score": rc[sym],
                        "aligned": aligned_count(rs[sym], rm[sym], rl[sym],
                                                 params.aligned_rank_floor),
                        "vol_pct": (float(volmap[sym][i])
                                    if sym in volmap and np.isfinite(volmap[sym][i])
                                    else None),
                        "peers": len(members), "computed_at": now,
                    }}, upsert=True))
            written += len(members)
        if len(ops) >= 20000:
            db[COLLECTION].bulk_write(ops, ordered=False)
            ops = []
            say(f"  ...{written:,} rows written (through {ds})")
    if ops:
        db[COLLECTION].bulk_write(ops, ordered=False)

    return {"param_hash": params.hash, "symbols": len(symbols), "rows": written,
            "dates": len(emit_dates), "skipped_thin_segment": skipped_thin,
            "start": emit_dates[0] if emit_dates else None,
            "end": emit_dates[-1] if emit_dates else None}
