"""The numpy path must never drift from the readable reference implementation."""
import math

import numpy as np
import pytest

from rsz_compute import info_ratio_vec
from rsz_engine import info_ratio


def _same(rel, n):
    ref = info_ratio(list(rel), n)
    vec = info_ratio_vec(np.array(rel, dtype=float), n)
    assert len(ref) == len(vec)
    for i, (a, b) in enumerate(zip(ref, vec)):
        if math.isnan(a):
            assert not np.isfinite(b), f"idx {i}: ref NaN but vec {b}"
        else:
            assert b == pytest.approx(a, rel=1e-9, abs=1e-12), f"idx {i}: {a} vs {b}"


def test_matches_reference_on_trending_series():
    rng = np.random.default_rng(3)
    rel = np.cumprod(1 + rng.normal(0.0008, 0.012, 400))
    for n in (5, 22, 66, 222):
        _same(rel, n)


def test_matches_reference_with_embedded_gaps():
    rng = np.random.default_rng(4)
    rel = np.cumprod(1 + rng.normal(0.0, 0.01, 200))
    rel[57] = np.nan
    rel[58] = np.nan
    _same(rel, 22)


def test_matches_reference_when_series_too_short():
    _same(np.array([1.0, 1.1, 1.2]), 22)


def test_matches_reference_on_flat_series():
    _same(np.ones(80), 22)


def test_vectorized_is_start_independent():
    """Same reproducibility guarantee as the reference: loading more history must
    never change an already-published value."""
    rng = np.random.default_rng(9)
    rel = np.cumprod(1 + rng.normal(0.0005, 0.011, 500))
    a = info_ratio_vec(rel, 66)
    b = info_ratio_vec(rel[120:], 66)
    for k in range(len(b)):
        if np.isfinite(b[k]):
            assert a[k + 120] == pytest.approx(b[k], rel=1e-12)


# ── gap handling ────────────────────────────────────────────────────────────
def test_ffill_carries_short_gaps():
    from rsz_compute import ffill_gaps
    a = np.array([1.0, np.nan, np.nan, 2.0])
    out = ffill_gaps(a, limit=5)
    assert list(out) == [1.0, 1.0, 1.0, 2.0]


def test_ffill_leaves_long_gaps_as_holes():
    from rsz_compute import ffill_gaps
    a = np.array([1.0] + [np.nan] * 7 + [2.0])
    out = ffill_gaps(a, limit=3)
    assert np.isfinite(out[1]) and np.isfinite(out[3])
    assert not np.isfinite(out[4]), "gap longer than the limit must stay a hole"


def test_ffill_never_backfills_before_listing():
    from rsz_compute import ffill_gaps
    a = np.array([np.nan, np.nan, 5.0, 6.0])
    out = ffill_gaps(a, limit=5)
    assert not np.isfinite(out[0]) and not np.isfinite(out[1])


def test_one_missing_bar_no_longer_blanks_a_year():
    """Regression: a single hole used to null every window containing it — 222
    sessions of output lost for one absent bar."""
    from rsz_compute import ffill_gaps
    rng = np.random.default_rng(21)
    rel = np.cumprod(1 + rng.normal(0.0004, 0.01, 600))
    holed = rel.copy()
    holed[300] = np.nan
    before = np.isfinite(info_ratio_vec(holed, 222)).sum()
    after = np.isfinite(info_ratio_vec(ffill_gaps(holed), 222)).sum()
    clean = np.isfinite(info_ratio_vec(rel, 222)).sum()
    assert before < clean - 200, "test setup: the hole should have cost ~222 sessions"
    assert after == clean, f"gap fill should restore full coverage ({after} vs {clean})"
