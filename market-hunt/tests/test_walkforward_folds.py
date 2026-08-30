from datetime import date
import pytest
from walkforward.folds import make_folds, Fold


def test_locked_scheme_produces_six_folds_with_exact_dates():
    folds = make_folds(date(2018, 1, 1), date(2026, 6, 30))
    assert len(folds) == 6
    assert folds[0] == Fold(date(2018, 1, 1), date(2020, 12, 31), date(2021, 1, 1), date(2021, 12, 31))
    assert folds[5] == Fold(date(2023, 1, 1), date(2025, 12, 31), date(2026, 1, 1), date(2026, 6, 30))


def test_every_test_window_starts_after_its_train_window():
    for f in make_folds(date(2018, 1, 1), date(2026, 6, 30)):
        assert f.test_start > f.train_end


def test_step_advances_by_one_year():
    folds = make_folds(date(2018, 1, 1), date(2026, 6, 30))
    assert folds[1].train_start == date(2019, 1, 1)
    assert folds[1].test_start == date(2022, 1, 1)


def test_fold_rejects_leaky_dates():
    from datetime import date
    with pytest.raises(ValueError):
        Fold(date(2020, 1, 1), date(2021, 1, 1), date(2020, 12, 31), date(2021, 12, 31))
