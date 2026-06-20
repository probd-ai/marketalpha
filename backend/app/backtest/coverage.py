from dataclasses import dataclass
from datetime import date


@dataclass
class CoverageReport:
    total: int
    covered: list[str]
    missing: list[str]


def coverage_report(source, symbols: set[str], exchange: str,
                    start: date, end: date) -> CoverageReport:
    covered, missing = [], []
    for s in sorted(symbols):
        df = source.get_ohlc(s, exchange, "daily", start, end)
        (covered if not df.empty else missing).append(s)
    return CoverageReport(total=len(symbols), covered=covered, missing=missing)
