from datetime import date
from typing import Protocol
import pandas as pd


class DataSource(Protocol):
    def get_ohlc(
        self, symbol: str, exchange: str, interval: str, start: date, end: date
    ) -> pd.DataFrame: ...
