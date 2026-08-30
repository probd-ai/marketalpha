import os
import sys
from dataclasses import dataclass
from datetime import date, datetime

# market-hunt repo root on path so `api_server`, `membership` import cleanly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GRID_WINDOWS = ("20_day_return", "price_roc_66d", "price_roc_222d")
GRID_SIZES = (10, 15, 20, 25, 30)
STRATEGY_ID = "strategy_1781939795919"


@dataclass
class RunResult:
    cagr: float
    sharpe: float
    max_drawdown: float
    alpha: float
    total_return: float
    equity: list[tuple[str, float]]
    n_points: int


def build_run_result(sim_result: dict) -> RunResult:
    s = sim_result["summary"]
    rows = sim_result["results"]
    equity = [(r["date"], float(r["portfolio_value"])) for r in rows]
    if len(equity) >= 2:
        d0 = datetime.strptime(equity[0][0], "%Y-%m-%d")
        d1 = datetime.strptime(equity[-1][0], "%Y-%m-%d")
        years = max((d1 - d0).days / 365.25, 1e-9)
        cagr = (equity[-1][1] / equity[0][1]) ** (1 / years) - 1
    else:
        cagr = float("nan")
    return RunResult(
        cagr=cagr,
        sharpe=float(s["sharpe_ratio"]),
        max_drawdown=float(s["max_drawdown"]),
        alpha=float(s["alpha"]),
        total_return=float(s["total_return"]),
        equity=equity,
        n_points=len(equity),
    )


class SimRunner:
    """Drives market-hunt's run_strategy_simulation in-process. Loads membership,
    the strategy doc, and the IndicatorDataManager ONCE; reuses them across runs."""

    def __init__(self, universe: str = "NIFTY500", mongo_uri: str = "mongodb://localhost:27017/"):
        self.universe = universe
        self.mongo_uri = mongo_uri
        self._db = None
        self._dm = None
        self._dm_ctx = None
        self._strategy = None
        self._membership = None

    async def setup(self):
        from pymongo import MongoClient
        from membership import load_membership
        from indicator_data_manager import IndicatorDataManager

        db = MongoClient(self.mongo_uri)["market_hunt"]
        self._strategy = db["simulation_strategies"].find_one({"id": STRATEGY_ID})
        if not self._strategy:
            raise ValueError(f"strategy {STRATEGY_ID} not found")
        self._membership = load_membership(db, self.universe, datetime.now().date())
        self._db = db
        self._dm_ctx = IndicatorDataManager()
        self._dm = await self._dm_ctx.__aenter__()

    async def aclose(self):
        if self._dm_ctx is not None:
            await self._dm_ctx.__aexit__(None, None, None)

    async def run(self, start: date, end: date, max_holdings: int,
                  momentum_ranking: str) -> "RunResult | None":
        from app.routers.simulation import SimulationParams
        from engine.stateless import run_strategy_simulation
        from membership import union_over

        universe_symbols = sorted(union_over(self._membership, start, end))
        params = SimulationParams(
            strategy_id=STRATEGY_ID,
            portfolio_base_value=100000,
            rebalance_frequency="monthly",
            rebalance_date="first",
            rebalance_type="equal_weight",
            universe=self.universe,
            benchmark_symbol="Nifty 50",
            max_holdings=max_holdings,
            momentum_ranking=momentum_ranking,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            point_in_time_membership=True,
            include_brokerage=True,
        )
        sim = await run_strategy_simulation(
            self._dm, self._strategy, universe_symbols, params, membership=self._membership
        )
        if not sim or len(sim.get("results", [])) < 2:
            return None
        return build_run_result(sim)

    def load_close_series(self, symbol: str, start: date, end: date) -> list[tuple[str, float]]:
        out = []
        # Covers 2015–2029; a run window past 2029 would silently truncate the benchmark.
        for coll in ("prices_2015_2019", "prices_2020_2024", "prices_2025_2029"):
            for r in self._db[coll].find(
                {"symbol": symbol,
                 "date": {"$gte": datetime(start.year, start.month, start.day),
                          "$lte": datetime(end.year, end.month, end.day)}},
                {"_id": 0, "date": 1, "close_price": 1},
            ):
                if r.get("close_price") is not None:
                    out.append((r["date"].strftime("%Y-%m-%d"), float(r["close_price"])))
        out.sort(key=lambda t: t[0])
        return out
