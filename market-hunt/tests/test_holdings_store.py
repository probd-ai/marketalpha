"""The sync-state machine and file discovery for the holdings bookkeeper.

`sync_state` decides what warning the user sees, so its ORDER matters more than any
individual branch: the dangerous failure is reporting "in sync" from a file that
predates the rebalance, where the comparison is meaningless rather than reassuring.
Each test below pins one state against a condition that could mask it.
"""
import datetime as dt
import os

import pytest

from holdings_store import newest_export, sync_state

FP = "acct1fingerp"


def snap(**kw):
    base = {"account_fingerprint": FP, "file_as_of": "2026-08-30T19:18:00",
            "file_mtime": "2026-08-30T19:20:00", "id": "snap_1"}
    base.update(kw)
    return base


class TestSyncStateSeverityOrder:
    def test_no_file_beats_everything(self):
        assert sync_state(snapshot=None, lock=None, last_rebalance="2026-09-01",
                          gaps=[{"x": 1}], unresolved=["a"],
                          expected_fingerprint=FP)["state"] == "NO_FILE"

    def test_wrong_account_beats_unresolved_and_gaps(self):
        s = sync_state(snapshot=snap(account_fingerprint="deadbeef0000"), lock=None,
                       last_rebalance=None, gaps=[{"x": 1}], unresolved=["a"],
                       expected_fingerprint=FP)
        assert s["state"] == "WRONG_ACCOUNT"

    def test_unresolved_blocks_before_any_gap_is_reported(self):
        """Unresolved names mean the quantity picture is incomplete, so gaps computed
        from it would be wrong -- it must block rather than report a partial answer."""
        s = sync_state(snapshot=snap(), lock=None, last_rebalance=None,
                       gaps=[{"x": 1}], unresolved=["Great Eastern Shipping Company"],
                       expected_fingerprint=FP)
        assert s["state"] == "UNRESOLVED"
        assert s["unresolved"] == ["Great Eastern Shipping Company"]

    def test_stale_file_never_reports_in_sync(self):
        """The whole point: a file older than the last rebalance cannot be judged."""
        s = sync_state(snapshot=snap(file_as_of="2026-08-30T19:18:00"), lock={"x": 1},
                       last_rebalance="2026-09-16", gaps=[], unresolved=[],
                       expected_fingerprint=FP)
        assert s["state"] == "REBALANCE_DUE"

    def test_fresh_file_after_rebalance_is_judged_normally(self):
        s = sync_state(snapshot=snap(file_as_of="2026-09-17T10:00:00"),
                       lock={"locked_at": "x"}, last_rebalance="2026-09-16",
                       gaps=[], unresolved=[], expected_fingerprint=FP)
        assert s["state"] == "LOCKED_CLEAN"


class TestSyncStateOutcomes:
    def test_unlocked_when_no_baseline_yet(self):
        s = sync_state(snapshot=snap(), lock=None, last_rebalance=None, gaps=[],
                       unresolved=[], expected_fingerprint=FP)
        assert s["state"] == "UNLOCKED"

    def test_drift_reported_against_the_locked_baseline(self):
        s = sync_state(snapshot=snap(), lock={"locked_at": "2026-08-30T20:00:00"},
                       last_rebalance=None, gaps=[{"symbol": "AAA"}], unresolved=[],
                       expected_fingerprint=FP)
        assert s["state"] == "LOCKED_DRIFT"
        assert s["locked_at"] == "2026-08-30T20:00:00"

    def test_clean_when_locked_and_no_gaps(self):
        s = sync_state(snapshot=snap(), lock={"locked_at": "t"}, last_rebalance=None,
                       gaps=[], unresolved=[], expected_fingerprint=FP)
        assert s["state"] == "LOCKED_CLEAN"

    def test_missing_fingerprint_does_not_falsely_trip_wrong_account(self):
        """Kite exports carry no account identity, so the guard must stay silent
        rather than block every Kite file."""
        s = sync_state(snapshot=snap(account_fingerprint=None), lock=None,
                       last_rebalance=None, gaps=[], unresolved=[],
                       expected_fingerprint=FP)
        assert s["state"] == "UNLOCKED"


class TestNewestExport:
    def test_none_when_folder_missing_or_empty(self, tmp_path):
        assert newest_export(str(tmp_path / "nope")) is None
        assert newest_export(str(tmp_path)) is None
        assert newest_export("") is None

    def test_picks_newest_by_mtime_not_by_name(self, tmp_path):
        """Dhan embeds the date in the filename, so it changes every download and
        alphabetical order is meaningless. Windows' ' (1)' duplicates make name-based
        selection outright dangerous -- those were two different account holders."""
        old = tmp_path / "Dhan_Portfolio_EQ_30-08-2026.csv"
        new = tmp_path / "Dhan_Portfolio_EQ_30-08-2026 (1).csv"
        old.write_text("old")
        new.write_text("new")
        os.utime(old, (1_000_000, 1_000_000))
        os.utime(new, (2_000_000, 2_000_000))
        picked, mtime = newest_export(str(tmp_path))
        assert os.path.basename(picked) == "Dhan_Portfolio_EQ_30-08-2026 (1).csv"
        assert isinstance(mtime, dt.datetime)

    def test_ignores_non_csv(self, tmp_path):
        (tmp_path / "notes.txt").write_text("x")
        assert newest_export(str(tmp_path)) is None
