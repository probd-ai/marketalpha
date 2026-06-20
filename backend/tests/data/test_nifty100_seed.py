from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from app.data.universe.provider import load_provider_from_seed

SEED = Path(__file__).resolve().parents[2] / "data" / "seed"


def test_nifty100_current_is_populated():
    rows = pd.read_csv(SEED / "nifty100_current.csv")["symbol"].dropna()
    assert len(rows) >= 90  # ~100 constituents


def test_changelog_has_no_unverified_rows():
    df = pd.read_csv(SEED / "nifty100_changelog.csv")
    assert (df["source_url"].astype(str).str.upper() != "VERIFY").all()
    assert df["source_url"].astype(str).str.startswith("http").all()


def test_added_stock_is_absent_before_and_present_after():
    # Verified row: TVS Motor Company was included in NIFTY 100 effective 2023-09-29
    # (semi-annual review announced 2023-08-17). Verified directly from the official PDF.
    # Source: https://www.niftyindices.com/Press_Release/ind_prs17082023.pdf
    ADDED_SYMBOL = "TVSMOTOR"
    EVENT_DATE = date(2023, 9, 29)
    provider = load_provider_from_seed(SEED, anchor=date(2026, 6, 20))
    before = provider.members_as_of("NIFTY100", EVENT_DATE - timedelta(days=1))
    after = provider.members_as_of("NIFTY100", EVENT_DATE + timedelta(days=1))
    assert ADDED_SYMBOL not in before
    assert ADDED_SYMBOL in after
