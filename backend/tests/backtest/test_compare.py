from datetime import date
import numpy as np
import pandas as pd
from app.strategies.rs_momentum import RSMomentumStrategy
from app.data.universe.provider import IndexMembershipProvider, BiasedUniverseProvider
from app.data.universe.membership import MembershipInterval
from app.backtest.compare import run_comparison, ComparisonResult
from app.strategies.base import BacktestConfig


def _series(idx, start, slope):
    return pd.Series(start + slope * np.arange(len(idx)), index=idx)


class DictSource:
    def __init__(self, panel: pd.DataFrame, nifty: pd.Series):
        self._panel, self._nifty = panel, nifty

    def get_ohlc(self, symbol, exchange, interval, start, end):
        if symbol == "NIFTY":
            s = self._nifty
        elif symbol in self._panel.columns:
            s = self._panel[symbol]
        else:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return pd.DataFrame({"open": s, "high": s, "low": s, "close": s, "volume": 1.0})


def test_comparison_excludes_future_member_in_pit_run():
    idx = pd.bdate_range("2021-01-01", "2021-12-31")
    # STEADY rises modestly; LATE rises fastest (a momentum magnet) but joins late.
    panel = pd.DataFrame({
        "STEADY": _series(idx, 100, 0.1),
        "OLD": _series(idx, 100, 0.05),
        "LATE": _series(idx, 100, 1.0),
    }, index=idx)
    nifty = _series(idx, 100, 0.02)
    src = DictSource(panel, nifty)

    current = {"STEADY", "OLD", "LATE"}
    pit = IndexMembershipProvider({"NIFTY50": [
        MembershipInterval("STEADY", None, None),
        MembershipInterval("OLD", None, None),
        MembershipInterval("LATE", date(2021, 11, 1), None),  # added late
    ]})
    biased = BiasedUniverseProvider({"NIFTY50": current})
    cfg = BacktestConfig(index="NIFTY50", start=date(2021, 1, 1), end=date(2021, 12, 31),
                         top_pct=1.0, top_n=1)

    res = run_comparison(src, pit, biased, RSMomentumStrategy(), cfg)
    assert isinstance(res, ComparisonResult)
    # Biased picks LATE from the first rebalance; PIT cannot until Nov.
    biased_early = res.biased.holdings[0][1]
    pit_early = res.point_in_time.holdings[0][1]
    assert "LATE" in biased_early
    assert "LATE" not in pit_early
    assert res.coverage.total == 3 and res.coverage.missing == []
    assert set(res.delta) == {"biased_final", "pit_final", "final_abs", "final_pct"}
