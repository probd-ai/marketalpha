#!/usr/bin/env python3
"""Compute and store the RSZ indicator for the whole PIT segment universe.

  python scripts/compute_rsz.py            # full history from 2020-01-01
  python scripts/compute_rsz.py --start 2026-01-01
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient  # noqa: E402

import settings  # noqa: E402
from rsz_compute import START_DATE, compute_rsz  # noqa: E402
from rsz_engine import RszParams  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START_DATE)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    db = MongoClient(settings.MONGO_URI)[settings.DB_NAME]
    params = RszParams()
    t0 = time.time()
    print(f"RSZ params={params}  hash={params.hash}")
    res = compute_rsz(db, params, start=args.start, end=args.end,
                      progress=lambda m: print(m, flush=True))
    print(f"\ndone in {time.time() - t0:.0f}s")
    for k, v in res.items():
        print(f"  {k:24s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
