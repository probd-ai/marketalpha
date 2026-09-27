#!/usr/bin/env python3
"""
Stock Data Manager - Handles stock price data with 5-year partitioning

Manages historical price data storage and retrieval with MongoDB collections
partitioned by 5-year periods for horizontal scalability and performance.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import asdict
import logging
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, BulkWriteError
from pymongo.operations import ReplaceOne
import motor.motor_asyncio
from bson import ObjectId

from nse_data_client import NSEDataClient, PriceData, SymbolMapping
from tv_price_client import TvPriceClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Shared motor client cache. API routes enter a fresh StockDataManager per
# request; without this every request created its own pooled client AND re-ran
# ~10 create_index calls. Motor clients are bound to the event loop they were
# created on, so the cache is keyed per running loop (weak: a finished loop —
# e.g. each asyncio.run() in CLI scripts/tests — releases its clients).
# Index init runs once per (uri, db) — indexes live in the DB, not the loop.
import asyncio as _asyncio
import weakref as _weakref

_shared_clients: "_weakref.WeakKeyDictionary" = _weakref.WeakKeyDictionary()

# Price partitions whose indexes this process has already ensured, as (db, collection).
# Index creation used to run on EVERY _get_price_collection call — 5 commands per call,
# 35 per symbol on a 2020-2026 read, ~24k per NIFTY500 backtest. Mongo no-ops an existing
# index, but each one is still a round-trip. Once per collection per process is enough.
_price_indexes_ensured: set = set()
_indexes_initialized: set = set()


class StockDataManager:
    """
    Manages stock price data with 5-year partitioning for scalability
    """

    def __init__(self, connection_string: str | None = None, database_name: str | None = None):
        import settings
        self.connection_string = connection_string or settings.MONGO_URI
        self.database_name = database_name or settings.DB_NAME
        self.client = None
        self.db = None
        self.nse_client = None

        # Collection naming pattern: prices_YYYY_YYYY (5-year partitions)
        self.partition_years = 5

    async def __aenter__(self):
        """Async context manager entry"""
        # MongoDB async client — shared per (event loop, uri, db)
        cache_key = (self.connection_string, self.database_name)
        loop_clients = _shared_clients.setdefault(_asyncio.get_running_loop(), {})
        if cache_key not in loop_clients:
            loop_clients[cache_key] = motor.motor_asyncio.AsyncIOMotorClient(self.connection_string)
        self.client = loop_clients[cache_key]
        self.db = self.client[self.database_name]

        # NSE client (equity masters / scrip-code mapping) is created lazily on
        # first use: backtest price reads are Mongo-only and must not depend on
        # a live NSE session (concurrent eager inits time out under NSE limits).

        # TradingView (tvdatafeed) client — historical OHLC source (NSE charting API is dead)
        self.price_client = TvPriceClient()

        # Initialize collections and indexes (once per process per database)
        if cache_key not in _indexes_initialized:
            await self._initialize_collections()
            _indexes_initialized.add(cache_key)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.nse_client:
            await self.nse_client.__aexit__(exc_type, exc_val, exc_tb)
        # NOTE: self.client is the shared process-wide client — never close it
        # here; motor manages its own pool and closing would break other
        # in-flight requests using the same client.

    async def _ensure_nse_client(self):
        """Create and enter the NSE client on first use (lazy)."""
        if self.nse_client is None:
            client = NSEDataClient()
            await client.__aenter__()
            self.nse_client = client
        return self.nse_client
    
    async def _initialize_collections(self):
        """Initialize MongoDB collections and indexes"""
        logger.info("🔧 Initializing stock data collections and indexes...")
        
        # Create symbol mappings collection with indexes
        symbol_mappings = self.db.symbol_mappings
        await symbol_mappings.create_index([("symbol", ASCENDING)], unique=True)
        await symbol_mappings.create_index([("nse_scrip_code", ASCENDING)])
        await symbol_mappings.create_index([("index_name", ASCENDING)])
        await symbol_mappings.create_index([("industry", ASCENDING)])
        
        # Create stock metadata collection
        stock_metadata = self.db.stock_metadata
        await stock_metadata.create_index([("symbol", ASCENDING)], unique=True)
        # nse_scrip_code is NOT unique per symbol (NSE scrip codes can repeat across
        # renamed/demerged tickers, e.g. LT/LTM). Index for lookups, but not unique.
        await stock_metadata.create_index([("nse_scrip_code", ASCENDING)])
        await stock_metadata.create_index([("last_updated", DESCENDING)])
        
        # Create data processing logs collection
        processing_logs = self.db.data_processing_logs
        await processing_logs.create_index([("timestamp", DESCENDING)])
        await processing_logs.create_index([("status", ASCENDING)])
        await processing_logs.create_index([("symbol", ASCENDING)])
        
        logger.info("✅ Collections and indexes initialized")
    
    def _get_partition_collection_name(self, year: int) -> str:
        """Get collection name for a given year's partition"""
        # 5-year partitions: 2005-2009, 2010-2014, 2015-2019, 2020-2024, 2025-2029, etc.
        start_year = (year // self.partition_years) * self.partition_years
        # Adjust for partitioning starting from 2005
        if start_year < 2005:
            start_year = 2005
        end_year = start_year + self.partition_years - 1
        
        return f"prices_{start_year}_{end_year}"

    @staticmethod
    def _most_recent_trading_day(ref: datetime = None):
        """Most recent weekday <= ref (date). Weekend-aware; ignores exchange
        holidays — on a holiday the top-up simply re-fetches a small recent window
        and finds nothing new, which is harmless and idempotent."""
        d = (ref or datetime.now()).date()
        while d.weekday() >= 5:  # 5=Saturday, 6=Sunday
            d -= timedelta(days=1)
        return d

    async def _get_price_collection(self, year: int) -> Collection:
        """Get price collection for a specific year"""
        collection_name = self._get_partition_collection_name(year)
        collection = self.db[collection_name]

        # Ensure indexes once per collection per process, not on every read.
        # Marked before awaiting so a burst of concurrent readers (700 symbols
        # via asyncio.gather) doesn't all pile into the same create_index calls.
        key = (self.database_name, collection_name)
        if key in _price_indexes_ensured:
            return collection
        _price_indexes_ensured.add(key)

        # Create indexes if collection is new
        try:
            # Identity is (symbol, date): a symbol is the unique tradable entity.
            # scrip_code is NOT unique per symbol (collisions corrupt series), so it
            # must never be part of the uniqueness key — keep it as a lookup index only.
            await collection.create_index([("symbol", ASCENDING), ("date", ASCENDING)], unique=True)
            await collection.create_index([("symbol", ASCENDING), ("date", DESCENDING)])
            await collection.create_index([("scrip_code", ASCENDING), ("date", ASCENDING)])
            await collection.create_index([("date", DESCENDING)])
            await collection.create_index([("year_partition", ASCENDING)])
        except Exception as e:
            # Indexes might already exist
            pass
        
        return collection
    
    async def get_all_price_collections(self) -> List[str]:
        """Get all existing price collection names"""
        collections = await self.db.list_collection_names()
        price_collections = [c for c in collections if c.startswith("prices_")]
        return sorted(price_collections)
    
    async def store_symbol_mappings(self, mappings: List[SymbolMapping]) -> Dict[str, int]:
        """Store symbol mappings in the database"""
        logger.info(f"💾 Storing {len(mappings)} symbol mappings...")
        
        collection = self.db.symbol_mappings
        results = {"inserted": 0, "updated": 0, "preserved": 0, "errors": 0}

        # Fields that describe *identity/membership* and are always safe to refresh,
        # vs the NSE-match fields that must survive a failed match.
        match_fields = ("nse_scrip_code", "nse_symbol", "nse_name", "match_confidence")

        for mapping in mappings:
            try:
                mapping_doc = asdict(mapping)
                mapping_doc['_id'] = mapping.symbol  # Use symbol as primary key

                if mapping.nse_scrip_code is not None:
                    # A real NSE match: authoritative, overwrite wholesale.
                    res = await collection.replace_one(
                        {"_id": mapping.symbol}, mapping_doc, upsert=True)
                else:
                    # No match this run. NEVER null out a code we already know —
                    # the download path uses it and earlier runs may have assigned a
                    # synthetic one. Refresh membership only; seed the match fields
                    # just for genuinely new symbols.
                    set_fields = {k: v for k, v in mapping_doc.items()
                                  if k not in match_fields and k != "_id"}
                    res = await collection.update_one(
                        {"_id": mapping.symbol},
                        {"$set": set_fields,
                         "$setOnInsert": {k: mapping_doc[k] for k in match_fields}},
                        upsert=True)
                    if res.upserted_id is None:
                        results["preserved"] += 1

                results["inserted" if res.upserted_id is not None else "updated"] += 1

            except Exception as e:
                logger.error(f"❌ Error storing mapping for {mapping.symbol}: {e}")
                results["errors"] += 1

        logger.info(f"✅ Symbol mappings stored: {results}")
        return results
    
    async def get_symbol_mappings(
        self, 
        symbols: List[str] = None, 
        index_name: str = None,
        industry: str = None,
        mapped_only: bool = False
    ) -> List[SymbolMapping]:
        """Retrieve symbol mappings with optional filters"""
        collection = self.db.symbol_mappings
        
        # Build query
        query = {}
        if symbols:
            query["symbol"] = {"$in": symbols}
        if index_name:
            query["index_names"] = index_name  # Search in array field
        if industry:
            query["industry"] = industry
        if mapped_only:
            query["nse_scrip_code"] = {"$ne": None}
        
        cursor = collection.find(query)
        documents = await cursor.to_list(length=None)
        
        # Convert to SymbolMapping objects
        mappings = []
        for doc in documents:
            doc.pop('_id', None)  # Remove MongoDB _id
            # Handle backward compatibility for old records
            if 'index_name' in doc and 'index_names' not in doc:
                doc['index_names'] = [doc.pop('index_name')]
            
            # Remove any unknown fields that aren't part of SymbolMapping
            valid_fields = {
                'company_name', 'symbol', 'industry', 'index_names',
                'nse_scrip_code', 'nse_symbol', 'nse_name', 'match_confidence', 'last_updated'
            }
            filtered_doc = {k: v for k, v in doc.items() if k in valid_fields}
            mappings.append(SymbolMapping(**filtered_doc))
        
        return mappings
    
    async def store_price_data(self, price_data: List[PriceData]) -> Dict[str, int]:
        """Store price data with automatic partitioning"""
        if not price_data:
            return {"inserted": 0, "updated": 0, "errors": 0}
        
        logger.info(f"💾 Storing {len(price_data)} price records...")
        
        # Group data by year for partitioning
        partitioned_data = {}
        for record in price_data:
            year = record.year_partition
            if year not in partitioned_data:
                partitioned_data[year] = []
            partitioned_data[year].append(record)
        
        total_results = {"inserted": 0, "updated": 0, "errors": 0}
        
        # Store data in appropriate partitions
        for year, records in partitioned_data.items():
            collection = await self._get_price_collection(year)
            
            # Prepare documents
            documents = []
            for record in records:
                doc = asdict(record)
                # Identity key is (symbol, date). Keying on scrip_code here caused
                # collisions: distinct symbols sharing an NSE scrip code (e.g. LT/LTM)
                # overwrote each other's bars in a shared _id namespace.
                doc['_id'] = f"{record.symbol}_{record.date.strftime('%Y%m%d')}"
                documents.append(doc)
            
            # Bulk upsert
            try:
                operations = []
                for doc in documents:
                    operations.append(
                        ReplaceOne(
                            {"_id": doc["_id"]},
                            doc,
                            upsert=True
                        )
                    )
                
                if operations:
                    result = await collection.bulk_write(operations, ordered=False)
                    total_results["inserted"] += result.upserted_count
                    total_results["updated"] += result.modified_count
                    
                    logger.info(f"✅ Stored {len(documents)} records in partition {year}")
                    
            except BulkWriteError as e:
                logger.error(f"❌ Bulk write error for year {year}: {e}")
                total_results["errors"] += len(e.details.get("writeErrors", []))
            except Exception as e:
                logger.error(f"❌ Error storing price data for year {year}: {e}")
                total_results["errors"] += len(documents)
        
        logger.info(f"✅ Price data storage complete: {total_results}")
        return total_results
    
    async def get_price_data(
        self,
        symbol: str = None,
        scrip_code: int = None,
        start_date: datetime = None,
        end_date: datetime = None,
        limit: int = None,
        sort_order: int = -1  # -1 for descending (newest first), 1 for ascending (oldest first)
    ) -> List[PriceData]:
        """Retrieve price data with filters"""
        if not symbol and not scrip_code:
            raise ValueError("Either symbol or scrip_code must be provided")
        
        # Build query
        query = {}
        if symbol:
            query["symbol"] = symbol
        if scrip_code:
            query["scrip_code"] = scrip_code
        if start_date or end_date:
            date_query = {}
            if start_date:
                date_query["$gte"] = start_date
            if end_date:
                # Bars carry intraday timestamps (e.g. 09:15), so treat end_date
                # as inclusive of its whole calendar day.
                date_query["$lt"] = datetime.combine(end_date.date(), datetime.min.time()) + timedelta(days=1)
            query["date"] = date_query

        # Determine which partitions to query
        if start_date and end_date:
            start_year = start_date.year
            end_year = end_date.year
        elif start_date:
            start_year = start_date.year
            end_year = datetime.now().year
        elif end_date:
            start_year = 2005  # Our earliest data
            end_year = end_date.year
        else:
            # Query all partitions
            collections = await self.get_all_price_collections()
            start_year = 2005
            end_year = datetime.now().year
        
        # Query relevant partitions
        all_records = []
        mongo_sort_direction = DESCENDING if sort_order == -1 else ASCENDING
        
        # If descending sort, iterate years in reverse order for efficiency
        if sort_order == -1:
            year_range = range(end_year, start_year - 1, -1)  # 2025, 2024, 2023, ..., 2005
        else:
            year_range = range(start_year, end_year + 1)      # 2005, 2006, 2007, ..., 2025
        
        # One query per PARTITION, not per year. Five consecutive years map onto a
        # single prices_YYYY_YYYY collection and the query carries no year predicate,
        # so iterating years re-ran the IDENTICAL query up to 5x and dropped the
        # duplicates in the dedup below — a 2020-2026 read fetched 7039 rows to keep
        # 1655. Collapse to the distinct partitions, preserving iteration order.
        _seen_partitions = set()
        partition_years = []
        for year in year_range:
            name = self._get_partition_collection_name(year)
            if name not in _seen_partitions:
                _seen_partitions.add(name)
                partition_years.append(year)

        for year in partition_years:
            try:
                collection = await self._get_price_collection(year)

                cursor = collection.find(query).sort("date", mongo_sort_direction)
                if limit and len(all_records) >= limit:
                    break
                
                if limit:
                    remaining = limit - len(all_records)
                    cursor = cursor.limit(remaining)
                
                documents = await cursor.to_list(length=None)
                
                # Convert to PriceData objects
                for doc in documents:
                    doc.pop('_id', None)
                    all_records.append(PriceData(**doc))
                    
                # If we have enough records, break early
                if limit and len(all_records) >= limit:
                    break
                    
            except Exception as e:
                logger.warning(f"⚠️ Error querying partition for year {year}: {e}")
                continue
        
        # Deduplicate records by date (in case of partition overlap)
        seen_dates = set()
        deduplicated_records = []
        for record in all_records:
            if record.date not in seen_dates:
                seen_dates.add(record.date)
                deduplicated_records.append(record)
        
        # Final sort by date and apply limit
        deduplicated_records.sort(key=lambda x: x.date, reverse=(sort_order == -1))
        if limit:
            deduplicated_records = deduplicated_records[:limit]
        
        logger.info(f"📊 Retrieved {len(all_records)} records, after deduplication: {len(deduplicated_records)}")
        return deduplicated_records
    
    async def get_price_data_count(
        self,
        symbol: str = None,
        scrip_code: int = None,
        start_date: datetime = None,
        end_date: datetime = None
    ) -> int:
        """Get count of price data records with filters"""
        if not symbol and not scrip_code:
            raise ValueError("Either symbol or scrip_code must be provided")
        
        # Build query
        query = {}
        if symbol:
            query["symbol"] = symbol
        if scrip_code:
            query["scrip_code"] = scrip_code
        if start_date or end_date:
            date_query = {}
            if start_date:
                date_query["$gte"] = start_date
            if end_date:
                # Same inclusive-calendar-day contract as get_price_data.
                date_query["$lt"] = datetime.combine(end_date.date(), datetime.min.time()) + timedelta(days=1)
            query["date"] = date_query

        # Get all price collections and count records
        collections = await self.get_all_price_collections()
        
        total_count = 0
        for collection_name in collections:
            try:
                collection = self.db[collection_name]
                count = await collection.count_documents(query)
                total_count += count
            except Exception as e:
                logger.warning(f"⚠️ Error counting partition {collection_name}: {e}")
                continue
        
        return total_count

    async def refresh_symbol_mappings_from_index_meta(self) -> Dict[str, int]:
        """Refresh symbol mappings by fetching data from index_meta collection"""
        logger.info("🔄 Refreshing symbol mappings from index_meta...")
        
        # Fetch symbols from index_meta
        index_meta_collection = self.db.index_meta
        cursor = index_meta_collection.find({}, {
            "Company Name": 1,
            "Symbol": 1, 
            "Industry": 1,
            "index_name": 1
        })
        
        symbols = await cursor.to_list(length=None)
        logger.info(f"📋 Found {len(symbols)} symbols in index_meta")
        
        # Fetch NSE masters
        await self._ensure_nse_client()
        masters = await self.nse_client.fetch_equity_masters()
        
        # Create mappings
        mappings = self.nse_client.match_symbols_with_masters(symbols, masters)
        
        # Calculate statistics
        total_symbols = len(symbols)
        unique_symbols = len(mappings)
        mapped_symbols = len([m for m in mappings if m.nse_scrip_code is not None])
        unmapped_symbols = unique_symbols - mapped_symbols
        
        # Store mappings
        storage_result = await self.store_symbol_mappings(mappings)
        
        # Return comprehensive statistics
        return {
            "total_symbols": total_symbols,
            "unique_symbols": unique_symbols,
            "mapped_symbols": mapped_symbols,
            "unmapped_symbols": unmapped_symbols,
            "new_mappings": storage_result.get("inserted", 0),
            "updated_mappings": storage_result.get("updated", 0),
            "storage_errors": storage_result.get("errors", 0)
        }
    
    async def analyze_data_gaps_after_download(self, symbol: str, downloaded_data: List[PriceData], force_refresh: bool = False) -> Dict[str, Any]:
        """
        Analyze gaps between downloaded NSE data and existing DB data
        This is the correct approach: download first, then compare with DB
        
        Args:
            symbol: Stock symbol
            downloaded_data: Fresh data downloaded from NSE (source of truth)
            
        Returns:
            Gap analysis with recommendations for data processing
        """
        if not downloaded_data:
            return {
                "status": "no_data",
                "action": "skip",
                "message": "No data available from NSE",
                "insert_count": 0,
                "update_count": 0
            }
        
        # Get date range from downloaded data (source of truth)
        downloaded_dates = {data.date.date() for data in downloaded_data}
        min_downloaded_date = min(downloaded_dates)
        max_downloaded_date = max(downloaded_dates)
        
        logger.info(f"📊 Analyzing data gaps for {symbol}")
        logger.info(f"   Downloaded from NSE: {len(downloaded_data)} trading days ({min_downloaded_date} to {max_downloaded_date})")

        # Get existing data from DB for the same date range
        existing_data = await self.get_price_data(
            symbol=symbol,
            start_date=datetime.combine(min_downloaded_date, datetime.min.time()),
            end_date=datetime.combine(max_downloaded_date, datetime.max.time())
        )

        existing_dates = {data.date.date() for data in existing_data}
        logger.info(f"   Existing in DB: {len(existing_dates)} unique trading days (from {len(existing_data)} total records)")        # Compare NSE data (source of truth) with DB data
        missing_in_db = downloaded_dates - existing_dates  # Dates in NSE but not in DB (need INSERT)
        existing_in_db = downloaded_dates & existing_dates  # Dates in both (need UPDATE)
        extra_in_db = existing_dates - downloaded_dates     # Dates in DB but not in NSE (data validation issue)
        
        # Count operations needed
        insert_count = len(missing_in_db)
        update_count = len(existing_in_db)
        extra_count = len(extra_in_db)
        
        # Determine action and message based on data analysis and force_refresh
        if insert_count == len(downloaded_data):
            # All downloaded data is new
            action = "insert_all"
            message = f"All {len(downloaded_data)} trading days are new - will insert all"
        elif insert_count > 0 and update_count > 0:
            # Mixed: some new, some existing
            action = "insert_and_update" 
            message = f"Mixed data: {insert_count} new trading days to insert, {update_count} existing to update"
        elif update_count == len(downloaded_data):
            # All data exists - check if we should update or skip
            if force_refresh:
                action = "update_all"
                message = f"All {len(downloaded_data)} trading days exist - will force update all"
            else:
                action = "skip_all"
                message = f"All {len(downloaded_data)} trading days exist and current - will skip"
        else:
            # Should not happen, but handle gracefully
            action = "process"
            message = f"Processing {len(downloaded_data)} trading days"
        
        # Log extra data warning if any
        if extra_count > 0:
            logger.warning(f"⚠️ Found {extra_count} dates in DB that NSE doesn't have - possible data cleanup needed")
        
        # Calculate coverage and freshness
        coverage_percentage = (len(existing_dates) / len(downloaded_dates) * 100) if downloaded_dates else 0
        days_behind = (datetime.now().date() - max_downloaded_date).days if downloaded_dates else 999
        
        return {
            "status": "analyzed",
            "action": action,
            "message": message,
            "statistics": {
                "total_downloaded": len(downloaded_data),
                "total_existing": len(existing_data),
                "insert_count": insert_count,
                "update_count": update_count,
                "extra_in_db": extra_count,
                "coverage_percentage": coverage_percentage,
                "date_range": f"{min_downloaded_date} to {max_downloaded_date}",
                "days_behind": days_behind,
                "data_freshness": "current" if days_behind <= 1 else f"{days_behind} days old"
            },
            "insert_count": insert_count,
            "update_count": update_count,
            "missing_dates_sample": sorted(list(missing_in_db))[:10] if missing_in_db else [],
            "extra_dates_sample": sorted(list(extra_in_db))[:10] if extra_in_db else []
        }

    async def download_historical_data_for_symbol(
        self,
        symbol: str,
        start_date: datetime = None,
        end_date: datetime = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """Download historical data for a single symbol with intelligent gap analysis"""
        
        # Symbol mapping is OPTIONAL. The price source (tvdatafeed) resolves by
        # symbol, and prices are keyed _id=<symbol>_<date>, so nse_scrip_code is a
        # lookup field — not an identity and not a precondition. Gating on it here
        # (mapped_only=True) silently froze every symbol the nsecodes.csv snapshot
        # could not match: refresh-mappings nulls their code each run, so they
        # errored out before TradingView was ever contacted and simply stopped
        # updating. Fetch by symbol; carry the code through when we happen to know it.
        mappings = await self.get_symbol_mappings([symbol], mapped_only=False)
        mapping = mappings[0] if mappings else None
        scrip_code = mapping.nse_scrip_code if mapping else None

        logger.info(f"📈 Downloading historical data for {symbol} (scrip: {scrip_code})")

        # Set default date range
        if not start_date:
            start_date = datetime(2005, 1, 1)
        if not end_date:
            end_date = datetime.now()

        # Recency check (non-force): skip only when already current to the latest
        # completed trading day; otherwise fetch ONLY the gap forward (incremental),
        # not the full history. The previous `(now - last).days < 2` test skipped
        # symbols that were a single trading day stale, so the newest bar never loaded.
        if not force_refresh:
            latest = await self.get_price_data(symbol=symbol, limit=1, sort_order=-1)
            if latest:
                last_date = latest[0].date
                latest_trading_day = self._most_recent_trading_day(end_date)
                logger.info(f"ℹ️ Latest stored for {symbol}: {last_date.date()} "
                            f"(latest trading day: {latest_trading_day})")
                if last_date.date() >= latest_trading_day:
                    return {"message": f"Data current for {symbol}", "last_date": last_date}
                # Incremental top-up: re-fetch from a small buffer before last_date so
                # the upsert closes the gap without re-downloading the whole history.
                start_date = max(start_date, last_date - timedelta(days=5))
                logger.info(f"⬆️ Incremental top-up for {symbol} from {start_date.date()}")

        # Download from TradingView via tvdatafeed (source of truth; NSE charting API is dead)
        historical_data = await self.price_client.fetch_historical_data(
            scrip_code=scrip_code,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date
        )

        if not historical_data:
            return {"error": f"No historical data fetched for {symbol}"}

        # Perform gap analysis with downloaded data
        gap_analysis = await self.analyze_data_gaps_after_download(symbol, historical_data, force_refresh)
        
        logger.info(f"📊 Gap Analysis: {gap_analysis['message']}")
        if gap_analysis.get('statistics'):
            stats = gap_analysis['statistics']
            logger.info(f"   📅 Trading Days Analysis:")
            logger.info(f"      New days to insert: {stats['insert_count']}")
            logger.info(f"      Existing days to update: {stats['update_count']}")
            logger.info(f"      Coverage: {stats['coverage_percentage']:.1f}% of trading days")

        # Check if we should skip processing based on gap analysis action
        should_skip = (gap_analysis.get('action') == 'skip_all')
        
        if should_skip:
            logger.info(f"✅ Skipping storage: All {len(historical_data)} trading days already exist in database")
            storage_result = {"inserted": 0, "updated": 0, "errors": 0, "skipped": len(historical_data)}
        else:
            # Store in database (this handles inserts and updates automatically)
            storage_result = await self.store_price_data(historical_data)

        # Update metadata only if we processed data
        if not should_skip:
            await self._update_stock_metadata(symbol, scrip_code, len(historical_data))

        # Log processing
        processing_status = "skipped" if should_skip else "success"
        records_processed = 0 if should_skip else len(historical_data)
        
        await self._log_processing_activity(
            symbol=symbol,
            scrip_code=scrip_code,
            status=processing_status,
            records_processed=records_processed,
            start_date=start_date,
            end_date=end_date
        )

        return {
            "symbol": symbol,
            "scrip_code": scrip_code,
            "records_fetched": len(historical_data),
            "storage_result": storage_result,
            "gap_analysis": gap_analysis,
            "date_range": f"{start_date.date()} to {end_date.date()}"
        }

    async def download_historical_data_for_index(
        self,
        index_name: str,
        start_date: datetime = None,
        end_date: datetime = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """Download historical data for all symbols in an index"""
        
        logger.info(f"📊 Downloading historical data for index: {index_name}")
        
        # Get all symbols for the index
        mappings = await self.get_symbol_mappings(index_name=index_name, mapped_only=True)
        
        if not mappings:
            return {"error": f"No mapped symbols found for index {index_name}"}
        
        results = []
        errors = []
        
        for mapping in mappings:
            try:
                result = await self.download_historical_data_for_symbol(
                    symbol=mapping.symbol,
                    start_date=start_date,
                    end_date=end_date,
                    force_refresh=force_refresh
                )
                results.append(result)
                
                # Small delay to avoid overwhelming the API
                await asyncio.sleep(0.5)
                
            except Exception as e:
                error_msg = f"Error downloading data for {mapping.symbol}: {str(e)}"
                logger.error(f"❌ {error_msg}")
                errors.append(error_msg)
        
        return {
            "index_name": index_name,
            "total_symbols": len(mappings),
            "successful_downloads": len(results),
            "errors": len(errors),
            "results": results,
            "error_details": errors
        }
    
    async def download_historical_data_for_industry(
        self,
        industry_name: str,
        start_date: datetime = None,
        end_date: datetime = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """Download historical data for all symbols in an industry"""
        
        logger.info(f"🏭 Downloading historical data for industry: {industry_name}")
        
        # Get all symbols for the industry
        mappings = await self.get_symbol_mappings(industry=industry_name, mapped_only=True)
        
        if not mappings:
            return {"error": f"No mapped symbols found for industry {industry_name}"}
        
        results = []
        errors = []
        
        for mapping in mappings:
            try:
                result = await self.download_historical_data_for_symbol(
                    symbol=mapping.symbol,
                    start_date=start_date,
                    end_date=end_date,
                    force_refresh=force_refresh
                )
                results.append(result)
                
                # Small delay to avoid overwhelming the API
                await asyncio.sleep(0.5)
                
            except Exception as e:
                error_msg = f"Error downloading data for {mapping.symbol}: {str(e)}"
                logger.error(f"❌ {error_msg}")
                errors.append(error_msg)
        
        return {
            "industry_name": industry_name,
            "total_symbols": len(mappings),
            "successful_downloads": len(results),
            "errors": len(errors),
            "results": results,
            "error_details": errors
        }
    
    async def _update_stock_metadata(self, symbol: str, scrip_code: int, records_count: int):
        """Update stock metadata after data download"""
        collection = self.db.stock_metadata
        
        metadata = {
            "symbol": symbol,
            "nse_scrip_code": scrip_code,
            "last_updated": datetime.now(),
            "total_records": records_count,
            "last_download_status": "success"
        }
        
        # Key metadata by symbol — the unique tradable entity. scrip_code is not
        # unique per symbol, so keying on it let one symbol clobber another's row.
        await collection.replace_one(
            {"symbol": symbol},
            metadata,
            upsert=True
        )
    
    async def _log_processing_activity(
        self,
        symbol: str,
        scrip_code: int,
        status: str,
        records_processed: int = 0,
        start_date: datetime = None,
        end_date: datetime = None,
        error_message: str = None
    ):
        """Log data processing activity"""
        collection = self.db.data_processing_logs
        
        log_entry = {
            "timestamp": datetime.now(),
            "symbol": symbol,
            "scrip_code": scrip_code,
            "status": status,
            "records_processed": records_processed,
            "start_date": start_date,
            "end_date": end_date,
            "error_message": error_message
        }
        
        await collection.insert_one(log_entry)
    
    async def delete_price_data_for_symbol(self, symbol: str) -> Dict[str, Any]:
        """
        Delete all price data for a specific symbol from all partitions
        Useful for testing and data cleanup
        
        Args:
            symbol: Stock symbol to delete
            
        Returns:
            Dictionary with deletion results
        """
        logger.info(f"🗑️ Deleting all price data for symbol: {symbol}")
        
        total_deleted = 0
        partitions_affected = []
        errors = []
        
        # Get all price collections (partitions)
        try:
            collections = await self.db.list_collection_names()
            price_collections = [c for c in collections if c.startswith('prices_')]
            
            logger.info(f"   Checking {len(price_collections)} partitions...")
            
            for collection_name in price_collections:
                try:
                    collection = self.db[collection_name]
                    
                    # Check how many records exist for this symbol
                    count_before = await collection.count_documents({'symbol': symbol})
                    
                    if count_before > 0:
                        # Delete all records for this symbol
                        delete_result = await collection.delete_many({'symbol': symbol})
                        deleted_count = delete_result.deleted_count
                        
                        if deleted_count > 0:
                            total_deleted += deleted_count
                            partitions_affected.append({
                                'partition': collection_name,
                                'deleted': deleted_count
                            })
                            logger.info(f"   ✅ Deleted {deleted_count} records from {collection_name}")
                        
                except Exception as e:
                    error_msg = f"Error deleting from {collection_name}: {str(e)}"
                    errors.append(error_msg)
                    logger.error(f"   ❌ {error_msg}")
            
            # Also clean up metadata if any
            try:
                metadata_deleted = await self.db.stock_metadata.delete_many({'symbol': symbol})
                if metadata_deleted.deleted_count > 0:
                    logger.info(f"   🗑️ Deleted metadata for {symbol}")
            except Exception as e:
                logger.warning(f"   ⚠️ Could not delete metadata: {e}")
            
            # Log summary
            if total_deleted > 0:
                logger.info(f"✅ Successfully deleted {total_deleted} total records for {symbol} from {len(partitions_affected)} partitions")
            else:
                logger.info(f"ℹ️ No records found for {symbol} in any partition")
            
            return {
                'symbol': symbol,
                'total_deleted': total_deleted,
                'partitions_affected': partitions_affected,
                'errors': errors,
                'success': len(errors) == 0
            }
            
        except Exception as e:
            error_msg = f"Failed to delete data for {symbol}: {str(e)}"
            logger.error(f"❌ {error_msg}")
            return {
                'symbol': symbol,
                'total_deleted': 0,
                'partitions_affected': [],
                'errors': [error_msg],
                'success': False
            }

    async def get_symbol_date_range(self, symbol: str) -> Optional[Dict[str, datetime]]:
        """Get the date range for a specific symbol across all collections"""
        date_range = {"earliest": None, "latest": None}
        
        # Get all price collections
        collections = await self.get_all_price_collections()
        
        for collection_name in collections:
            collection = self.db[collection_name]
            
            # Get earliest date for this symbol
            earliest = await collection.find({"symbol": symbol}).sort("date", ASCENDING).limit(1).to_list(1)
            if earliest:
                earliest_date = earliest[0]["date"]
                if date_range["earliest"] is None or earliest_date < date_range["earliest"]:
                    date_range["earliest"] = earliest_date
            
            # Get latest date for this symbol
            latest = await collection.find({"symbol": symbol}).sort("date", DESCENDING).limit(1).to_list(1)
            if latest:
                latest_date = latest[0]["date"]
                if date_range["latest"] is None or latest_date > date_range["latest"]:
                    date_range["latest"] = latest_date
        
        # Return None if no data found
        if date_range["earliest"] is None or date_range["latest"] is None:
            return None
            
        return date_range

    async def get_data_statistics(self) -> Dict[str, Any]:
        """Get statistics about stored price data"""
        stats = {
            "collections": {},
            "total_records": 0,
            "symbols_with_data": set(),
            "date_range": {"earliest": None, "latest": None}
        }
        
        # Get all price collections
        collections = await self.get_all_price_collections()
        
        for collection_name in collections:
            collection = self.db[collection_name]
            
            # Count records
            count = await collection.count_documents({})
            stats["collections"][collection_name] = {"record_count": count}
            stats["total_records"] += count
            
            if count > 0:
                # Get symbols
                symbols = await collection.distinct("symbol")
                stats["symbols_with_data"].update(symbols)
                
                # Get date range
                earliest = await collection.find({}).sort("date", ASCENDING).limit(1).to_list(1)
                latest = await collection.find({}).sort("date", DESCENDING).limit(1).to_list(1)
                
                if earliest:
                    earliest_date = earliest[0]["date"]
                    if stats["date_range"]["earliest"] is None or earliest_date < stats["date_range"]["earliest"]:
                        stats["date_range"]["earliest"] = earliest_date
                
                if latest:
                    latest_date = latest[0]["date"]
                    if stats["date_range"]["latest"] is None or latest_date > stats["date_range"]["latest"]:
                        stats["date_range"]["latest"] = latest_date
        
        stats["unique_symbols_count"] = len(stats["symbols_with_data"])
        stats["symbols_with_data"] = list(stats["symbols_with_data"])  # Convert set to list for JSON serialization
        
        return stats


async def test_stock_data_manager():
    """Test function for stock data manager"""
    async with StockDataManager() as manager:
        # Test 1: Refresh symbol mappings
        print("Testing symbol mappings refresh...")
        result = await manager.refresh_symbol_mappings_from_index_meta()
        print(f"Mapping result: {result}")
        
        # Test 2: Get some mappings
        mappings = await manager.get_symbol_mappings(mapped_only=True)
        print(f"Found {len(mappings)} mapped symbols")
        
        # Test 3: Download data for one symbol (if available)
        if mappings:
            test_symbol = mappings[0].symbol
            print(f"Testing download for symbol: {test_symbol}")
            
            download_result = await manager.download_historical_data_for_symbol(
                symbol=test_symbol,
                start_date=datetime.now() - timedelta(days=30)  # Last 30 days
            )
            print(f"Download result: {download_result}")
        
        # Test 4: Get statistics
        stats = await manager.get_data_statistics()
        print(f"Data statistics: {stats}")


if __name__ == "__main__":
    asyncio.run(test_stock_data_manager())
