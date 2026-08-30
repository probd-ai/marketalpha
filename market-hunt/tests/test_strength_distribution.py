"""Unit tests for bin_strength_values + a live-API smoke for the endpoint."""
import os
import pytest
import requests
from app.routers.analytics import bin_strength_values

BASE = os.environ.get("MH_API_BASE", "http://127.0.0.1:3001")


@pytest.mark.integration
def test_strength_distribution_endpoint_shape():
    r = requests.get(f"{BASE}/api/analytics/strength-distribution",
                     params={"universe": "NIFTY50", "metric": "mean_long",
                             "start": "2022-01-01", "end": "2022-12-31", "bins": 20}, timeout=120)
    r.raise_for_status()
    j = r.json()
    assert j["universe"] == "NIFTY50" and j["bins"] == 20
    assert len(j["bin_edges"]) == 21 and j["bin_edges"][0] == 0.0 and j["bin_edges"][-1] == 100.0
    assert 40 <= j["n_members"] <= 70   # PIT default → ever-members (N50 ≈ 63)
    assert j["point_in_time"] is True
    assert len(j["months"]) >= 10
    mo = j["months"][-1]
    assert len(mo["counts"]) == 20 and len(mo["pct"]) == 20
    assert abs(sum(mo["pct"]) - 100.0) < 1.0
    assert 0 <= mo["mean"] <= 100


def test_bins_and_boundaries():
    # 10 bins of width 10. 0 -> bin0; 100 -> last bin; 55 -> bin5; 9.9 -> bin0; 10 -> bin1.
    counts, pct = bin_strength_values([0, 9.9, 10, 55, 100], bins=10)
    assert len(counts) == 10
    assert counts[0] == 2          # 0 and 9.9
    assert counts[1] == 1          # 10
    assert counts[5] == 1          # 55
    assert counts[9] == 1          # 100 clamped to last bin
    assert sum(counts) == 5
    assert round(sum(pct), 1) == 100.0


def test_empty():
    counts, pct = bin_strength_values([], bins=20)
    assert sum(counts) == 0
    assert pct == [0.0] * 20


def test_pct_sums_to_100():
    import random
    random.seed(1)
    vals = [random.uniform(0, 100) for _ in range(500)]
    counts, pct = bin_strength_values(vals, bins=20)
    assert sum(counts) == 500
    assert abs(sum(pct) - 100.0) < 0.5  # rounding tolerance


@pytest.mark.integration
def test_strength_distribution_all_metrics():
    r = requests.get(f"{BASE}/api/analytics/strength-distribution-all",
                     params={"universe": "NIFTYMIDCAP150", "start": "2022-01-01", "end": "2022-12-31", "bins": 20}, timeout=180)
    r.raise_for_status()
    j = r.json()
    assert j["universe"] == "NIFTYMIDCAP150" and j["bins"] == 20
    assert set(j["metrics"].keys()) == {"mean_long", "mean_mid", "mean_short", "truevx_score"}
    for m, rows in j["metrics"].items():
        assert len(rows) >= 10, m
        last = rows[-1]
        assert len(last["pct"]) == 20 and abs(sum(last["pct"]) - 100.0) < 1.0


def test_sma_series():
    from app.routers.analytics import sma_series
    assert sma_series([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert sma_series([10, 20], 5) == [None, None]   # never enough
    assert sma_series([2, 2, 2, 2], 1) == [2.0, 2.0, 2.0, 2.0]


@pytest.mark.integration
def test_strength_distribution_long_endpoint():
    r = requests.get(f"{BASE}/api/analytics/strength-distribution-long",
                     params={"universe": "NIFTY50", "start": "2020-01-01", "end": "2022-12-31",
                             "bins": 20, "windows": "222,444,666"}, timeout=180)
    r.raise_for_status()
    j = r.json()
    assert j["windows"] == [222, 444, 666]
    assert set(j["metrics"].keys()) == {"sma_222", "sma_444", "sma_666"}
    # sma_222 should report members across the window; sma_666 valid later (warmup)
    s222 = [row for row in j["metrics"]["sma_222"] if row["n"] > 0]
    s666 = [row for row in j["metrics"]["sma_666"] if row["n"] > 0]
    assert len(s222) >= 12
    assert s666 and s666[0]["date"] >= "2020-08"   # 666d needs ~3y prior history
    last = s222[-1]
    assert abs(sum(last["pct"]) - 100.0) < 1.0


def test_bin_values_range():
    from app.routers.analytics import bin_values_range
    # range [-100,300], 4 bins width 100: [-100,0)|[0,100)|[100,200)|[200,300]; out-of-range clamps.
    counts, pct = bin_values_range([-50, 0, 50, 150, 250, 999, -999], bins=4, lo=-100, hi=300)
    assert counts == [2, 2, 1, 2]   # bin0: -50,-999(clamp) · bin1: 0,50 · bin2: 150 · bin3: 250,999(clamp)
    assert sum(counts) == 7 and round(sum(pct), 0) == 100


@pytest.mark.integration
def test_price_return_distribution_endpoint():
    r = requests.get(f"{BASE}/api/analytics/price-return-distribution",
                     params={"universe": "NIFTY50", "start": "2020-01-01", "bins": 20,
                             "range_min": -80, "range_max": 400}, timeout=180)
    r.raise_for_status()
    j = r.json()
    assert j["universe"] == "NIFTY50" and j["point_in_time"] is True
    assert j["bin_edges"][0] == -80 and j["bin_edges"][-1] == 400 and len(j["bin_edges"]) == 21
    assert j["end"]  # auto-filled to today
    mo = j["months"][-1]
    assert len(mo["pct"]) == 20 and abs(sum(mo["pct"]) - 100.0) < 1.0
    assert 0 <= mo["pct_pos"] <= 100
    # over 6+ years the median cumulative return should be solidly positive
    assert mo["median"] > 0
