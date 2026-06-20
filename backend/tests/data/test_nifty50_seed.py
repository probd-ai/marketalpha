from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from app.data.universe.provider import load_provider_from_seed

SEED = Path(__file__).resolve().parents[2] / "data" / "seed"


def test_nifty50_current_is_populated():
    rows = pd.read_csv(SEED / "nifty50_current.csv")["symbol"].dropna()
    assert len(rows) >= 45  # ~50 constituents


def test_changelog_has_no_unverified_rows():
    df = pd.read_csv(SEED / "nifty50_changelog.csv")
    assert (df["source_url"].astype(str).str.upper() != "VERIFY").all()
    assert df["source_url"].astype(str).str.startswith("http").all()


def test_added_stock_is_absent_before_and_present_after():
    # Verified row: Shriram Finance replaced UPL in NIFTY 50 effective 2024-03-28.
    # Source: https://www.niftyindices.com/Press_Release/ind_prs28022024.pdf
    ADDED_SYMBOL = "SHRIRAMFIN"
    EVENT_DATE = date(2024, 3, 28)
    provider = load_provider_from_seed(SEED, anchor=date(2026, 6, 20))
    before = provider.members_as_of("NIFTY50", EVENT_DATE - timedelta(days=1))
    after = provider.members_as_of("NIFTY50", EVENT_DATE + timedelta(days=1))
    assert ADDED_SYMBOL not in before
    assert ADDED_SYMBOL in after
