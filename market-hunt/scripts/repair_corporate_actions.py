"""One-time repair for unadjusted corporate actions already in the price store.

`refresh_all` now force-refreshes symbols the NSE feed reports (120-day window), but
that cannot heal what is already stored: a split from 2021 is long outside any feed
window, and its phantom bar still sits in the series poisoning every rolling window
that spans it.

This walks the WHOLE price history for single-session moves too large to be a real
trade, force-refreshes each suspect from the source (tvdatafeed serves split-adjusted
history — verified: stored/TV ratio was exactly 2.000 before KIRLPNU's 1:2 and 1.000
after), then re-derives that symbol's indicators.

Over-inclusion is SAFE here in a way it is not for the recurring guard. Re-downloading
a stock whose fall was genuine (INDUSINDBK -27%) simply rewrites identical values. The
guard in refresh_all only reports these, because doing it every run forever would be
waste; doing it once, now, is the cheapest way to be certain.

    python scripts/repair_corporate_actions.py                 # scan, change nothing
    python scripts/repair_corporate_actions.py --apply         # repair
    python scripts/repair_corporate_actions.py --apply --symbols KIRLPNU,TDPOWERSYS
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient  # noqa: E402

from scripts.refresh_all import (  # noqa: E402
    DB_NAME,
    MONGO_URI,
    detect_price_discontinuities,
)

PY = sys.executable
BATCH = 60          # symbols per compute_indicators call, to keep the CLI line sane


def scan(db, since: dt.datetime) -> dict[str, list[dict]]:
    bars: dict[str, list] = {}
    for cname in [c for c in db.list_collection_names() if c.startswith("prices_")]:
        for d in db[cname].find({"date": {"$gte": since}},
                                {"_id": 0, "symbol": 1, "date": 1, "close_price": 1}):
            bars.setdefault(d["symbol"], []).append((d["date"].date(), d.get("close_price")))
    out = {}
    for sym, rows in bars.items():
        hits = detect_price_discontinuities(sorted(rows))
        if hits:
            out[sym] = hits
    return out


async def refetch(symbols: list[str]) -> dict[str, str]:
    from stock_data_manager import StockDataManager
    errs: dict[str, str] = {}
    async with StockDataManager() as mgr:
        for i, sym in enumerate(symbols, 1):
            try:
                res = await mgr.download_historical_data_for_symbol(
                    symbol=sym, force_refresh=True)
                if res and res.get("error"):
                    errs[sym] = str(res["error"])
            except Exception as exc:                              # noqa: BLE001
                errs[sym] = f"{type(exc).__name__}: {exc}"
            print(f"  [{i}/{len(symbols)}] {sym}"
                  + (f"  ERROR {errs[sym]}" if sym in errs else "  ok"), flush=True)
            await asyncio.sleep(0.25)
    return errs


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scan-from", default="2018-01-01",
                   help="indicators are stored from 2018, so that is the default floor")
    p.add_argument("--apply", action="store_true", help="actually re-download + reindex")
    p.add_argument("--symbols", default=None, help="comma-separated override of the scan")
    p.add_argument("--skip-indicators", action="store_true")
    a = p.parse_args()

    db = MongoClient(MONGO_URI)[DB_NAME]
    since = dt.datetime.strptime(a.scan_from, "%Y-%m-%d")

    if a.symbols:
        targets = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
        suspects = {}
    else:
        print(f"scanning price history from {since.date()} ...", flush=True)
        suspects = scan(db, since)
        targets = sorted(suspects)
        print(f"\n{len(targets)} symbols show an unexplained single-session move\n")
        print(f"{'symbol':<14} {'date':<12} {'prev':>10} {'close':>10} {'ratio':>7}")
        for sym in targets:
            for h in suspects[sym]:
                print(f"{sym:<14} {h['date']!s:<12} {h['prev_close']:>10.2f} "
                      f"{h['close']:>10.2f} {h['ratio']:>7.2f}")

    if not targets:
        print("nothing to repair")
        return 0
    if not a.apply:
        print(f"\n(dry run — re-run with --apply to repair {len(targets)} symbols)")
        return 0

    print(f"\nforce re-downloading {len(targets)} symbols from the source ...", flush=True)
    errs = asyncio.run(refetch(targets))
    ok = [s for s in targets if s not in errs]
    print(f"\nre-downloaded {len(ok)}/{len(targets)}"
          + (f"; FAILED: {sorted(errs)}" if errs else ""))

    if a.skip_indicators or not ok:
        return 1 if errs else 0

    # A price fix alone leaves the phantom bar inside stored indicators for up to 222
    # sessions (the mean_long window) — that is what actually corrupts selection.
    print(f"\nrecomputing indicators for {len(ok)} symbols ...", flush=True)
    bad = 0
    for i in range(0, len(ok), BATCH):
        chunk = ok[i:i + BATCH]
        rc = subprocess.call(
            [PY, "scripts/compute_indicators.py", "--start-fetch", "2016-01-01",
             "--store-from", "2018-01-01", "--force", "--symbols", ",".join(chunk)],
            env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
        print(f"  batch {i // BATCH + 1}: {len(chunk)} symbols -> exit {rc}", flush=True)
        bad += rc != 0

    print("\nre-scanning to confirm the discontinuities are gone ...", flush=True)
    left = scan(MongoClient(MONGO_URI)[DB_NAME], since)
    still = sorted(set(left) & set(targets))
    print(f"  {len(still)} of {len(targets)} still show a jump"
          + (f": {still[:20]}" if still else " — all clear"))
    print("  (a genuine crash legitimately remains; only splits should disappear)")
    return 1 if (errs or bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
