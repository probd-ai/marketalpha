from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Fold:
    train_start: date
    train_end: date
    test_start: date
    test_end: date

    def __post_init__(self):
        if self.test_start <= self.train_end:
            raise ValueError(
                f"leakage: test_start {self.test_start} <= train_end {self.train_end}")


def make_folds(data_start: date, data_end: date,
               train_years: int = 3, test_years: int = 1, step_years: int = 1) -> list[Fold]:
    """Rolling walk-forward folds. Train spans `train_years`, the test window is the
    next `test_years`, and the whole window slides forward by `step_years`. The last
    test window is clipped to `data_end`. Each Fold validates test_start > train_end
    on construction (leakage guard)."""
    folds: list[Fold] = []
    train_start = data_start
    while True:
        train_end = date(train_start.year + train_years, 1, 1) - timedelta(days=1)
        test_start = date(train_start.year + train_years, 1, 1)
        if test_start > data_end:
            break
        test_end = min(date(train_start.year + train_years + test_years, 1, 1) - timedelta(days=1), data_end)
        folds.append(Fold(train_start, train_end, test_start, test_end))
        train_start = date(train_start.year + step_years, 1, 1)
    return folds
