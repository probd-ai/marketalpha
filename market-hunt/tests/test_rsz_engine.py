"""RSZ engine — pure-math tests. No DB."""
import math

import pytest

from rsz_engine import (
    RszParams, aligned_count, composite_z, info_ratio, percentile_ranks,
    rel_series, segment_for,
)


# ── segment resolution ──────────────────────────────────────────────────────
def test_segment_most_specific_wins():
    # A Nifty 50 name also sits in NIFTY100/NIFTY500; it must score as a largecap.
    assert segment_for({"NIFTY50", "NIFTY100", "NIFTY500"}) == "NIFTY50"
    assert segment_for({"NIFTYMIDCAP150", "NIFTY500"}) == "NIFTYMIDCAP150"
    assert segment_for({"NIFTY500"}) == "NIFTY500"


def test_segment_unknown_falls_back():
    assert segment_for({"SOMETHING_ELSE"}) == "NIFTY500"
    assert segment_for(set()) == "NIFTY500"


# ── relative line ───────────────────────────────────────────────────────────
def test_rel_series_divides_pointwise():
    assert rel_series([100.0, 110.0], [50.0, 55.0]) == [2.0, 2.0]


def test_rel_series_rejects_length_mismatch():
    with pytest.raises(ValueError):
        rel_series([1.0, 2.0], [1.0])


def test_rel_series_nan_on_bad_benchmark():
    out = rel_series([100.0, 110.0], [0.0, 55.0])
    assert math.isnan(out[0]) and out[1] == 2.0


# ── information ratio: the core statistic ───────────────────────────────────
def test_info_ratio_is_nan_until_window_fills():
    rel = [1.0 + 0.01 * i for i in range(10)]
    z = info_ratio(rel, 5)
    assert all(math.isnan(v) for v in z[:5])
    assert not math.isnan(z[5])


def test_info_ratio_zero_when_flat():
    z = info_ratio([1.0] * 30, 5)
    # a flat relative line has zero drift AND zero sigma -> undefined, not a score
    assert all(math.isnan(v) for v in z)


def test_info_ratio_sign_follows_direction():
    up = info_ratio([1.0 * (1.01 ** i) * (1 + 0.001 * ((-1) ** i)) for i in range(40)], 10)
    dn = info_ratio([1.0 * (0.99 ** i) * (1 + 0.001 * ((-1) ** i)) for i in range(40)], 10)
    assert up[-1] > 0 and dn[-1] < 0


def test_info_ratio_does_not_saturate():
    """The defect that killed truevx: a stronger trend MUST produce a bigger number.

    Two stocks, same relative volatility, one drifting twice as fast. truevx's tanh
    returns ~1.0 for both; the information ratio must separate them.
    """
    def series(drift):
        return [(1 + drift) ** i * (1 + 0.002 * ((-1) ** i)) for i in range(60)]
    slow = info_ratio(series(0.002), 22)[-1]
    fast = info_ratio(series(0.004), 22)[-1]
    assert fast > slow * 1.5, f"expected clear separation, got slow={slow} fast={fast}"


def test_info_ratio_is_volatility_normalized():
    """Same excess return, more noise -> lower conviction. This is what makes two
    different stocks comparable in one cross-section."""
    calm = [(1.003) ** i * (1 + 0.001 * ((-1) ** i)) for i in range(60)]
    wild = [(1.003) ** i * (1 + 0.020 * ((-1) ** i)) for i in range(60)]
    assert info_ratio(calm, 22)[-1] > info_ratio(wild, 22)[-1]


def test_info_ratio_independent_of_series_start():
    """Reproducibility: truevx's EMA was seeded at data[0], so loading more history
    silently changed past values. A rolling window must not do that."""
    full = [(1.002) ** i * (1 + 0.003 * ((-1) ** i)) for i in range(120)]
    a = info_ratio(full, 22)
    b = info_ratio(full[40:], 22)          # same data, later start
    for k in range(len(b)):
        if not math.isnan(b[k]):
            assert a[k + 40] == pytest.approx(b[k], rel=1e-12)


# ── composite ───────────────────────────────────────────────────────────────
def test_composite_is_long_weighted():
    # long horizon strong, short weak -> blend must lean to the long leg
    assert composite_z(0.0, 0.0, 3.0, (0.5, 1.0, 1.5)) == pytest.approx(1.5)
    assert composite_z(3.0, 0.0, 0.0, (0.5, 1.0, 1.5)) == pytest.approx(0.5)


def test_composite_nan_if_any_horizon_missing():
    assert math.isnan(composite_z(1.0, math.nan, 1.0, (0.5, 1.0, 1.5)))


# ── cross-sectional ranking ─────────────────────────────────────────────────
def test_percentile_ranks_span_full_range():
    r = percentile_ranks({"a": 1.0, "b": 2.0, "c": 3.0})
    assert r["a"] == 0.0 and r["c"] == 100.0 and r["b"] == pytest.approx(50.0)


def test_percentile_ranks_average_ties():
    r = percentile_ranks({"a": 5.0, "b": 5.0, "c": 9.0})
    assert r["a"] == r["b"] == pytest.approx(25.0)
    assert r["c"] == 100.0


def test_percentile_ranks_skip_nan():
    r = percentile_ranks({"a": 1.0, "b": math.nan, "c": 2.0})
    assert set(r) == {"a", "c"}


def test_percentile_ranks_no_mass_tie_on_continuous_input():
    """The headline fix. truevx put 7.3% of ALL observations at exactly 100.0, so
    the top decile could not be ordered. A continuous input must not do that."""
    vals = {f"s{i}": math.log(1 + i) * 0.37 for i in range(200)}
    r = percentile_ranks(vals)
    at_top = sum(1 for v in r.values() if v >= 99.5)
    assert at_top == 1, f"{at_top} names tied at the top — ranking is degenerate"


# ── alignment ───────────────────────────────────────────────────────────────
def test_aligned_count_counts_agreeing_horizons():
    assert aligned_count(95.0, 80.0, 75.0, 70.0) == 3
    assert aligned_count(95.0, 80.0, 10.0, 70.0) == 2
    assert aligned_count(10.0, 20.0, 30.0, 70.0) == 0


def test_aligned_count_ignores_nan():
    assert aligned_count(95.0, math.nan, 90.0, 70.0) == 2


# ── params ──────────────────────────────────────────────────────────────────
def test_param_hash_is_stable_and_discriminating():
    assert RszParams().hash == RszParams().hash
    assert RszParams().hash != RszParams(horizons=(10, 50, 200)).hash
    assert RszParams().hash != RszParams(weights=(1.0, 1.0, 1.0)).hash
