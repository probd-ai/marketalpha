"""Download NSE month-end NIFTY 50 constituent+weight files (mcwb) into nifty50_mcwb/.

Source: nsearchives.nseindia.com/content/indices/mcwb_<mon><yy>.zip — each zip holds the NIFTY 50
file (niftymcwb.csv, later nifty50_mcwb.csv) with symbol, free-float market cap and weight as of
month end. Research-only: files land on disk, nothing is written to Mongo.

  .venv/Scripts/python.exe scripts/research_grid/fetch_mcwb.py [--from 2005-01] [--to 2026-08]
"""
from __future__ import annotations

import argparse
import io
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

OUT = Path(__file__).with_name("nifty50_mcwb")
HOSTS = ["https://nsearchives.nseindia.com", "https://archives.nseindia.com"]


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                      "Accept": "*/*", "Referer": "https://www.nseindia.com/"})
    try:
        s.get("https://www.nseindia.com/", timeout=20)
    except requests.RequestException:
        pass
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2005-01")
    ap.add_argument("--to", dest="end", default="2026-08")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    s = session()
    missing = []
    for p in pd.period_range(args.start, args.end, freq="M"):
        tag = p.strftime("%b%y").lower()
        dest = OUT / f"{p.strftime('%Y-%m')}.csv"
        if dest.exists():
            continue
        got = False
        for h in HOSTS:
            try:
                r = s.get(f"{h}/content/indices/mcwb_{tag}.zip", timeout=30)
            except requests.RequestException:
                continue
            if r.status_code == 200 and r.content[:2] == b"PK":
                z = zipfile.ZipFile(io.BytesIO(r.content))
                # some zips nest the csv inside one or two folders (mcwb_jun11/mcwb_jun11/...)
                name = next((n for n in z.namelist()
                             if n.rsplit("/", 1)[-1].lower() in ("niftymcwb.csv", "nifty50_mcwb.csv")), None)
                if name:
                    dest.write_bytes(z.read(name))
                    got = True
                    break
        if not got:
            missing.append(str(p))
        time.sleep(0.4)
    print(f"saved to {OUT}; missing months: {missing}")


if __name__ == "__main__":
    main()
