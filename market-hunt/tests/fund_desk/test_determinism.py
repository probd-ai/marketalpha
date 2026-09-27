"""The engine must give byte-identical results in every process (Python's string-hash seed changes per process)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = r'''
import sys, json
sys.path.insert(0, r"{root}"); sys.path.insert(0, r"{tests}")
import numpy as np, pandas as pd
from helpers import make_market
from fund.engine.costs import Costs
from fund.engine.grid10 import Grid10
from fund.engine.tranche import Tranche
from fund.engine.replay import replay_sleeve
syms = [f"S{{i:02d}}" for i in range(30)]
days = 80
idx = pd.bdate_range("2021-01-04", periods=days)
rng = np.random.default_rng(11)
roc = pd.DataFrame(np.round(rng.normal(0, 0.05, (days, 30)), 2), index=idx, columns=syms)   # rounded -> many ties
grid = pd.DataFrame(rng.choice([-1.0, 1.0], (days, 30)), index=idx, columns=syms)
elig = pd.DataFrame(rng.random((days, 30)) > 0.3, index=idx, columns=syms)
m = make_market(syms, days, seed=5, grid=grid, roc=roc, elig=elig)
c = Costs(0.0012, 0.001, 0.0005)
out = []
for book in (Grid10(10), Tranche(5, 8, 40, 0.10, True)):
    r = replay_sleeve(book, m, c, idx[1], idx[-1], set(idx[::5]), 1e6)
    out.append([repr(x) for x in r.equity.to_numpy()])
print(json.dumps(out))
'''


def _run(seed):
    code = SCRIPT.format(root=ROOT, tests=Path(__file__).parent)
    env = {**os.environ, "PYTHONHASHSEED": str(seed)}
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout


def test_identical_across_hash_seeds():
    assert _run(1) == _run(2) == _run(12345)
