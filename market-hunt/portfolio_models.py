from typing import List, Optional
from pydantic import BaseModel


class Sleeve(BaseModel):
    id: str
    label: str
    strategy_id: str
    weight: float                       # capital % ; sleeve weights sum to 100
    universe: str
    rebalance_frequency: str = "monthly"   # monthly|weekly|quarterly|dynamic
    rebalance_date: str = "mid"            # first|last|mid
    rebalance_type: str = "equal_weight"   # equal_weight|skewed
    max_holdings: int = 25
    momentum_ranking: str = "20_day_return"
    include_brokerage: bool = True
    slippage_bps: float = 0.0              # per-side execution slippage (bps)
    regime_gate: bool = False              # index-level risk on/off gate
    gate_symbol: str = "Nifty 500"         # gate index (user-selectable)
    gate_sma: int = 200                    # SMA length when gate_mode == "sma"
    gate_mode: str = "sma"                 # sma | dynamic_fib
    gate_fib_lookback: int = 222           # Dynamic-Fib trailing range
    gate_fib_level: float = 0.66           # risk-on above LL + range*level
    stock_gate: bool = False               # per-stock fib eligibility gate
    portfolio_gate: bool = False           # self-gate on the equity curve
    exchange: str = "NSE"
    book_weekday: int = 0                  # book strategies only: weekly session, 0 = Mon .. 4 = Fri


class Portfolio(BaseModel):
    id: Optional[str] = None
    name: str
    description: str = ""
    total_capital: float = 100000
    benchmark_symbol: str = "Nifty 50"
    inception_date: str                  # YYYY-MM-DD ; floor 2022-01-01
    point_in_time_membership: bool = True
    sleeves: List[Sleeve]
    created_at: Optional[str] = None
    last_modified: Optional[str] = None
