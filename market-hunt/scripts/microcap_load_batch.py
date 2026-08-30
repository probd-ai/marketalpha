"""Batch-load full price history for NIFTY Microcap 250 members that aren't in the store yet.

RUN THIS IN YOUR OWN SHELL WITH TV CREDS SET (full history needs them):
    export TV_USERNAME=...   # (PowerShell: $env:TV_USERNAME = '...')
    export TV_PASSWORD=...
    ./.venv/Scripts/python.exe scripts/microcap_load_batch.py            # dry-run: list what's missing
    ./.venv/Scripts/python.exe scripts/microcap_load_batch.py --load     # actually download

It loads only CURRENTLY-LISTED missing microcaps (delisted ones can't be fetched via tvfeed —
that residual gap is logged for the backtest to bound). Idempotent: skips names already priced.
"""
import os, sys, subprocess
from pymongo import MongoClient

from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, union_over, members_as_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
LOAD = "--load" in sys.argv

# all names that were EVER microcap members in the window + current (PIT needs historical members)
iv = load_membership(db, "NIFTYMICROCAP250", anchor=date.today())
micro = sorted(union_over(iv, date(2022, 1, 1), date(2026, 6, 30)) | members_as_of(iv, date.today()))
priced = set()
for coll in ("prices_2015_2019", "prices_2020_2024", "prices_2025_2029"):
    priced |= set(db[coll].distinct("symbol"))
missing = [s for s in micro if s not in priced]

print(f"ever-microcap members (2022-2026) + current: {len(micro)}")
print(f"already priced: {len(micro) - len(missing)}   MISSING (to load): {len(missing)}")
print("(anonymous tvfeed gives full history for LIVING names; delisted will fail = accepted gap)")

if not LOAD:
    print("\n[dry-run] missing symbols:")
    print(", ".join(missing))
    print("\nre-run with --load to download.")
    sys.exit(0)

env = dict(os.environ, PYTHONIOENCODING="utf-8")     # avoid Windows cp1252 emoji crash in loader
ok, fail = [], []
for i, sym in enumerate(missing, 1):
    print(f"[{i}/{len(missing)}] {sym}", flush=True)
    r = subprocess.run([PY, os.path.join(ROOT, "DataLoadManagement.py"),
                        "download-stock", sym, "--force-refresh"],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    tail = (r.stdout or "")[-300:]
    if r.returncode == 0 and ("✅" in tail or "stored" in tail.lower() or "saved" in tail.lower()):
        ok.append(sym)
    else:
        fail.append(sym)
        print(f"   ! {sym} may have failed (rc={r.returncode}); stderr: {(r.stderr or '')[-200:]}")

print(f"\n=== done: loaded {len(ok)}, failed/empty {len(fail)} ===")
if fail:
    print("failed (likely delisted/unfetchable — the residual survivorship set):")
    print(", ".join(fail))
