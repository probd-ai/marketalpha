from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

Metric = Literal["mean_short", "mean_mid", "mean_long", "truevx_score"]

class CompareRHS(BaseModel):
    metric: Optional[Metric] = None
    number: Optional[float] = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.metric is None) == (self.number is None):
            raise ValueError("compare rhs must set exactly one of {metric, number}")
        return self

class Signal(BaseModel):
    kind: Literal["cross", "compare", "last_cross_dir"]
    negate: bool = False
    # cross / last_cross_dir
    metric: Optional[Metric] = None
    level: Optional[float] = None
    direction: Optional[Literal["up", "down"]] = None
    grid: Optional[List[float]] = None
    # compare
    lhs_metric: Optional[Metric] = None
    op: Optional[Literal[">", "<", ">=", "<=", "==", "!="]] = None
    rhs: Optional[CompareRHS] = None

    @model_validator(mode="after")
    def _shape_by_kind(self):
        if self.kind == "cross":
            if self.metric is None or self.level is None or self.direction is None:
                raise ValueError("cross needs metric, level, direction")
        elif self.kind == "last_cross_dir":
            if self.metric is None or not self.grid or self.direction is None:
                raise ValueError("last_cross_dir needs metric, grid, direction")
        elif self.kind == "compare":
            if self.lhs_metric is None or self.op is None or self.rhs is None:
                raise ValueError("compare needs lhs_metric, op, rhs")
        return self

SignalGroup = List[Signal]

class WaveConfig(BaseModel):
    capital_N: float = Field(gt=0)
    tranches: int
    reentry: bool = True
    buy_cadence: Literal["daily", "monthly", "weekly", "quarterly"] = "daily"
    buy_date: Literal["first", "mid", "last"] = "first"
    entry: List[SignalGroup]
    accumulate: List[SignalGroup] = []
    exit: List[SignalGroup]
    exit_mode: Literal["full", "fractional"] = "full"

    @field_validator("tranches")
    @classmethod
    def _k_positive(cls, v):
        if v < 1:
            raise ValueError("tranches must be >= 1")
        return v

    @model_validator(mode="after")
    def _cross_requires_daily(self):
        if self.buy_cadence != "daily":
            for grp in list(self.entry) + list(self.accumulate):
                if any(s.kind == "cross" for s in grp):
                    raise ValueError("cross signals in entry/accumulate require buy_cadence='daily'")
        return self
