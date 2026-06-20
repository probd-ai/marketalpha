from datetime import date, datetime
from typing import Mapping
import pandas as pd
from pymongo import MongoClient

INDEX_SYMBOL_MAP: dict[str, str] = {"NIFTY": "Nifty 50"}

_COL_MAP = {
    "open_price": "open",
    "high_price": "high",
    "low_price": "low",
    "close_price": "close",
}
_OUT_COLS = ["open", "high", "low", "close", "volume"]


class MongoDataSource:
    """Read-only DataSource over market-hunt's MongoDB price store."""

    def __init__(self, uri: str, db_name: str,
                 index_symbol_map: Mapping[str, str] | None = None,
                 client: MongoClient | None = None) -> None:
        self._client = client or MongoClient(uri)
        self._db = self._client[db_name]
        self._index_map = dict(index_symbol_map or {})

    def _price_collections(self) -> list[str]:
        return sorted(c for c in self._db.list_collection_names()
                      if c.startswith("prices_"))

    def get_ohlc(self, symbol: str, exchange: str, interval: str,
                 start: date, end: date) -> pd.DataFrame:
        resolved = self._index_map.get(symbol, symbol)
        start_dt = datetime(start.year, start.month, start.day)
        end_dt = datetime(end.year, end.month, end.day, 23, 59, 59)
        rows: list[dict] = []
        for coll in self._price_collections():
            rows.extend(self._db[coll].find(
                {"symbol": resolved, "date": {"$gte": start_dt, "$lte": end_dt}}
            ))
        if not rows:
            return pd.DataFrame(columns=_OUT_COLS)
        df = pd.DataFrame(rows).rename(columns=_COL_MAP)
        # market-hunt stores bars as UTC datetimes with an intraday time (e.g. 09:15);
        # pymongo returns them tz-naive. Normalize to a clean tz-naive daily index.
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None).dt.normalize()
        df = df.set_index("date").sort_index()
        return df[_OUT_COLS]
