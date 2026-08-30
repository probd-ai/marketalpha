"""Symbol mappings must never gate or silently freeze a price download.

Two defects found during the 2026-08-01 refresh, both of which froze ~64 symbols
(including the Nifty 100 / Midcap 150 / Smallcap 250 / Microcap 250 benchmark
series) at the *previous* refresh date while the pipeline still reported OK:

1. ``refresh-mappings`` rebuilds symbol_mappings from the nsecodes.csv snapshot
   and writes nse_scrip_code=None for anything it cannot fuzzy-match, clobbering
   synthetic codes assigned by earlier runs.
2. ``download_historical_data_for_symbol`` gated on that code (mapped_only=True)
   and returned "No NSE mapping found" *before ever calling TradingView* — even
   though the TV client resolves purely by symbol and prices are keyed
   _id=<symbol>_<date>, so the scrip code is a lookup field, not an identity.

These tests pin both contracts. Pure unit tests — no Mongo, no network.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pytest

from nse_data_client import SymbolMapping
from stock_data_manager import StockDataManager


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- fakes
class FakeUpdateResult:
    def __init__(self, upserted_id=None):
        self.upserted_id = upserted_id


class FakeMappingCollection:
    """Minimal async stand-in for the symbol_mappings collection."""

    def __init__(self, docs=None):
        self.docs = dict(docs or {})

    async def replace_one(self, flt, doc, upsert=False):
        key = flt["_id"]
        existed = key in self.docs
        self.docs[key] = dict(doc)
        return FakeUpdateResult(None if existed else key)

    async def update_one(self, flt, update, upsert=False):
        key = flt["_id"]
        existed = key in self.docs
        doc = self.docs.get(key, {"_id": key})
        doc.update(update.get("$set", {}))
        if not existed:
            doc.update(update.get("$setOnInsert", {}))
        self.docs[key] = doc
        return FakeUpdateResult(None if existed else key)


class FakeDB:
    def __init__(self, mapping_collection):
        self.symbol_mappings = mapping_collection


class RecordingPriceClient:
    """Captures the scrip_code the download path hands to tvdatafeed."""

    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or []

    async def fetch_historical_data(self, scrip_code=None, symbol=None,
                                    start_date=None, end_date=None):
        self.calls.append({"scrip_code": scrip_code, "symbol": symbol})
        return list(self.rows)


def _manager(mappings, price_client):
    """A StockDataManager wired to fakes (its __init__ performs no I/O)."""
    mgr = StockDataManager()
    mgr.price_client = price_client

    async def fake_get_symbol_mappings(symbols=None, index_name=None,
                                       industry=None, mapped_only=False):
        found = [m for m in mappings if not symbols or m.symbol in symbols]
        if mapped_only:
            found = [m for m in found if m.nse_scrip_code is not None]
        return found

    async def fake_get_price_data(*a, **kw):
        return []

    mgr.get_symbol_mappings = fake_get_symbol_mappings
    mgr.get_price_data = fake_get_price_data
    return mgr


def _mapping(symbol, code=None):
    return SymbolMapping(company_name=symbol, symbol=symbol, industry="",
                         index_names=["NIFTY500"], nse_scrip_code=code,
                         last_updated=datetime(2026, 8, 1))


# --------------------------------------------------------------------------- gate
class TestDownloadNotGatedOnScripCode:
    """The TV client fetches by symbol; a missing scrip code must not block it."""

    def test_symbol_with_no_mapping_still_reaches_tradingview(self):
        client = RecordingPriceClient()
        mgr = _manager([], client)

        result = _run(mgr.download_historical_data_for_symbol(symbol="Nifty 100"))

        assert client.calls, (
            "download aborted before contacting TradingView for an unmapped symbol; "
            f"got {result!r}")
        assert client.calls[0]["symbol"] == "Nifty 100"

    def test_symbol_with_null_scrip_code_still_reaches_tradingview(self):
        client = RecordingPriceClient()
        mgr = _manager([_mapping("LENSKART", code=None)], client)

        result = _run(mgr.download_historical_data_for_symbol(symbol="LENSKART"))

        assert client.calls, (
            "download aborted before contacting TradingView for a null-code mapping; "
            f"got {result!r}")
        assert client.calls[0]["scrip_code"] is None

    def test_known_scrip_code_is_passed_through(self):
        client = RecordingPriceClient()
        mgr = _manager([_mapping("RELIANCE", code=2885)], client)

        _run(mgr.download_historical_data_for_symbol(symbol="RELIANCE"))

        assert client.calls[0]["scrip_code"] == 2885

    def test_empty_source_still_reports_a_source_gap_not_a_mapping_error(self):
        """A genuine TV gap must stay distinguishable from a mapping problem."""
        client = RecordingPriceClient(rows=[])
        mgr = _manager([], client)

        result = _run(mgr.download_historical_data_for_symbol(symbol="AXISNIFTY"))

        assert "No historical data" in result.get("error", "")
        assert "mapping" not in result.get("error", "").lower()


# --------------------------------------------------------------------------- store
class TestStoreSymbolMappingsPreservesCodes:
    """A refresh that cannot match a symbol must not destroy a known code."""

    def _store(self, existing, incoming):
        coll = FakeMappingCollection(existing)
        mgr = StockDataManager()
        mgr.db = FakeDB(coll)
        result = _run(mgr.store_symbol_mappings(incoming))
        return coll, result

    def test_unmatched_symbol_keeps_existing_scrip_code(self):
        existing = {"LENSKART": {"_id": "LENSKART", "symbol": "LENSKART",
                                 "nse_scrip_code": 9000001,
                                 "match_confidence": "synthetic_tv"}}
        coll, _ = self._store(existing, [_mapping("LENSKART", code=None)])

        assert coll.docs["LENSKART"]["nse_scrip_code"] == 9000001, (
            "refresh clobbered a known scrip code with None — this is the bug that "
            "silently froze ~64 symbols")

    def test_unmatched_symbol_still_refreshes_index_membership(self):
        existing = {"LENSKART": {"_id": "LENSKART", "symbol": "LENSKART",
                                 "nse_scrip_code": 9000001,
                                 "index_names": ["NIFTY500"]}}
        incoming = _mapping("LENSKART", code=None)
        incoming.index_names = ["NIFTY500", "NIFTY200"]
        coll, _ = self._store(existing, [incoming])

        assert coll.docs["LENSKART"]["index_names"] == ["NIFTY500", "NIFTY200"]
        assert coll.docs["LENSKART"]["nse_scrip_code"] == 9000001

    def test_new_unmatched_symbol_is_still_created(self):
        coll, _ = self._store({}, [_mapping("BRANDNEW", code=None)])

        assert "BRANDNEW" in coll.docs
        assert coll.docs["BRANDNEW"]["nse_scrip_code"] is None

    def test_matched_symbol_overwrites_stale_code(self):
        existing = {"LENSKART": {"_id": "LENSKART", "symbol": "LENSKART",
                                 "nse_scrip_code": 9000001}}
        coll, _ = self._store(existing, [_mapping("LENSKART", code=4321)])

        assert coll.docs["LENSKART"]["nse_scrip_code"] == 4321, (
            "a real NSE match must win over a synthetic placeholder")

    def test_preserved_codes_are_reported(self):
        existing = {"LENSKART": {"_id": "LENSKART", "symbol": "LENSKART",
                                 "nse_scrip_code": 9000001}}
        _, result = self._store(existing, [_mapping("LENSKART", code=None)])

        assert result.get("preserved") == 1
