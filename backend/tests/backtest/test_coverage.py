from datetime import date
import pandas as pd
from app.backtest.coverage import coverage_report, CoverageReport


class FakeSource:
    def __init__(self, available: set[str]):
        self._available = available

    def get_ohlc(self, symbol, exchange, interval, start, end):
        if symbol in self._available:
            return pd.DataFrame({"close": [1.0, 2.0]},
                                index=pd.to_datetime(["2021-01-01", "2021-01-02"]))
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def test_coverage_splits_covered_and_missing():
    src = FakeSource({"A", "C"})
    rep = coverage_report(src, {"A", "B", "C"}, "NSE", date(2021, 1, 1), date(2021, 12, 31))
    assert isinstance(rep, CoverageReport)
    assert rep.total == 3
    assert rep.covered == ["A", "C"]   # sorted
    assert rep.missing == ["B"]
