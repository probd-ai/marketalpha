"""Portfolio run-job state must survive process restarts and multiple workers:
it lives in Mongo (portfolio_run_jobs), not a process-local dict."""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def jobs_coll():
    from app.db import mongo_conn
    if mongo_conn.db is None:
        mongo_conn.connect()
    coll = mongo_conn.db.portfolio_run_jobs
    # Own the whole collection: it is a transient status cache (capped at 50 by
    # prune, regenerated on every run request), and the prune test asserts a
    # GLOBAL cap — leftover real jobs would skew the count.
    coll.delete_many({})
    yield coll
    coll.delete_many({"_id": {"$regex": "^testjob_"}})


def test_set_get_roundtrip(jobs_coll):
    from app.routers.portfolio import _job_get, _job_set
    _job_set("testjob_1", status="running", result=None, error=None,
             created_at="2026-07-10T00:00:00")
    job = _job_get("testjob_1")
    assert job["status"] == "running"
    assert job["result"] is None
    assert job["error"] is None


def test_update_transition_preserves_created_at(jobs_coll):
    from app.routers.portfolio import _job_get, _job_set
    _job_set("testjob_2", status="running", result=None, error=None,
             created_at="2026-07-10T00:00:00")
    _job_set("testjob_2", status="completed", result={"total": 1.23})
    job = _job_get("testjob_2")
    assert job["status"] == "completed"
    assert job["result"] == {"total": 1.23}
    assert job["created_at"] == "2026-07-10T00:00:00"  # not clobbered by update


def test_get_unknown_returns_none(jobs_coll):
    from app.routers.portfolio import _job_get
    assert _job_get("testjob_never_created") is None


def test_prune_keeps_newest(jobs_coll):
    from app.routers.portfolio import _job_get, _job_set, _prune_run_jobs
    for i in range(60):
        _job_set(f"testjob_p{i:03d}", status="completed", result=None, error=None,
                 created_at=f"2026-07-10T00:00:{i:02d}")
    _prune_run_jobs(max_jobs=50)
    assert _job_get("testjob_p000") is None          # oldest pruned
    assert _job_get("testjob_p059") is not None      # newest kept
    remaining = jobs_coll.count_documents({"_id": {"$regex": "^testjob_p"}})
    assert remaining == 50


def test_run_status_survives_new_process_view(jobs_coll):
    """Simulates a second worker: a fresh read path (straight from Mongo) sees the job."""
    from app.routers.portfolio import _job_set
    _job_set("testjob_worker2", status="completed", result={"ok": True}, error=None,
             created_at="2026-07-10T00:00:00")
    doc = jobs_coll.find_one({"_id": "testjob_worker2"})
    assert doc["status"] == "completed"
    assert doc["result"] == {"ok": True}
