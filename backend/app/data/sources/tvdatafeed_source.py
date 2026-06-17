from datetime import date
import pandas as pd

_COLUMNS = ["open", "high", "low", "close", "volume"]


class TvDatafeedSource:
    """DataSource backed by the rongardF tvdatafeed fork."""

    def __init__(self, username=None, password=None, client=None):
        if client is not None:
            self._client = client
            self._use_fake = True
        else:
            from tvDatafeed import TvDatafeed
            self._client = TvDatafeed(username=username, password=password)
            self._use_fake = False

    def get_ohlc(self, symbol, exchange, interval, start, end):
        if self._use_fake:
            interval_val = interval  # pass through as-is for fake clients
        else:
            from tvDatafeed import Interval
            interval_map = {"daily": Interval.in_daily}
            if interval not in interval_map:
                raise ValueError(
                    f"Unsupported interval {interval!r}. Valid: {list(interval_map)}"
                )
            interval_val = interval_map[interval]

        n_bars = max((end - start).days * 2, 100)
        raw = self._client.get_hist(
            symbol=symbol, exchange=exchange,
            interval=interval_val, n_bars=n_bars,
        )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=_COLUMNS)
        df = raw.copy()
        df.columns = [c.lower() for c in df.columns]
        df = df[[c for c in _COLUMNS if c in df.columns]]
        idx = pd.to_datetime(df.index)
        df.index = idx.tz_convert(None) if idx.tz is not None else idx
        df = df.sort_index()
        mask = (df.index.date >= start) & (df.index.date <= end)
        return df.loc[mask]
