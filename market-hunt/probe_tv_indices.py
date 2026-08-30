#!/usr/bin/env python3
"""Probe TradingView (anonymous tvdatafeed) for NSE microcap/smallcap index tickers.
Reports which candidate symbols return daily bars + their coverage. Read-only."""
import logging
logging.basicConfig(level=logging.WARNING)
from tvDatafeed import TvDatafeed, Interval

tv = TvDatafeed()  # anonymous

candidates = [
    ("NIFTY_MICROCAP250", "NSE"),
    ("NIFTYMICROCAP250", "NSE"),
    ("NIFTY_MICROCAP_250", "NSE"),
    ("NIFTYSMLCAP250", "NSE"),
    ("NIFTY_SMLCAP_250", "NSE"),
    ("NIFTYSMALLCAP250", "NSE"),
    ("CNXSMALLCAP", "NSE"),
    ("NIFTYSMLCAP100", "NSE"),
    ("NIFTYMIDCAP150", "NSE"),
    ("NIFTY_MIDCAP_150", "NSE"),
]

for sym, exch in candidates:
    try:
        df = tv.get_hist(symbol=sym, exchange=exch, interval=Interval.in_daily, n_bars=5000)
        if df is None or df.empty:
            print(f"{exch}:{sym:22} -> EMPTY")
        else:
            print(f"{exch}:{sym:22} -> {len(df)} bars  {df.index.min().date()}..{df.index.max().date()}  lastClose={df['close'].iloc[-1]:.1f}")
    except Exception as e:
        print(f"{exch}:{sym:22} -> ERR {type(e).__name__}: {str(e)[:60]}")
