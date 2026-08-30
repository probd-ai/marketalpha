"""RSZ endpoints.

RSZ is NOT a ranker. Tested across 45 backtests it wins no universe consistently and
does not beat a plain price return, so nothing here feeds the strategy or simulation
surfaces. What it does measure well is VOLATILITY among names that are already
strong, which is the one thing these endpoints serve: given the stocks whose 22/66/222
day relative strength all agree, split them into the smooth compounders and the jumpy
ones, and describe the jumpy ones well enough to trade their swings.

Everything is read-only over the precomputed `rsz` collection. Recompute with:
    python scripts/compute_rsz.py
    python scripts/validate_rsz.py && python scripts/rsz_degeneracy.py
"""
import logging
import statistics
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.db import mongo_conn
from rsz_compute import COLLECTION
from rsz_engine import BENCHMARK, SEGMENT_PRIORITY, RszParams

logger = logging.getLogger(__name__)
router = APIRouter()

_PARAMS = RszParams()
_PARAM_HASH = _PARAMS.hash
_LOOKBACK_SESSIONS = 260          # ~1 trading year of closes for the swing profile
_DIP_HIGH_WINDOW = 20             # "recent high" the dip is measured from
_DIP_THRESHOLD = 5.0              # a pullback worth acting on, percent
_RECOVERY_WINDOW = 20             # sessions allowed to regain the prior high


def _db():
    if mongo_conn.db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    return mongo_conn.db


def _latest_date(db):
    doc = db[COLLECTION].find_one({"param_hash": _PARAM_HASH}, sort=[("date", -1)])
    return doc["date"] if doc else None


def _closes(db, symbols: list[str], since: datetime) -> dict[str, list[tuple[str, float, float, float]]]:
    """symbol -> ascending [(date, close, high, low)]."""
    out: dict[str, list] = {}
    for coll in [c for c in db.list_collection_names() if c.startswith("prices_")]:
        for d in db[coll].find(
                {"symbol": {"$in": symbols}, "date": {"$gte": since}},
                {"symbol": 1, "date": 1, "close_price": 1, "high_price": 1, "low_price": 1}):
            c = d.get("close_price")
            if not c:
                continue
            out.setdefault(d["symbol"], []).append((
                d["date"].strftime("%Y-%m-%d"), float(c),
                float(d.get("high_price") or c), float(d.get("low_price") or c)))
    for s in out:
        out[s].sort(key=lambda r: r[0])
    return out


def _pct(a: float, b: float):
    return round((a / b - 1) * 100, 2) if b else None


def _swing_profile(bars: list[tuple[str, float, float, float]]) -> dict:
    """Describe how this name actually moves, for someone trading its swings.

    Everything is measured on closes and the trailing 20-session high, i.e. what a
    trader could have seen at the time. `dips_per_year` / `median_dip_pct` say how
    often a buyable pullback shows up and how deep it usually goes; `recovery_rate`
    says how often that pullback was in fact bought back within a month.
    """
    n = len(bars)
    if n < _DIP_HIGH_WINDOW + _RECOVERY_WINDOW + 5:
        return {}
    closes = [b[1] for b in bars]
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]

    daily_range = [
        (highs[i] - lows[i]) / closes[i] * 100
        for i in range(n) if closes[i]
    ]

    depths, recovered, episodes = [], 0, 0
    in_dip = False
    for i in range(_DIP_HIGH_WINDOW, n - _RECOVERY_WINDOW):
        prior_high = max(highs[i - _DIP_HIGH_WINDOW:i])
        if not prior_high:
            continue
        drop = (prior_high / closes[i] - 1) * 100
        if drop >= _DIP_THRESHOLD:
            if not in_dip:                      # count each pullback once
                episodes += 1
                in_dip = True
                depths.append(drop)
                if max(highs[i + 1:i + 1 + _RECOVERY_WINDOW]) >= prior_high:
                    recovered += 1
        else:
            in_dip = False

    years = n / 252.0
    return {
        "avg_daily_range_pct": round(sum(daily_range) / len(daily_range), 2) if daily_range else None,
        "dips_per_year": round(episodes / years, 1) if years else None,
        "median_dip_pct": round(statistics.median(depths), 2) if depths else None,
        "max_dip_pct": round(max(depths), 2) if depths else None,
        "recovery_rate_pct": round(recovered / episodes * 100, 1) if episodes else None,
        "dip_episodes": episodes,
    }


@router.get("/api/rsz/coverage")
async def rsz_coverage():
    """What exists, so the UI never renders an empty page without saying why."""
    db = _db()
    coll = db[COLLECTION]
    latest = _latest_date(db)
    if latest is None:
        return {"success": True, "ready": False, "param_hash": _PARAM_HASH,
                "message": "RSZ has not been computed yet — run scripts/compute_rsz.py"}
    first = coll.find_one({"param_hash": _PARAM_HASH}, sort=[("date", 1)])
    segs = []
    for seg in SEGMENT_PRIORITY:
        n = coll.count_documents({"param_hash": _PARAM_HASH, "date": latest, "segment": seg})
        if n:
            segs.append({"segment": seg, "names": n})
    return {
        "success": True, "ready": True, "param_hash": _PARAM_HASH,
        "benchmark": BENCHMARK,
        "rows": coll.count_documents({"param_hash": _PARAM_HASH}),
        "symbols": len(coll.distinct("symbol", {"param_hash": _PARAM_HASH})),
        "first_date": first["date"].strftime("%Y-%m-%d"),
        "latest_date": latest.strftime("%Y-%m-%d"),
        "segments": segs,
        "params": {"horizons": list(_PARAMS.horizons), "weights": list(_PARAMS.weights),
                   "aligned_rank_floor": _PARAMS.aligned_rank_floor,
                   "vol_window": _PARAMS.vol_window},
    }


@router.get("/api/rsz/swing")
async def rsz_swing(
    segment: str | None = None,
    min_aligned: int = Query(3, ge=1, le=3),
    limit: int = Query(10, ge=2, le=40),
    date: str | None = None,
):
    """Strong names split into SMOOTH and VOLATILE, with returns and a swing profile.

    Selection: names whose relative strength agrees on at least `min_aligned` of the
    three horizons, taken in RSZ order, capped at `limit`. The smooth/volatile cut is
    the MEDIAN volatility of that selected set, so the split adapts to the universe —
    a "volatile" largecap and a "volatile" microcap are each volatile for their peers.
    """
    db = _db()
    if date:
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    else:
        dt = _latest_date(db)
        if dt is None:
            raise HTTPException(status_code=404, detail="RSZ not computed yet")

    q = {"param_hash": _PARAM_HASH, "date": dt, "aligned": {"$gte": min_aligned},
         "vol_pct": {"$ne": None}}
    if segment:
        q["segment"] = segment
    qualifying = list(db[COLLECTION].find(q, {"_id": 0}).sort("rsz_score", -1))
    if not qualifying:
        return {"success": True, "date": dt.strftime("%Y-%m-%d"), "qualifying": 0,
                "cut_vol_pct": None, "smooth": [], "volatile": [],
                "message": f"no names met aligned >= {min_aligned}"
                           + (f" in {segment}" if segment else "")}

    picked = qualifying[:limit]
    vols = sorted(r["vol_pct"] for r in picked)
    cut = statistics.median(vols)

    syms = [r["symbol"] for r in picked]
    since = dt - timedelta(days=int(_LOOKBACK_SESSIONS * 1.6))
    bars = _closes(db, syms, since)

    smooth, volatile = [], []
    for r in picked:
        b = [x for x in bars.get(r["symbol"], []) if x[0] <= dt.strftime("%Y-%m-%d")]
        last = b[-1][1] if b else None
        row = {
            "symbol": r["symbol"], "segment": r["segment"],
            "rsz_score": round(r["rsz_score"], 1), "aligned": r["aligned"],
            "vol_pct": round(r["vol_pct"], 1),
            "z_comp": round(r.get("z_comp", 0), 2),
            "r_short": round(r.get("r_short", 0), 0),
            "r_mid": round(r.get("r_mid", 0), 0),
            "r_long": round(r.get("r_long", 0), 0),
            "price": round(last, 2) if last else None,
            "ret_1m": _pct(last, b[-22][1]) if last and len(b) > 22 else None,
            "ret_3m": _pct(last, b[-66][1]) if last and len(b) > 66 else None,
            "ret_12m": _pct(last, b[-252][1]) if last and len(b) > 252 else None,
            **_swing_profile(b),
        }
        (volatile if r["vol_pct"] > cut else smooth).append(row)

    smooth.sort(key=lambda x: x["vol_pct"])
    volatile.sort(key=lambda x: -x["vol_pct"])
    return {
        "success": True, "date": dt.strftime("%Y-%m-%d"), "benchmark": BENCHMARK,
        "segment": segment, "min_aligned": min_aligned,
        "qualifying": len(qualifying), "shown": len(picked),
        "cut_vol_pct": round(cut, 1),
        "dip_threshold_pct": _DIP_THRESHOLD, "recovery_window": _RECOVERY_WINDOW,
        "smooth": smooth, "volatile": volatile,
    }


@router.get("/api/rsz/symbol/{symbol}")
async def rsz_symbol(symbol: str, days: int = Query(400, ge=30, le=2000)):
    """One name's RSZ + volatility history, for the drill-down chart."""
    db = _db()
    latest = _latest_date(db)
    if latest is None:
        raise HTTPException(status_code=404, detail="RSZ not computed yet")
    since = latest - timedelta(days=int(days * 1.5))
    rows = list(db[COLLECTION].find(
        {"param_hash": _PARAM_HASH, "symbol": symbol.upper(), "date": {"$gte": since}},
        {"_id": 0, "param_hash": 0, "computed_at": 0}).sort("date", 1))
    if not rows:
        raise HTTPException(status_code=404, detail=f"no RSZ history for {symbol}")
    for r in rows:
        r["date"] = r["date"].strftime("%Y-%m-%d")
    px = _closes(db, [symbol.upper()], since).get(symbol.upper(), [])
    pmap = {d: c for d, c, _h, _l in px}
    base = None
    for r in rows:
        c = pmap.get(r["date"])
        if c and base is None:
            base = c
        r["price_rebased"] = round(c / base * 100, 2) if c and base else None
    return {"success": True, "symbol": symbol.upper(), "count": len(rows),
            "segment": rows[-1]["segment"], "benchmark": BENCHMARK,
            "rows": rows[-days:]}


@router.get("/api/rsz/validation")
async def rsz_validation():
    """Precomputed head-to-head study (scripts/validate_rsz.py + rsz_degeneracy.py)."""
    db = _db()
    doc = db.research_results.find_one({"_id": "rsz_validation"})
    if not doc:
        raise HTTPException(
            status_code=404,
            detail="validation study not found — run scripts/validate_rsz.py")
    doc.pop("_id", None)
    return {"success": True, "study": doc}
