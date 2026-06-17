from datetime import date
import pandas as pd
from app.data.sources.tvdatafeed_source import TvDatafeedSource


class _FakeTv:
    def get_hist(self, symbol, exchange, interval, n_bars):
        idx = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
        return pd.DataFrame(
            {"symbol": symbol, "open": 1.0, "high": 2.0, "low": 0.5,
             "close": [10.0, 11.0, 12.0], "volume": 100},
            index=idx,
        )


def test_get_ohlc_normalizes_and_slices():
    src = TvDatafeedSource(client=_FakeTv())
    df = src.get_ohlc("INFY", "NSE", "daily", date(2024, 1, 2), date(2024, 1, 3))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.is_monotonic_increasing
    assert df.index.min().date() == date(2024, 1, 2)
    assert df["close"].tolist() == [11.0, 12.0]


def test_get_ohlc_invalid_interval_raises_on_fake_client():
    import pytest
    src = TvDatafeedSource(client=_FakeTv())
    with pytest.raises(ValueError):
        src.get_ohlc("INFY", "NSE", "weekly", date(2024, 1, 2), date(2024, 1, 3))
