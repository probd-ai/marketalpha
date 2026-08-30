"""Fix the 225 failed microcap loads: most failed only because they lack a symbol_mappings row
(loader refuses without one) — NOT because they're delisted. Insert a synthetic mapping per the
capture_index_rebalance procedure, then load via the proven CLI with correct UTF-8 decoding
(the earlier batch crashed because capture_output decoded child UTF-8 as cp1252). Success is
judged against Mongo (ground truth), not stdout. Remaining failures = genuinely delisted/unfetchable.
"""
import os, sys, subprocess
from datetime import date, datetime
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, union_over, members_as_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
PCOLS = ("prices_2015_2019", "prices_2020_2024", "prices_2025_2029")


def priced(sym):
    return any(db[c].count_documents({"symbol": sym}, limit=1) for c in PCOLS)


iv = load_membership(db, "NIFTYMICROCAP250", anchor=date.today())
micro = sorted(union_over(iv, date(2022, 1, 1), date(2026, 6, 30)) | members_as_of(iv, date.today()))
missing = [s for s in micro if not priced(s)]
print(f"still-missing microcaps to retry: {len(missing)}", flush=True)

# 1) ensure a symbol_mappings row exists for each (synthetic if absent)
base = 9_000_000
made = 0
for i, s in enumerate(missing):
    if db.symbol_mappings.count_documents({"symbol": s}, limit=1) == 0:
        db.symbol_mappings.insert_one({
            "symbol": s, "nse_symbol": s, "nse_name": s, "company_name": s,
            "nse_scrip_code": base + i, "industry": "", "index_names": ["NIFTYMICROCAP250"],
            "match_confidence": "synthetic_tv", "last_updated": datetime(2026, 6, 21)})
        made += 1
print(f"synthetic mappings inserted: {made}", flush=True)

# 2) load each via CLI with correct UTF-8 decoding; verify against Mongo
env = dict(os.environ, PYTHONIOENCODING="utf-8")
ok, dead = [], []
for i, s in enumerate(missing, 1):
    subprocess.run([PY, os.path.join(ROOT, "DataLoadManagement.py"), "download-stock", s, "--force-refresh"],
                   cwd=ROOT, env=env, encoding="utf-8", errors="replace",
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if priced(s):
        ok.append(s)
    else:
        dead.append(s)
    if i % 25 == 0:
        print(f"  [{i}/{len(missing)}] loaded={len(ok)} dead={len(dead)}", flush=True)

print(f"\n=== retry done: loaded {len(ok)}, still-dead {len(dead)} ===")
print("genuinely delisted/unfetchable (residual survivorship gap):")
print(", ".join(dead))
