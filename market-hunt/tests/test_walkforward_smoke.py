import asyncio
from datetime import date
import pytest
from walkforward.sim_runner import SimRunner


@pytest.mark.integration
def test_single_run_against_live_sim_produces_valid_result():
    async def go():
        runner = SimRunner()
        await runner.setup()
        try:
            r = await runner.run(date(2020, 1, 1), date(2020, 12, 31), 20, "price_roc_66d")
        finally:
            await runner.aclose()
        return r
    r = asyncio.run(go())
    assert r is not None
    assert r.n_points > 50            # a year of trading days
    assert -100 < r.max_drawdown <= 0
    # warmup correctness: first rebalance has real signals -> portfolio moved off base
    assert r.equity[0][1] == pytest.approx(100000.0, rel=0.05)
    assert r.total_return != 0.0
