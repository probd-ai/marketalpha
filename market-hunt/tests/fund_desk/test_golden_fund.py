"""Golden master: fund.engine.fund.blend == fund_report_data.blend on the report's sleeve curves (Task 10)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.integration
HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "research_grid"


def test_blend_equals_report():
    sys.path.insert(0, str(HARNESS))
    from fund_report_data import W, blend as report_blend
    from hedge_compare import rd, to_inr

    from fund.engine.fund import blend

    val = pd.read_csv(HARNESS / "grid10_weekly.csv", index_col=0, parse_dates=True)
    gro = pd.read_csv(HARNESS / "n200_8wcap10_deploy.csv", index_col=0, parse_dates=True)
    sat = pd.read_csv(HARNESS / "n750_cap_weekly8w_cap10deploy.csv", index_col=0, parse_dates=True)
    idx = val.index.intersection(gro.index).intersection(sat.index)
    idx = idx[(idx >= "2020-10-01") & (idx <= "2026-09-25")]
    fx = rd("FX_IDC_USDINR.csv")
    gold, ndx = to_inr(rd("TVC_GOLD.csv"), fx, idx), to_inr(rd("NASDAQ_NDX.csv"), fx, idx)
    for k in range(5):
        px = pd.DataFrame({"Value core": val[str(k)].reindex(idx).ffill(), "Gold": gold / gold.iloc[0],
                           "Nasdaq-100": ndx / ndx.iloc[0], "Growth": gro[str(k)].reindex(idx).ffill(),
                           "Satellite": sat[str(k)].reindex(idx).ffill()})
        ref, _, ref_cost = report_blend(px, list(W.values()))
        res = blend(px, W)
        np.testing.assert_allclose(res.equity.to_numpy(), ref.to_numpy(), rtol=1e-12)
        assert res.reset_cost == pytest.approx(ref_cost, rel=1e-12)
