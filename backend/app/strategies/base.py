from datetime import date
from typing import Protocol
import pandas as pd
from pydantic import BaseModel


class BacktestConfig(BaseModel):
    index: str = "NIFTY50"
    start: date
    end: date
    starting_capital: float = 1_000_000
    lookback: int = 22
    top_pct: float = 0.5
    top_n: int = 10
    nominal_size: int = 50
    cost_bps: float = 0.0


class Strategy(Protocol):
    def select(self, close_panel: pd.DataFrame, nifty: pd.Series,
               members: set[str], as_of: date, cfg: BacktestConfig) -> dict[str, float]: ...
