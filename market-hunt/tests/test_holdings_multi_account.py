"""One portfolio funded by several broker accounts.

Alpha-Market is held across two Dhan accounts (two distinct account fingerprints, 15 and 12 positions). Reading only the newest file would reconcile
against 15 of 27 positions and report the other 12 as missing -- confidently wrong,
which is worse than refusing.

So the folder is aggregated, not picked from. Two hazards that follow:

* the SAME account exported twice (user drops a new file without deleting the old)
  must collapse to the newest, never double-count;
* files with no account identity (Kite states none) cannot be told apart -- two of
  them might be two accounts or one account twice, and guessing wrong doubles a
  quantity. That must block, not warn.

Staleness takes the OLDEST file: a book is only as current as its stalest part.
"""
import datetime as dt

import pytest

from holdings_store import FileSnap, aggregate, select_current


def fs(fp, qty_by_sym, *, mtime_day=1, as_of=None, content="x", name="f.csv"):
    """A parsed file stand-in: fingerprint, holdings, mtime, embedded timestamp."""
    return FileSnap(
        path=name,
        mtime=dt.datetime(2026, 8, mtime_day, 12, 0),
        fingerprint=fp,
        quantities=dict(qty_by_sym),
        file_as_of=as_of,
        content_hash=content,
        broker="dhan" if fp else "kite",
        unresolved=[],
    )


class TestSelectCurrent:
    def test_distinct_accounts_are_all_kept(self):
        a = fs("acc_a", {"AAA": 10})
        b = fs("acc_b", {"BBB": 5})
        chosen, dropped, ambiguous = select_current([a, b])
        assert len(chosen) == 2
        assert dropped == [] and ambiguous is False

    def test_same_account_twice_collapses_to_newest(self):
        """User drops a new export without deleting the old -- take the newest for
        that account rather than summing the same holdings twice."""
        old = fs("acc_a", {"AAA": 10}, mtime_day=1, content="old", name="old.csv")
        new = fs("acc_a", {"AAA": 12}, mtime_day=5, content="new", name="new.csv")
        chosen, dropped, ambiguous = select_current([old, new])
        assert len(chosen) == 1
        assert chosen[0].quantities == {"AAA": 12}
        assert dropped == ["old.csv"]
        assert ambiguous is False

    def test_identical_content_is_deduped_not_treated_as_two_accounts(self):
        a = fs(None, {"AAA": 10}, content="same", name="a.csv")
        b = fs(None, {"AAA": 10}, content="same", name="b.csv")
        chosen, _dropped, ambiguous = select_current([a, b])
        assert len(chosen) == 1
        assert ambiguous is False

    def test_two_unidentifiable_files_are_ambiguous(self):
        """Kite exports carry no account identity. Two different ones could be two
        accounts or one account twice; summing would double a quantity."""
        a = fs(None, {"AAA": 10}, content="one", name="a.csv")
        b = fs(None, {"BBB": 5}, content="two", name="b.csv")
        chosen, _dropped, ambiguous = select_current([a, b])
        assert ambiguous is True
        assert chosen == []

    def test_one_unidentifiable_file_is_fine(self):
        chosen, _dropped, ambiguous = select_current([fs(None, {"AAA": 10})])
        assert len(chosen) == 1 and ambiguous is False

    def test_identified_and_unidentified_can_coexist(self):
        """A Dhan account plus a single Kite account in one portfolio is workable."""
        chosen, _d, ambiguous = select_current(
            [fs("acc_a", {"AAA": 1}), fs(None, {"BBB": 2}, content="k")])
        assert len(chosen) == 2 and ambiguous is False


class TestAggregate:
    def test_quantities_sum_across_accounts(self):
        r = aggregate([fs("a", {"AAA": 10, "SHARED": 3}),
                       fs("b", {"BBB": 5, "SHARED": 7})])
        assert r["quantities"] == {"AAA": 10, "SHARED": 10, "BBB": 5}

    def test_fingerprints_collected_as_a_set(self):
        r = aggregate([fs("a", {"AAA": 1}), fs("b", {"BBB": 1})])
        assert sorted(r["fingerprints"]) == ["a", "b"]

    def test_staleness_uses_the_oldest_file(self):
        """A book is only as current as its stalest part."""
        r = aggregate([
            fs("a", {"AAA": 1}, as_of=dt.datetime(2026, 8, 30, 19, 22)),
            fs("b", {"BBB": 1}, as_of=dt.datetime(2026, 8, 25, 9, 0)),
        ])
        assert r["as_of"] == "2026-08-25T09:00:00"

    def test_falls_back_to_mtime_when_a_file_states_no_time(self):
        r = aggregate([fs("a", {"AAA": 1}, mtime_day=3, as_of=None)])
        assert r["as_of"].startswith("2026-08-03")
        assert r["as_of_source"] == "file_mtime"

    def test_broker_timestamp_preferred_when_all_present(self):
        r = aggregate([fs("a", {"AAA": 1}, as_of=dt.datetime(2026, 8, 30, 19, 18))])
        assert r["as_of_source"] == "broker"

    def test_sources_are_listed_for_display(self):
        r = aggregate([fs("a", {"AAA": 1}, name="one.csv"),
                       fs("b", {"BBB": 1}, name="two.csv")])
        assert sorted(s["file"] for s in r["sources"]) == ["one.csv", "two.csv"]

    def test_empty_input(self):
        r = aggregate([])
        assert r["quantities"] == {} and r["fingerprints"] == []


class TestSnapshotIdentity:
    """The snapshot hash must change when RESOLUTION changes, not only the bytes.

    Regression: confirming an alias turns an unresolved name into a real symbol
    without touching the file. Hashing file content alone meant the cached snapshot
    kept claiming 0 holdings / 27 unresolved, which `/lock` then refused — while
    `/status`, which recomputes, showed the correct 39 gaps. The two disagreed.
    """

    def test_hash_changes_when_names_resolve(self):
        unresolved = FileSnap(path="a.csv", mtime=dt.datetime(2026, 8, 30),
                              fingerprint="acc", quantities={}, file_as_of=None,
                              content_hash="same", broker="dhan",
                              unresolved=["Great Eastern Shipping Company"])
        resolved = FileSnap(path="a.csv", mtime=dt.datetime(2026, 8, 30),
                            fingerprint="acc", quantities={"GESHIP": 27},
                            file_as_of=None, content_hash="same", broker="dhan",
                            unresolved=[])
        assert aggregate([unresolved])["content_hash"] != aggregate([resolved])["content_hash"]

    def test_hash_stable_when_nothing_changed(self):
        s = FileSnap(path="a.csv", mtime=dt.datetime(2026, 8, 30), fingerprint="acc",
                     quantities={"AAA": 10}, file_as_of=None, content_hash="same",
                     broker="dhan", unresolved=[])
        assert aggregate([s])["content_hash"] == aggregate([s])["content_hash"]

    def test_hash_changes_when_a_quantity_changes(self):
        def snap(q):
            return FileSnap(path="a.csv", mtime=dt.datetime(2026, 8, 30), fingerprint="acc",
                            quantities={"AAA": q}, file_as_of=None, content_hash="same",
                            broker="dhan", unresolved=[])
        assert aggregate([snap(10)])["content_hash"] != aggregate([snap(11)])["content_hash"]
