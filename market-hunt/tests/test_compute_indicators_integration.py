import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.compute_indicators import run, BENCHMARK, INDICATOR
from indicator_data_manager import IndicatorDataManager


def _count(sym):
    async def c():
        async with IndicatorDataManager() as m:
            return await m.count_indicator_data(sym, INDICATOR, BENCHMARK)
    return asyncio.run(c())


def test_run_one_symbol_stores_scores_from_2018():
    summary = asyncio.run(run(start_fetch="2016-01-01", store_from="2018-01-01",
                              symbols=["RELIANCE"], force=True))
    assert summary["ok"] == 1
    assert summary["failed"] == []
    assert _count("RELIANCE") > 0

    async def check():
        async with IndicatorDataManager() as m:
            return await m.get_indicator_data("RELIANCE", INDICATOR, BENCHMARK)
    rows = asyncio.run(check())
    assert min(r["date"] for r in rows) >= "2018-01-01"
    assert all(0.0 <= r["truevx_score"] <= 100.0 for r in rows)


def test_rerun_is_idempotent():
    n1 = _count("RELIANCE")
    asyncio.run(run(start_fetch="2016-01-01", store_from="2018-01-01",
                    symbols=["RELIANCE"], force=True))
    n2 = _count("RELIANCE")
    assert n1 == n2
