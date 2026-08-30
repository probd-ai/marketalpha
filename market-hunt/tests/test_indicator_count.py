import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indicator_data_manager import IndicatorDataManager

TEST_SYMBOL = "__TEST_TVX__"


def test_count_after_store_then_cleanup():
    async def run():
        async with IndicatorDataManager() as m:
            await m._delete_existing_data(TEST_SYMBOL, "truevx", "Nifty 50")
            await m.store_indicator_data(
                TEST_SYMBOL, "truevx", "Nifty 50",
                [{"date": "2024-01-01", "truevx_score": 50.0}], {"s1": 22})
            n = await m.count_indicator_data(TEST_SYMBOL, "truevx", "Nifty 50")
            await m._delete_existing_data(TEST_SYMBOL, "truevx", "Nifty 50")
            return n
    assert asyncio.run(run()) == 1
