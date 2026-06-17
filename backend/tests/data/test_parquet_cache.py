from datetime import date
import pandas as pd
from app.data.cache.parquet_cache import ParquetCache, CachedDataSource


class _CountingSource:
    def __init__(self):
        self.calls = 0

    def get_ohlc(self, symbol, exchange, interval, start, end):
        self.calls += 1
        idx = pd.bdate_range("2024-01-01", periods=10)
        return pd.DataFrame({"close": range(10)}, index=idx, dtype=float)


def test_cache_roundtrip(tmp_path):
    cache = ParquetCache(tmp_path)
    df = pd.DataFrame({"close": [1.0, 2.0]}, index=pd.bdate_range("2024-01-01", periods=2))
    cache.put("NSE_INFY_daily", df)
    out = cache.get("NSE_INFY_daily")
    assert out is not None and out["close"].tolist() == [1.0, 2.0]
    assert cache.get("missing") is None


def test_cached_source_fetches_once_then_serves_from_cache(tmp_path):
    src = _CountingSource()
    cached = CachedDataSource(src, ParquetCache(tmp_path))
    a = cached.get_ohlc("INFY", "NSE", "daily", date(2024, 1, 1), date(2024, 1, 5))
    b = cached.get_ohlc("INFY", "NSE", "daily", date(2024, 1, 2), date(2024, 1, 3))
    assert src.calls == 1                       # second call served from cache
    assert a.index.min().date() >= date(2024, 1, 1)
    assert b["close"].tolist() == [1.0, 2.0]    # sliced to requested window
