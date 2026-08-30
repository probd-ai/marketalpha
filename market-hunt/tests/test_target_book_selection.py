"""Which cached run supplies the reconciliation target.

Regression: `_target_book` sorted `portfolio_runs` by `computed_at`, which is null on
some run documents. Mongo sorts nulls first descending, so a stale run won and every
gap was computed against the wrong book — silently, and looking perfectly healthy.
LargeCaps showed 3/13 overlap with its live account instead of the true 7/12.

`/api/portfolio/{id}/last-run` sorts by `created_at`. These must agree, or the page
and the reconciliation disagree about what the portfolio holds.
"""
import datetime as _dt

import pytest

from app.routers.holdings import _target_book


class _Coll:
    """Just enough Mongo for `find_one(..., sort=[(key, -1)])`.

    Sorts with None last on a descending sort, matching what the real driver does
    for the fields under test — the point is that a null key must not win.
    """

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def insert_one(self, d):
        self.docs.append(d)

    def insert_many(self, ds):
        self.docs.extend(ds)

    def find_one(self, query=None, projection=None, sort=None):
        rows = [d for d in self.docs
                if all(d.get(k) == v for k, v in (query or {}).items())]
        if sort:
            key, direction = sort[0]
            rows.sort(key=lambda d: (d.get(key) is None, d.get(key) or ""),
                      reverse=direction < 0)
            rows = [r for r in rows if r.get(key) is not None] or rows
        return rows[0] if rows else None


class _DB:
    def __init__(self):
        self.portfolio_runs = _Coll()
        self.indicators = _Coll()

    def __getattr__(self, name):
        raise AttributeError(name)


def _run(pid, created_at, book, *, computed_at=None, data_version="2026-08-28",
         end_key="latest:x", history=None):
    return {"portfolio_id": pid, "created_at": created_at, "computed_at": computed_at,
            "data_version": data_version, "end_key": end_key, "config_hash": "h",
            "result": {"current_book": [{"symbol": s, "quantity": q} for s, q in book.items()],
                       "rebalance_history": history or []}}


@pytest.fixture
def db():
    d = _DB()
    d.indicators.insert_one({"indicator_type": "truevx", "date": _dt.datetime(2026, 8, 28)})
    return d


class TestRunSelection:
    def test_newest_by_created_at_wins(self, db):
        db.portfolio_runs.insert_many([
            _run("p1", "2026-08-01T10:00:00", {"OLD": 5}),
            _run("p1", "2026-08-29T19:00:00", {"NEW": 7}),
        ])
        target, _last, _meta = _target_book(db, "p1")
        assert target == {"NEW": 7}

    def test_null_computed_at_cannot_win(self, db):
        """The actual bug: a run with computed_at=None outranked the real newest."""
        db.portfolio_runs.insert_many([
            _run("p1", "2026-08-29T19:00:00", {"CORRECT": 1}, computed_at="2026-08-29"),
            _run("p1", "2026-07-01T10:00:00", {"STALE": 1}, computed_at=None),
        ])
        target, _l, _m = _target_book(db, "p1")
        assert target == {"CORRECT": 1}

    def test_no_run_is_not_an_error(self, db):
        target, last, meta = _target_book(db, "missing")
        assert target == {} and last is None and meta["has_run"] is False


class TestTargetShape:
    def test_quantities_rounded_to_whole_shares(self, db):
        """The simulated book divides capital by price, so it holds 37.0813 shares;
        a real book holds 37. Without rounding every position shows a phantom gap."""
        db.portfolio_runs.insert_one(_run("p1", "2026-08-29", {"AAA": 37.0813, "BBB": 12.5}))
        target, _l, _m = _target_book(db, "p1")
        assert target == {"AAA": 37, "BBB": 12}

    def test_duplicate_symbols_across_sleeves_are_summed(self, db):
        db.portfolio_runs.insert_one({
            "portfolio_id": "p1", "created_at": "2026-08-29", "data_version": "2026-08-28",
            "end_key": "latest:x",
            "result": {"current_book": [{"symbol": "AAA", "quantity": 10},
                                        {"symbol": "AAA", "quantity": 5}],
                       "rebalance_history": []}})
        target, _l, _m = _target_book(db, "p1")
        assert target == {"AAA": 15}

    def test_last_rebalance_is_the_latest_history_date(self, db):
        db.portfolio_runs.insert_one(_run("p1", "2026-08-29", {"A": 1}, history=[
            {"date": "2026-08-03"}, {"date": "2026-07-16"}, {"date": "2026-08-17"}]))
        _t, last, _m = _target_book(db, "p1")
        assert last == "2026-08-17"


class TestStaleness:
    def test_run_older_than_the_data_is_flagged(self, db):
        db.portfolio_runs.insert_one(_run("p1", "2026-08-29", {"A": 1},
                                          data_version="2026-08-14"))
        _t, _l, meta = _target_book(db, "p1")
        assert meta["stale"] is True

    def test_current_run_is_not_flagged(self, db):
        db.portfolio_runs.insert_one(_run("p1", "2026-08-29", {"A": 1},
                                          data_version="2026-08-28"))
        _t, _l, meta = _target_book(db, "p1")
        assert meta["stale"] is False

    def test_fixed_end_date_runs_are_never_stale(self, db):
        """A run pinned to an explicit end date is reproducible by design; only
        `latest:` runs go stale as new data arrives."""
        db.portfolio_runs.insert_one(_run("p1", "2026-08-29", {"A": 1},
                                          data_version="2026-01-01", end_key="2026-01-01"))
        _t, _l, meta = _target_book(db, "p1")
        assert meta["stale"] is False
