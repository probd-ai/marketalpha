#!/usr/bin/env python3
"""
NSE Data Client - NSE India API Integration

Handles session management, symbol mapping, and historical data fetching
from NSE India APIs for stock price data management.
"""

import asyncio
import aiohttp
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
import logging
from urllib.parse import urljoin
from pathlib import Path
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Local snapshot of the NSE equity master (scrip_code|symbol|name|type), used as a
# fallback when the live GetEQMasters endpoint is unavailable. Same pipe format.
LOCAL_MASTERS_FILE = Path(__file__).resolve().parent / "nsecodes.csv"


@dataclass
class SymbolMapping:
    """Symbol mapping between index_meta and NSE scripcode"""
    company_name: str
    symbol: str
    industry: str
    index_names: List[str]  # Changed to support multiple indices
    nse_scrip_code: Optional[int] = None
    nse_symbol: Optional[str] = None
    nse_name: Optional[str] = None
    match_confidence: Optional[float] = None
    last_updated: Optional[datetime] = None


@dataclass
class PriceData:
    """Stock price data structure"""
    scrip_code: int
    symbol: str
    date: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    value: float
    year_partition: int
    last_updated: datetime


class NSEDataClient:
    """
    NSE India API client for fetching equity masters and historical data
    """
    
    def __init__(self):
        self.base_url = "https://charting.nseindia.com"
        self.nse_url = "https://www.nseindia.com"
        self.session_cookies = None
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0',
            'DNT': '1',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin'
        }
        self.session = None
        self._masters_cache = None
        self._cache_timestamp = None
        self._cache_duration = 3600  # 1 hour cache
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers=self.headers
        )
        await self._initialize_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    async def _initialize_session(self):
        """Initialize NSE session by visiting main page for cookies"""
        try:
            logger.info("Initializing NSE session...")
            async with self.session.get(self.nse_url) as response:
                if response.status == 200:
                    self.session_cookies = response.cookies
                    logger.info("✅ NSE session initialized successfully")
                else:
                    logger.warning(f"⚠️ NSE session init returned status: {response.status}")
        except Exception as e:
            logger.error(f"❌ Failed to initialize NSE session: {e}")
            raise
    
    async def fetch_equity_masters(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetch equity masters data from NSE API
        
        Args:
            force_refresh: Force refresh cache even if valid
            
        Returns:
            List of equity master records with scrip_code, symbol, name, type
        """
        # Check cache first
        if not force_refresh and self._is_cache_valid():
            logger.info("📋 Using cached equity masters data")
            return self._masters_cache
        
        try:
            logger.info("📊 Fetching equity masters from NSE API...")
            
            url = urljoin(self.base_url, "/Charts/GetEQMasters")
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    text_data = await response.text()

                    # Parse pipe-separated data
                    masters = self._parse_masters_data(text_data)

                    # Cache the results
                    self._masters_cache = masters
                    self._cache_timestamp = datetime.now()

                    logger.info(f"✅ Fetched {len(masters)} equity master records")
                    return masters
                else:
                    logger.warning(f"⚠️ Live masters endpoint returned HTTP {response.status}; "
                                   f"falling back to local snapshot ({LOCAL_MASTERS_FILE.name})")
                    return self._load_local_masters()

        except Exception as e:
            logger.warning(f"⚠️ Could not fetch live equity masters ({e}); "
                           f"falling back to local snapshot ({LOCAL_MASTERS_FILE.name})")
            return self._load_local_masters()

    def _load_local_masters(self) -> List[Dict[str, Any]]:
        """Load equity masters from the local nsecodes.csv snapshot (same pipe format
        scrip_code|symbol|name|type). Fallback for when the live NSE endpoint is gone."""
        if not LOCAL_MASTERS_FILE.exists():
            raise FileNotFoundError(
                f"Live NSE masters unavailable and no local snapshot at {LOCAL_MASTERS_FILE}")
        text_data = LOCAL_MASTERS_FILE.read_text(encoding="utf-8")
        # Drop a header row if present (first column not numeric)
        first = text_data.lstrip().split("\n", 1)[0]
        if first and not first.split("|", 1)[0].strip().isdigit():
            text_data = text_data.split("\n", 1)[1] if "\n" in text_data else ""
        masters = self._parse_masters_data(text_data)
        self._masters_cache = masters
        self._cache_timestamp = datetime.now()
        logger.info(f"📁 Loaded {len(masters)} equity master records from {LOCAL_MASTERS_FILE.name}")
        return masters
    
    def _parse_masters_data(self, text_data: str) -> List[Dict[str, Any]]:
        """Parse pipe-separated masters data"""
        masters = []
        lines = text_data.strip().split('\n')
        
        for line in lines:
            if line.strip():
                parts = line.split('|')
                if len(parts) >= 4:
                    try:
                        masters.append({
                            'scrip_code': int(parts[0]) if parts[0].isdigit() else None,
                            'symbol': parts[1].strip(),
                            'name': parts[2].strip(),
                            'type': parts[3].strip() if len(parts) > 3 else None,
                            'additional': parts[4:] if len(parts) > 4 else []
                        })
                    except (ValueError, IndexError) as e:
                        logger.warning(f"⚠️ Skipping invalid master record: {line[:50]}... - {e}")
                        continue
        
        return masters
    
    def _is_cache_valid(self) -> bool:
        """Check if masters cache is still valid"""
        if self._masters_cache is None or self._cache_timestamp is None:
            return False
        
        cache_age = (datetime.now() - self._cache_timestamp).total_seconds()
        return cache_age < self._cache_duration
    
    async def fetch_historical_data(
        self, 
        scrip_code: int, 
        symbol: str,
        start_date: datetime = None,
        end_date: datetime = None,
        interval: str = "1",
        period: str = "D"
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical price data for a symbol
        
        Args:
            scrip_code: NSE scrip code
            symbol: Symbol name for logging
            start_date: Start date (default: 2005-01-01 or earliest available)
            end_date: End date (default: current date)
            interval: Time interval (default: "1" for 1 day)
            period: Chart period (default: "D" for daily)
            
        Returns:
            List of historical price records
        """
        try:
            # Set default date range
            if end_date is None:
                end_date = datetime.now()
            if start_date is None:
                start_date = datetime(2005, 1, 1)  # Start from 2005
            
            logger.info(f"📈 Fetching historical data for {symbol} ({scrip_code}) from {start_date.date()} to {end_date.date()}")
            
            # Prepare payload
            payload = {
                "exch": "N",  # NSE
                "instrType": "C",  # Cash/Equity
                "scripCode": scrip_code,
                "ulToken": scrip_code,
                "fromDate": int(start_date.timestamp()),
                "toDate": int(end_date.timestamp()),
                "timeInterval": interval,
                "chartPeriod": period,
                "chartStart": 0
            }
            
            url = urljoin(self.base_url, "/Charts/symbolhistoricaldata/")
            
            # Set content type for POST request
            headers = {**self.headers, 'Content-Type': 'application/json'}
            
            async with self.session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if isinstance(data, list):
                        logger.info(f"✅ Fetched {len(data)} historical records for {symbol}")
                        return self._process_historical_data(data, scrip_code, symbol)
                    elif isinstance(data, dict):
                        # Handle different response formats
                        if 'data' in data:
                            records = data['data']
                            logger.info(f"✅ Fetched {len(records)} historical records for {symbol}")
                            return self._process_historical_data(records, scrip_code, symbol)
                        # Handle NSE charting API format with field arrays
                        elif all(key in data for key in ['s', 't', 'o', 'h', 'l', 'c']):
                            # NSE returns arrays for each field: {'s': [...], 't': [...], 'o': [...], etc}
                            records = self._parse_nse_array_format(data, scrip_code, symbol)
                            logger.info(f"✅ Parsed {len(records)} historical records for {symbol}")
                            return records
                        else:
                            logger.warning(f"⚠️ Unexpected response format for {symbol}: {list(data.keys())}")
                            return []
                    else:
                        logger.warning(f"⚠️ No data returned for {symbol}")
                        return []
                else:
                    logger.error(f"❌ Failed to fetch historical data for {symbol}: HTTP {response.status}")
                    return []
                    
        except Exception as e:
            logger.error(f"❌ Error fetching historical data for {symbol}: {e}")
            return []
    
    def _process_historical_data(
        self, 
        raw_data: List[Dict], 
        scrip_code: int, 
        symbol: str
    ) -> List[PriceData]:
        """Process raw historical data into PriceData objects"""
        processed_data = []
        
        for record in raw_data:
            try:
                # Handle different possible field names
                date_field = record.get('date') or record.get('Date') or record.get('timestamp')
                open_field = record.get('open') or record.get('Open') or record.get('o')
                high_field = record.get('high') or record.get('High') or record.get('h')
                low_field = record.get('low') or record.get('Low') or record.get('l')
                close_field = record.get('close') or record.get('Close') or record.get('c')
                volume_field = record.get('volume') or record.get('Volume') or record.get('v') or 0
                value_field = record.get('value') or record.get('Value') or record.get('val') or 0
                
                # Parse date
                if isinstance(date_field, (int, float)):
                    # Unix timestamp
                    date_obj = datetime.fromtimestamp(date_field)
                elif isinstance(date_field, str):
                    # Try various date formats
                    date_obj = self._parse_date_string(date_field)
                else:
                    logger.warning(f"⚠️ Invalid date format in record: {date_field}")
                    continue
                
                # Create PriceData object
                price_data = PriceData(
                    scrip_code=scrip_code,
                    symbol=symbol,
                    date=date_obj,
                    open_price=float(open_field or 0),
                    high_price=float(high_field or 0),
                    low_price=float(low_field or 0),
                    close_price=float(close_field or 0),
                    volume=int(volume_field or 0),
                    value=float(value_field or 0),
                    year_partition=date_obj.year,
                    last_updated=datetime.now()
                )
                
                processed_data.append(price_data)
                
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"⚠️ Skipping invalid price record for {symbol}: {e}")
                continue
        
        return processed_data
    
    def _parse_nse_array_format(self, data: Dict, scrip_code: int, symbol: str) -> List[PriceData]:
        """Parse NSE charting API array format response"""
        processed_data = []
        
        try:
            # NSE returns data as arrays: {'s': [...], 't': [...], 'o': [...], etc}
            timestamps = data.get('t', [])
            opens = data.get('o', [])
            highs = data.get('h', [])
            lows = data.get('l', [])
            closes = data.get('c', [])
            volumes = data.get('v', [])
            
            # Ensure all arrays have the same length
            min_length = min(len(arr) for arr in [timestamps, opens, highs, lows, closes] if arr)
            
            if min_length == 0:
                logger.warning(f"⚠️ No valid data arrays for {symbol}")
                return []
            
            for i in range(min_length):
                try:
                    # Parse timestamp (usually Unix timestamp in milliseconds)
                    timestamp = timestamps[i] if i < len(timestamps) else None
                    if timestamp:
                        if timestamp > 1e12:  # Milliseconds
                            date_obj = datetime.fromtimestamp(timestamp / 1000)
                        else:  # Seconds
                            date_obj = datetime.fromtimestamp(timestamp)
                    else:
                        continue
                    
                    # Parse prices and volume
                    open_price = float(opens[i]) if i < len(opens) and opens[i] is not None else 0.0
                    high_price = float(highs[i]) if i < len(highs) and highs[i] is not None else 0.0
                    low_price = float(lows[i]) if i < len(lows) and lows[i] is not None else 0.0
                    close_price = float(closes[i]) if i < len(closes) and closes[i] is not None else 0.0
                    volume = int(volumes[i]) if i < len(volumes) and volumes[i] is not None else 0
                    
                    # Calculate value (approximate)
                    value = close_price * volume if close_price and volume else 0.0
                    
                    # Create PriceData object
                    price_data = PriceData(
                        scrip_code=scrip_code,
                        symbol=symbol,
                        date=date_obj,
                        open_price=open_price,
                        high_price=high_price,
                        low_price=low_price,
                        close_price=close_price,
                        volume=volume,
                        value=value,
                        year_partition=date_obj.year,
                        last_updated=datetime.now()
                    )
                    
                    processed_data.append(price_data)
                    
                except (ValueError, TypeError, IndexError) as e:
                    logger.warning(f"⚠️ Skipping invalid record at index {i} for {symbol}: {e}")
                    continue
            
            logger.info(f"📊 Successfully parsed {len(processed_data)} records from NSE array format for {symbol}")
            
        except Exception as e:
            logger.error(f"❌ Error parsing NSE array format for {symbol}: {e}")
        
        return processed_data
    
    def _parse_date_string(self, date_str: str) -> datetime:
        """Parse various date string formats"""
        formats = [
            '%Y-%m-%d',
            '%d-%m-%Y',
            '%Y/%m/%d',
            '%d/%m/%Y',
            '%Y-%m-%d %H:%M:%S',
            '%d-%m-%Y %H:%M:%S'
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        
        raise ValueError(f"Unable to parse date string: {date_str}")
    
    def match_symbols_with_masters(
        self, 
        index_meta_symbols: List[Dict[str, str]], 
        masters_data: List[Dict[str, Any]] = None
    ) -> List[SymbolMapping]:
        """
        Match symbols from index_meta with NSE masters data
        Groups symbols by company to handle multiple indices per symbol
        
        Args:
            index_meta_symbols: List of symbols from index_meta collection
            masters_data: NSE masters data (fetched if not provided)
            
        Returns:
            List of SymbolMapping objects with match results
        """
        if masters_data is None:
            if self._masters_cache is None:
                raise ValueError("No masters data available. Call fetch_equity_masters() first.")
            masters_data = self._masters_cache
        
        logger.info(f"🔍 Matching {len(index_meta_symbols)} symbols with {len(masters_data)} NSE masters")
        
        # Group symbols by company symbol to handle multiple indices
        symbol_groups = {}
        for meta_symbol in index_meta_symbols:
            symbol = meta_symbol.get('Symbol', '').strip()
            if symbol:
                if symbol not in symbol_groups:
                    symbol_groups[symbol] = {
                        'company_name': meta_symbol.get('Company Name', ''),
                        'symbol': symbol,
                        'industry': meta_symbol.get('Industry', ''),
                        'index_names': [],
                        'records': []
                    }
                
                # Add index name to the list
                index_name = meta_symbol.get('index_name', '')
                if index_name and index_name not in symbol_groups[symbol]['index_names']:
                    symbol_groups[symbol]['index_names'].append(index_name)
                
                symbol_groups[symbol]['records'].append(meta_symbol)
        
        logger.info(f"📊 Grouped into {len(symbol_groups)} unique symbols across multiple indices")
        
        mappings = []
        
        for symbol_key, symbol_group in symbol_groups.items():
            # Use the first record for matching, but collect all indices
            representative_record = symbol_group['records'][0]
            best_match = self._find_best_symbol_match(representative_record, masters_data)
            
            mapping = SymbolMapping(
                company_name=symbol_group['company_name'],
                symbol=symbol_group['symbol'],
                industry=symbol_group['industry'],
                index_names=symbol_group['index_names'],  # Multiple indices
                last_updated=datetime.now()
            )
            
            if best_match:
                mapping.nse_scrip_code = best_match['scrip_code']
                mapping.nse_symbol = best_match['symbol']
                mapping.nse_name = best_match['name']
                mapping.match_confidence = best_match['confidence']
            
            mappings.append(mapping)
        
        # Log matching statistics
        matched_count = len([m for m in mappings if m.nse_scrip_code is not None])
        total_indices = sum(len(m.index_names) for m in mappings)
        logger.info(f"✅ Successfully matched {matched_count}/{len(mappings)} unique symbols across {total_indices} index memberships")
        
        return mappings
    
    def _find_best_symbol_match(
        self, 
        meta_symbol: Dict[str, str], 
        masters_data: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Find the best matching NSE symbol for an index_meta symbol"""
        
        symbol = meta_symbol.get('Symbol', '').strip().upper()
        company_name = meta_symbol.get('Company Name', '').strip().upper()
        
        if not symbol:
            return None
        
        best_match = None
        best_confidence = 0.0
        
        for master in masters_data:
            confidence = 0.0
            
            # Get NSE symbol and split by '-' to get base symbol (like LT-EQ -> LT)
            nse_symbol = master.get('symbol', '').strip().upper()
            if nse_symbol:
                base_nse_symbol = nse_symbol.split('-')[0]  # Remove suffixes like -EQ, -BE, etc.
            else:
                base_nse_symbol = ''
            
            # Priority 1: Exact match with base NSE symbol (highest confidence)
            if symbol and base_nse_symbol == symbol:
                confidence = 1.0
            # Priority 2: Exact match with full NSE symbol 
            elif symbol and nse_symbol == symbol:
                confidence = 0.95
            # Priority 3: Symbol is part of NSE symbol (lower confidence)
            elif symbol and nse_symbol and symbol in nse_symbol:
                confidence = 0.7
            # Priority 4: Base symbol is part of our symbol
            elif symbol and base_nse_symbol and base_nse_symbol in symbol:
                confidence = 0.6
            
            # Only use company name matching as a last resort and with very low confidence
            if confidence == 0.0 and company_name:
                nse_name = master.get('name', '').strip().upper()
                if nse_name and company_name in nse_name:
                    confidence = 0.3  # Very low confidence for name-only matching
            
            # Update best match - require minimum confidence of 0.5 to avoid bad matches
            if confidence > best_confidence and confidence >= 0.5:
                best_confidence = confidence
                best_match = {
                    **master,
                    'confidence': confidence
                }
        
        return best_match
    
    def _calculate_name_similarity(self, name1: str, name2: str) -> float:
        """Calculate similarity between two company names"""
        # Simple word-based similarity
        words1 = set(re.findall(r'\w+', name1.upper()))
        words2 = set(re.findall(r'\w+', name2.upper()))
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1.intersection(words2))
        union = len(words1.union(words2))
        
        return intersection / union if union > 0 else 0.0


# --------------------------------------------------------------------- corporate actions
# Actions that make the SOURCE re-price a stock's entire history. Dividends are
# deliberately absent: tvdatafeed does not adjust for them, so including them would
# force a full re-download of every dividend payer each quarter for no benefit.
PRICE_AFFECTING_ACTIONS = (
    "split", "sub-division", "subdivision", "bonus",
    "demerger", "rights", "consolidation", "merger",
)

_CA_URL = ("https://www.nseindia.com/api/corporates-corporateActions"
           "?index=equities&from_date={frm}&to_date={to}")
_CA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    # NSE serves Brotli when offered and this aiohttp build cannot decode it
    # ("400, Can not decode content-encoding: br"), which silently returned [] and
    # skipped every split/bonus force-refresh (2026-11-22). Never advertise br here.
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
}


def filter_price_affecting(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """EQ-series rows whose subject names a price-affecting action, deduped by symbol.

    Pure so the filtering rule is testable without touching the network — the NSE
    endpoint is unauthenticated and rate-limited, and must never gate a unit test.
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for action in rows or []:
        symbol = (action.get("symbol") or "").strip()
        if not symbol or symbol in seen:
            continue
        if (action.get("series") or "").strip().upper() != "EQ":
            continue
        subject = (action.get("subject") or "").lower()
        if not any(kw in subject for kw in PRICE_AFFECTING_ACTIONS):
            continue
        seen.add(symbol)
        out.append({
            "symbol": symbol,
            "series": "EQ",
            "subject": action.get("subject", ""),
            "exDate": action.get("exDate", ""),
            "company": action.get("comp", ""),
        })
    return out


async def fetch_corporate_actions(from_date: datetime = None,
                                  to_date: datetime = None) -> List[Dict[str, Any]]:
    """Price-affecting corporate actions from NSE for [from_date, to_date].

    A split/bonus/demerger makes the price source re-issue the WHOLE adjusted series,
    so any symbol returned here must be re-downloaded in full — an incremental top-up
    would splice adjusted bars onto unadjusted history and fabricate a crash.

    Returns [] on any failure: this is a best-effort enrichment, and the caller also
    runs a price-jump backstop. It must degrade, never abort the refresh.
    """
    to_date = to_date or datetime.now()
    from_date = from_date or (to_date - timedelta(days=90))
    url = _CA_URL.format(frm=from_date.strftime("%d-%m-%Y"),
                         to=to_date.strftime("%d-%m-%Y"))
    try:
        async with aiohttp.ClientSession() as session:
            # NSE rejects API calls without cookies from the main page first.
            async with session.get("https://www.nseindia.com", headers=_CA_HEADERS,
                                   timeout=aiohttp.ClientTimeout(total=30)):
                pass
            async with session.get(url, headers=_CA_HEADERS,
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.error("❌ NSE corporate-actions API returned %s", resp.status)
                    return []
                data = await resp.json(content_type=None)
    except Exception as exc:                                   # noqa: BLE001
        logger.error("❌ Error fetching corporate actions: %s", exc)
        return []

    affected = filter_price_affecting(data)
    for a in affected:
        logger.info("📌 price-affecting action: %s (%s) — %s",
                    a["symbol"], a["exDate"], a["subject"][:60])
    logger.info("✅ %d symbols with price-affecting corporate actions", len(affected))
    return affected


async def test_nse_client():
    """Test function for NSE client"""
    async with NSEDataClient() as client:
        # Test 1: Fetch masters
        masters = await client.fetch_equity_masters()
        print(f"Fetched {len(masters)} masters")
        
        # Test 2: Test historical data with NIFTY 50
        if masters:
            nifty_master = next((m for m in masters if m['scrip_code'] == 26000), None)
            if nifty_master:
                historical = await client.fetch_historical_data(
                    26000, 
                    "NIFTY 50",
                    start_date=datetime.now() - timedelta(days=30)
                )
                print(f"Fetched {len(historical)} historical records")


if __name__ == "__main__":
    asyncio.run(test_nse_client())
