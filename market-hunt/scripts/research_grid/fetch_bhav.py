"""Rebuild price history for delisted/merged NIFTY 50 ex-members from NSE daily bhavcopies.

TradingView has no history for these (Satyam, Ranbaxy, Cairn, HDFC Ltd, ...). The NSE equity
bhavcopy (archives.nseindia.com/content/historical/EQUITIES/<YYYY>/<MON>/cm<DD><MON><YYYY>bhav.csv.zip)
lists every EQ-series stock each session with OPEN/HIGH/LOW/CLOSE/PREVCLOSE. Chaining CLOSE/PREVCLOSE
bridges special sessions a weekday download misses (muhurat, the Sat 1-Feb-2020 budget session), but
these older files do NOT adjust PREVCLOSE for splits/bonuses (HDFC's 5:1 split shows as -79%), so
KNOWN_CA divides each ex-date return by the action's price ratio. Every |move| > 20% inside a
membership window was checked by hand: the four below are corporate actions, the rest are real
(Satyam fraud, Ranbaxy FDA ban, Unitech Oct-2008, 18-May-2009 election rally).
Research-only output: crash_prices/<SYMBOL>.csv (date, open, close adjusted to the last raw close).

  .venv/Scripts/python.exe scripts/research_grid/fetch_bhav.py [--from 2008-01-01] [--to 2020-09-30]
"""
from __future__ import annotations

import argparse
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from fetch_mcwb import HOSTS, session

SYMBOLS = ["BAJAJAUTO", "CAIRN", "HDFC", "IDFC", "JPASSOCIAT", "RANBAXY", "RELCAPITAL", "RPL",
           "SATYAMCOMP", "STER", "TATAMTRDVR"]
# ex-date -> price ratio (new/old) for splits and bonuses
KNOWN_CA = {
    "HDFC": {"2010-08-18": 1 / 5},          # face value Rs10 -> Rs2
    "STER": {"2010-06-21": 1 / 4},          # split Rs2 -> Rs1 plus 1:1 bonus
    "JPASSOCIAT": {"2007-12-17": 1 / 5,     # face value Rs10 -> Rs2
                   "2009-12-17": 2 / 3},    # 1:2 bonus
    "TATAMTRDVR": {"2011-09-12": 1 / 5},    # face value Rs10 -> Rs2
}
HERE = Path(__file__).resolve().parent
RAW = HERE / "crash_prices" / "bhav_raw.csv"


def fetch_day(s, d: pd.Timestamp):
    mon = d.strftime("%b").upper()
    name = f"cm{d.strftime('%d')}{mon}{d.year}bhav.csv.zip"
    for h in HOSTS:
        try:
            r = s.get(f"{h}/content/historical/EQUITIES/{d.year}/{mon}/{name}", timeout=30)
        except Exception:
            continue
        if r.status_code == 200 and r.content[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(r.content))
            df = pd.read_csv(z.open(z.namelist()[0]))
            df.columns = [c.strip() for c in df.columns]
            df = df[(df["SERIES"].str.strip() == "EQ") & (df["SYMBOL"].str.strip().isin(SYMBOLS))]
            df = df.assign(date=d)[["date", "SYMBOL", "OPEN", "HIGH", "LOW", "CLOSE", "PREVCLOSE"]]
            return df
    return None                                   # holiday / weekend / not published


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2008-01-01")
    ap.add_argument("--to", dest="end", default="2020-09-30")
    ap.add_argument("--rebuild-only", action="store_true", help="skip download; rebuild from bhav_raw.csv")
    ap.add_argument("--append", action="store_true", help="merge the fetched range into the existing bhav_raw.csv")
    args = ap.parse_args()
    if args.rebuild_only:
        raw = pd.read_csv(RAW, parse_dates=["date"])
    else:
        days = pd.bdate_range(args.start, args.end)
        s = session()
        with ThreadPoolExecutor(max_workers=4) as ex:
            parts = list(ex.map(lambda d: fetch_day(s, d), days))
        raw = pd.concat([p for p in parts if p is not None and len(p)], ignore_index=True)
        got_days = sum(p is not None for p in parts)
        RAW.parent.mkdir(exist_ok=True)
        if args.append and RAW.exists():
            raw = pd.concat([pd.read_csv(RAW, parse_dates=["date"]), raw], ignore_index=True)                 .drop_duplicates(["date", "SYMBOL"]).sort_values(["SYMBOL", "date"])
        raw.to_csv(RAW, index=False)
        print(f"sessions with a bhavcopy: {got_days} of {len(days)} weekdays; rows kept: {len(raw)}")

    for sym, g in raw.groupby("SYMBOL"):
        g = g.sort_values("date").reset_index(drop=True)
        # adjusted close: chain CLOSE/PREVCLOSE backward from the last raw close
        r = (g["CLOSE"] / g["PREVCLOSE"]).fillna(1.0)
        for ex_date, ratio in KNOWN_CA.get(sym.strip(), {}).items():
            r[g["date"] == pd.Timestamp(ex_date)] /= ratio
        factor = r[::-1].cumprod()[::-1].shift(-1).fillna(1.0)     # product of later returns
        adj_close = g["CLOSE"].iloc[-1] / factor
        adj = adj_close / g["CLOSE"]                                # per-day adjustment ratio
        out = pd.DataFrame({"date": g["date"], "open": g["OPEN"] * adj, "high": g["HIGH"] * adj,
                            "low": g["LOW"] * adj, "close": adj_close})
        out.to_csv(HERE / "crash_prices" / f"{sym.strip()}.csv", index=False)
        print(f"{sym.strip():11s} {len(out):5d} sessions {out.date.min().date()}..{out.date.max().date()}")


if __name__ == "__main__":
    main()
