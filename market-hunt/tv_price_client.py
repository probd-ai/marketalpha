#!/usr/bin/env python3
"""
TradingView (tvdatafeed) historical price client.

Drop-in replacement for the now-dead NSE charting OHLC path. Returns the SAME
`List[PriceData]` contract as `NSEDataClient.fetch_historical_data`, so
`StockDataManager.store_price_data` / `prices_YYYY` partitioning are unchanged.

Source: rongardF tvdatafeed fork (same as the tvagent production design).
Auth: anonymous by default (rate-limited); set TV_USERNAME / TV_PASSWORD env for a
logged-in session.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import List, Optional, Tuple

from nse_data_client import PriceData

logger = logging.getLogger(__name__)

# index_meta `Symbol` -> (TradingView symbol, exchange) for index/benchmark series.
# Equities and ETFs are NOT listed here — they fall through to (symbol.upper(), "NSE").
INDEX_TV_SYMBOLS: dict[str, Tuple[str, str]] = {
    "Nifty 50": ("NIFTY", "NSE"),
    "Nifty 100": ("CNX100", "NSE"),
    "Nifty 500": ("CNX500", "NSE"),
    # Broad-cap benchmarks for small/microcap research (verified via tvdatafeed probe):
    #   Smallcap 250 → full history from 2006; Microcap 250 → index history starts 2022-01.
    "Nifty Smallcap 250": ("NIFTYSMLCAP250", "NSE"),
    "Nifty Microcap 250": ("NIFTY_MICROCAP250", "NSE"),
    "Nifty Midcap 150": ("NIFTYMIDCAP150", "NSE"),
    "Nifty Bank": ("BANKNIFTY", "NSE"),
    "Nifty IT": ("CNXIT", "NSE"),
    "Nifty Auto": ("CNXAUTO", "NSE"),
    "Nifty FMCG": ("CNXFMCG", "NSE"),
    "Nifty Metal": ("CNXMETAL", "NSE"),
    "Nifty Pharma": ("CNXPHARMA", "NSE"),
    "Nifty Realty": ("CNXREALTY", "NSE"),
    "Nifty Energy": ("CNXENERGY", "NSE"),
    "Nifty Infra": ("CNXINFRA", "NSE"),
    "Nifty PSE": ("CNXPSE", "NSE"),
    "Nifty PSU Bank": ("CNXPSUBANK", "NSE"),
    "Nifty Fin Service": ("CNXFINANCE", "NSE"),
    "Nifty Pvt Bank": ("NIFTYPVTBANK", "NSE"),
    "India VIX": ("INDIAVIX", "NSE"),
    # Verified via tvdatafeed probe (returns current daily bars on NSE):
    "Nifty MNC": ("CNXMNC", "NSE"),
    "Nifty Consumption": ("CNXCONSUMPTION", "NSE"),
    "Nifty Serv Sector": ("CNXSERVICE", "NSE"),
    "Nifty Commodities": ("CNXCOMMODITIES", "NSE"),
}


class TvPriceClient:
    """Fetches daily OHLC from TradingView via tvdatafeed."""

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None,
                 exchange: str = "NSE") -> None:
        self._username = username if username is not None else os.environ.get("TV_USERNAME")
        self._password = password if password is not None else os.environ.get("TV_PASSWORD")
        self._exchange = exchange
        self._tv = None
        self._interval = None

    def _client(self):
        """Lazily construct the TvDatafeed client (network/login happens here)."""
        if self._tv is None:
            from tvDatafeed import TvDatafeed, Interval
            self._tv = TvDatafeed(username=self._username, password=self._password)
            self._interval = Interval.in_daily
            mode = "logged-in" if self._username else "anonymous"
            logger.info(f"📡 tvdatafeed client initialised ({mode})")
        return self._tv

    def _resolve(self, symbol: str) -> Tuple[str, str]:
        """Map an index_meta Symbol to (tv_symbol, exchange)."""
        if symbol in INDEX_TV_SYMBOLS:
            return INDEX_TV_SYMBOLS[symbol]
        # NSE hyphenated tickers (e.g. BAJAJ-AUTO, NAM-INDIA) are listed on
        # TradingView with an underscore (BAJAJ_AUTO, NAM_INDIA). Normalize for
        # the fetch; the caller still stores under the original index_meta symbol.
        return symbol.strip().upper().replace("-", "_"), self._exchange

    def _fetch_sync(self, tv_symbol: str, exchange: str, start_date: datetime, end_date: datetime):
        # get_hist returns the LAST n_bars bars (ending ~today), so size n_bars to reach
        # back from NOW to start_date — not just to cover end-start — then mask the window.
        span_days = max((datetime.now() - start_date).days, 1)
        n_bars = min(int(span_days * 0.8) + 100, 10000)  # ~trading days, capped
        return self._client().get_hist(
            symbol=tv_symbol, exchange=exchange, interval=self._interval, n_bars=n_bars)

    async def fetch_historical_data(
        self,
        scrip_code: int,
        symbol: str,
        start_date: datetime = None,
        end_date: datetime = None,
        interval: str = "1",   # accepted for signature parity; daily only
        period: str = "D",
    ) -> List[PriceData]:
        end_date = end_date or datetime.now()
        start_date = start_date or datetime(2005, 1, 1)
        tv_symbol, exchange = self._resolve(symbol)
        logger.info(f"📈 [tvdatafeed] {symbol} -> {exchange}:{tv_symbol} "
                    f"{start_date.date()}..{end_date.date()}")

        try:
            df = await asyncio.to_thread(self._fetch_sync, tv_symbol, exchange, start_date, end_date)
        except Exception as e:
            logger.error(f"❌ tvdatafeed fetch failed for {symbol} ({exchange}:{tv_symbol}): {e}")
            return []

        if df is None or df.empty:
            logger.warning(f"⚠️ No tvdatafeed data for {symbol} ({exchange}:{tv_symbol})")
            return []

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        out: List[PriceData] = []
        for ts, row in df.iterrows():
            d = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if getattr(d, "tzinfo", None) is not None:
                d = d.replace(tzinfo=None)
            if not (start_date <= d <= end_date):
                continue
            try:
                close = float(row.get("close", 0) or 0)
                vol = int(row.get("volume", 0) or 0)
                out.append(PriceData(
                    scrip_code=scrip_code,
                    symbol=symbol,
                    date=d,
                    open_price=float(row.get("open", 0) or 0),
                    high_price=float(row.get("high", 0) or 0),
                    low_price=float(row.get("low", 0) or 0),
                    close_price=close,
                    volume=vol,
                    value=close * vol,   # turnover proxy; TV provides no turnover field
                    year_partition=d.year,
                    last_updated=datetime.now(),
                ))
            except (ValueError, TypeError) as e:
                logger.warning(f"⚠️ Skipping bad tvdatafeed row for {symbol}: {e}")
                continue

        logger.info(f"✅ [tvdatafeed] {len(out)} records for {symbol}")
        return out
