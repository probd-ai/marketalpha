from datetime import date, datetime
import pytest
from pymongo import MongoClient
from app.data.sources.mongo_source import MongoDataSource, INDEX_SYMBOL_MAP

TEST_DB = "market_hunt_test"


def _doc(symbol, d: datetime, close: float, scrip=1):
    return {
        "_id": f"{symbol}_{d.strftime('%Y%m%d')}",
        "scrip_code": scrip, "symbol": symbol, "date": d,
        "open_price": close - 1, "high_price": close + 1,
        "low_price": close - 2, "close_price": close,
        "volume": 1000, "value": close * 1000,
        "year_partition": d.year, "last_updated": d,
    }


@pytest.fixture
def mongo():
    client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2000)
    client.admin.command("ping")  # fail fast if Mongo is down
    db = client[TEST_DB]
    db["prices_2020_2024"].insert_many([
        _doc("RELIANCE", datetime(2021, 6, 1), 100.0),
        _doc("RELIANCE", datetime(2021, 6, 2), 101.0),
        _doc("TCS", datetime(2021, 6, 1), 50.0),
    ])
    db["prices_2025_2029"].insert_many([
        _doc("RELIANCE", datetime(2025, 1, 2), 200.0),
    ])
    db["prices_2020_2024"].insert_one(
        {**_doc("Nifty 50", datetime(2021, 6, 1), 15000.0)}
    )
    yield client
    client.drop_database(TEST_DB)
    client.close()


def test_get_ohlc_maps_columns_and_filters_range(mongo):
    src = MongoDataSource("mongodb://localhost:27017/", TEST_DB)
    df = src.get_ohlc("RELIANCE", "NSE", "daily", date(2021, 1, 1), date(2021, 12, 31))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [100.0, 101.0]   # 2025 row excluded by range
    assert str(df.index.tz) == "None"               # tz-naive
    assert df.index[0] == __import__("pandas").Timestamp("2021-06-01")


def test_get_ohlc_spans_partitions(mongo):
    src = MongoDataSource("mongodb://localhost:27017/", TEST_DB)
    df = src.get_ohlc("RELIANCE", "NSE", "daily", date(2021, 1, 1), date(2025, 12, 31))
    assert df["close"].tolist() == [100.0, 101.0, 200.0]


def test_get_ohlc_missing_symbol_returns_empty(mongo):
    src = MongoDataSource("mongodb://localhost:27017/", TEST_DB)
    df = src.get_ohlc("NOSUCH", "NSE", "daily", date(2021, 1, 1), date(2021, 12, 31))
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_index_symbol_mapping(mongo):
    src = MongoDataSource("mongodb://localhost:27017/", TEST_DB, INDEX_SYMBOL_MAP)
    df = src.get_ohlc("NIFTY", "NSE", "daily", date(2021, 1, 1), date(2021, 12, 31))
    assert df["close"].tolist() == [15000.0]
