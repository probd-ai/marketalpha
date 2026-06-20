from datetime import date
from pathlib import Path
from typing import Protocol

import pandas as pd

from app.data.universe.membership import (
    ChangeEvent,
    MembershipInterval,
    build_membership_intervals,
)


class UniverseProvider(Protocol):
    def members_as_of(self, index: str, on: date) -> set[str]: ...
    def union_over(self, index: str, start: date, end: date) -> set[str]: ...


def _covers(iv: MembershipInterval, d: date) -> bool:
    if iv.effective_from is not None and iv.effective_from > d:
        return False
    if iv.effective_to is not None and d >= iv.effective_to:
        return False
    return True


def _overlaps(iv: MembershipInterval, start: date, end: date) -> bool:
    if iv.effective_to is not None and iv.effective_to <= start:
        return False
    if iv.effective_from is not None and iv.effective_from > end:
        return False
    return True


class IndexMembershipProvider:
    def __init__(self, intervals_by_index: dict[str, list[MembershipInterval]]) -> None:
        self._by_index = intervals_by_index

    def members_as_of(self, index: str, on: date) -> set[str]:
        return {iv.symbol for iv in self._by_index[index] if _covers(iv, on)}

    def union_over(self, index: str, start: date, end: date) -> set[str]:
        return {iv.symbol for iv in self._by_index[index] if _overlaps(iv, start, end)}


def _parse_symbols(cell: object) -> frozenset[str]:
    if cell is None or (isinstance(cell, float) and pd.isna(cell)) or str(cell).strip() == "":
        return frozenset()
    return frozenset(s.strip() for s in str(cell).split(";") if s.strip())


def load_provider_from_seed(seed_dir: Path, anchor: date) -> IndexMembershipProvider:
    seed_dir = Path(seed_dir)
    by_index: dict[str, list[MembershipInterval]] = {}
    for current_csv in seed_dir.glob("*_current.csv"):
        prefix = current_csv.name[: -len("_current.csv")]
        index_key = prefix.upper()
        raw_symbols = pd.read_csv(current_csv)["symbol"].dropna().astype(str).str.strip()
        current = {s for s in raw_symbols if s and s.lower() != "nan"}
        changelog: list[ChangeEvent] = []
        log_csv = seed_dir / f"{prefix}_changelog.csv"
        if log_csv.exists():
            df = pd.read_csv(log_csv)
            for _, row in df.iterrows():
                changelog.append(ChangeEvent(
                    on=pd.to_datetime(row["effective_date"]).date(),
                    added=_parse_symbols(row.get("added")),
                    removed=_parse_symbols(row.get("removed")),
                ))
        by_index[index_key] = build_membership_intervals(current, changelog, anchor)
    return IndexMembershipProvider(by_index)


class BiasedUniverseProvider:
    """Survivorship-biased provider: returns today's constituents at every date."""

    def __init__(self, members_by_index: dict[str, set[str]]) -> None:
        self._by_index = {k: set(v) for k, v in members_by_index.items()}

    def members_as_of(self, index: str, on: date) -> set[str]:
        return set(self._by_index.get(index, set()))

    def union_over(self, index: str, start: date, end: date) -> set[str]:
        return set(self._by_index.get(index, set()))


def load_biased_provider_from_seed(seed_dir: Path) -> BiasedUniverseProvider:
    seed_dir = Path(seed_dir)
    by_index: dict[str, set[str]] = {}
    for current_csv in seed_dir.glob("*_current.csv"):
        index_key = current_csv.name[: -len("_current.csv")].upper()
        raw = pd.read_csv(current_csv)["symbol"].dropna().astype(str).str.strip()
        by_index[index_key] = {s for s in raw if s and s.lower() != "nan"}
    return BiasedUniverseProvider(by_index)
