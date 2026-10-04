"""Build engine inputs (fund.engine.market.Market) from the market-hunt database.

The only module of the fund package that reads market data. Loading matches the validated research loaders
(scripts/research_grid/grid_meanrev.load_data, alpha_test.load_means) so the engine sees identical frames.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from brokerage_calculator import BrokerageCalculator
from membership import load_membership, members_as_of, union_over

from .engine.market import Market
from .engine.signals import alpha_eligible, grid_state, roc

BENCH = "Nifty 50"
WARMUP_DAYS = 122          # research loaded from 2020-06-01 for a 2020-10-01 start: 22-bar returns + grid history
TRUEVX_FIELDS = ("truevx_score", "mean_short", "mean_mid", "mean_long")


def charge_rates() -> tuple[float, float]:
    """Statutory charges per rupee on a buy and a sell (zero brokerage), from the platform calculator."""
    bc = BrokerageCalculator()
    v = 1_000_000.0
    return (bc.calculate_transaction_charges(v, "BUY").total_charges / v,
            bc.calculate_transaction_charges(v, "SELL").total_charges / v)


def load_from(model_start: date) -> datetime:
    return datetime.combine(model_start - timedelta(days=WARMUP_DAYS), datetime.min.time())


def _last_year(collection: str) -> int:
    """prices_2020_2024 -> 2024, prices_2025 -> 2025 (collections are split by year ranges)."""
    return int(collection.split("_")[-1])


def load_prices(db, symbols: list[str], since: datetime, want_value: bool = False):
    cols = [c for c in db.list_collection_names() if c.startswith("prices_") and _last_year(c) >= since.year]
    rows = []
    for c in cols:
        rows += list(db[c].find({"symbol": {"$in": symbols + [BENCH]}, "date": {"$gte": since}},
                                {"_id": 0, "symbol": 1, "date": 1, "open_price": 1, "close_price": 1, "value": 1}))
    px = pd.DataFrame(rows)
    px["date"] = pd.to_datetime(px["date"]).dt.normalize()
    px = px.drop_duplicates(["symbol", "date"])
    opens = px.pivot(index="date", columns="symbol", values="open_price").sort_index()
    closes = px.pivot(index="date", columns="symbol", values="close_price").sort_index()
    if want_value:
        return opens, closes, px.pivot(index="date", columns="symbol", values="value").sort_index()
    return opens, closes


def market_holidays(db) -> set:
    """NSE equity trading holidays stored by the refresh's calendar stage (empty if never loaded)."""
    from market_calendar import load_holidays
    return load_holidays(db)


def latest_closes(db, symbols: list[str]) -> dict[str, float]:
    """Most recent close per symbol (newest year collection first; a symbol absent everywhere is left out)."""
    out: dict[str, float] = {}
    cols = sorted((c for c in db.list_collection_names() if c.startswith("prices_")), key=_last_year, reverse=True)
    for c in cols:
        todo = [s for s in symbols if s not in out]
        if not todo:
            break
        for d in db[c].aggregate([{"$match": {"symbol": {"$in": todo}}}, {"$sort": {"date": -1}},
                                  {"$group": {"_id": "$symbol", "close": {"$first": "$close_price"}}}]):
            if d["close"]:
                out[d["_id"]] = float(d["close"])
    return out


def traded_range(db, symbols: list[str], since_iso: str | None) -> dict[str, tuple[float, float]]:
    """(lowest low, highest high) per symbol from `since_iso` (a fill date) to the latest bar; symbols without bars
    in that window are left out."""
    if not since_iso or not symbols:
        return {}
    since = datetime.fromisoformat(str(since_iso)[:10])
    lo: dict[str, float] = {}
    hi: dict[str, float] = {}
    for c in (c for c in db.list_collection_names() if c.startswith("prices_") and _last_year(c) >= since.year):
        for d in db[c].aggregate([{"$match": {"symbol": {"$in": symbols}, "date": {"$gte": since}}},
                                  {"$group": {"_id": "$symbol", "lo": {"$min": "$low_price"},
                                              "hi": {"$max": "$high_price"}}}]):
            if d["lo"] and d["hi"]:
                lo[d["_id"]] = min(lo.get(d["_id"], d["lo"]), d["lo"])
                hi[d["_id"]] = max(hi.get(d["_id"], d["hi"]), d["hi"])
    return {s: (float(lo[s]), float(hi[s])) for s in lo}


def load_truevx(db, symbols: list[str], since: datetime, fields=TRUEVX_FIELDS) -> dict[str, pd.DataFrame]:
    proj = {"_id": 0, "symbol": 1, "date": 1, **{f"data.{f}": 1 for f in fields}}
    docs = db.indicators.find({"symbol": {"$in": symbols}, "indicator_type": "truevx", "base_symbol": BENCH,
                               "date": {"$gte": since}}, proj)
    df = pd.DataFrame([{"symbol": d["symbol"], "date": d["date"], **(d.get("data") or {})} for d in docs])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.drop_duplicates(["symbol", "date"])
    return {f: df.pivot(index="date", columns="symbol", values=f).sort_index() for f in fields}


def build_market(db, universe: str, kind: str, model_start: date, today: date | None = None) -> Market:
    """kind: 'grid10' (needs mean_long grid) | 'tranche' (needs Alpha eligibility)."""
    today = today or date.today()
    iv = load_membership(db, universe, today)
    ever = sorted(union_over(iv, model_start, today))
    since = load_from(model_start)
    opens, closes, value = load_prices(db, ever, since, want_value=True)
    cols = [c for c in closes.columns if c != BENCH]
    r = roc(closes[cols])
    members = lambda d: members_as_of(iv, d)
    fields = ("mean_long",) if kind == "grid10" else TRUEVX_FIELDS
    if kind not in ("grid10", "tranche"):
        raise ValueError(f"unknown sleeve kind {kind!r}")
    f = load_truevx(db, ever, since, fields)
    last = pd.concat([f[k].apply(lambda c: c.last_valid_index()) for k in fields], axis=1).min(axis=1)
    common = dict(traded_value=value.reindex(columns=cols), indicator_last=last)
    if kind == "grid10":
        ml = f["mean_long"]
        return Market(opens, closes, BENCH, members, r,
                      grid=grid_state(ml.reindex(index=closes.index, columns=cols)), **common)
    return Market(opens, closes, BENCH, members, r, elig=alpha_eligible(f, closes.index, cols), **common)


def build_asset_market(db, symbol: str, model_start: date) -> Market:
    opens, closes, value = load_prices(db, [symbol], load_from(model_start), want_value=True)
    return Market(opens, closes, BENCH, lambda d: {symbol}, pd.DataFrame(index=closes.index),
                  traded_value=value.reindex(columns=[symbol]))
