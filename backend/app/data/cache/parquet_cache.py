import re
from datetime import date
from pathlib import Path
import pandas as pd


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)


class ParquetCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{_safe(key)}.parquet"

    def get(self, key: str) -> pd.DataFrame | None:
        p = self._path(key)
        if not p.exists():
            return None
        return pd.read_parquet(p)

    def put(self, key: str, df: pd.DataFrame) -> None:
        df.to_parquet(self._path(key))


class CachedDataSource:
    def __init__(self, source: object, cache: ParquetCache) -> None:
        self._source = source
        self._cache = cache

    def get_ohlc(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        key = f"{exchange}_{symbol}_{interval}"
        df = self._cache.get(key)
        if df is None:
            df = self._source.get_ohlc(symbol, exchange, interval, start, end)
            if not df.empty:
                self._cache.put(key, df)
        if df.empty:
            return df
        mask = (df.index.date >= start) & (df.index.date <= end)
        return df.loc[mask]
