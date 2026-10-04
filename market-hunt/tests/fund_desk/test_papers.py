"""AI Broadcast papers: validation and lifecycle (spec docs/specs/2026-10-04-ai-broadcast.md)."""
import pytest

from fund import papers


def _paper(**kw):
    p = {"title": "Bring equity in sync", "kind": "trade-plan", "summary": "Sell 8, buy 14.",
         "sections": [{"heading": "Sell", "body": "Sells first.",
                       "note": {"tone": "warn", "text": "Prices are the 10-01 close."},
                       "table": {"columns": ["Symbol", "Qty"], "rows": [["DIVISLAB", 16]], "align": ["left", "right"],
                                 "total": ["Total", 16]}}]}
    p.update(kw)
    return p


def test_a_good_paper_validates():
    assert papers.validate(_paper()) == []


@pytest.mark.parametrize("patch, msg", [
    ({"title": ""}, "title"), ({"title": "x" * 121}, "title"), ({"kind": "rumour"}, "kind"),
    ({"summary": "x" * 1001}, "summary"), ({"sections": []}, "sections"), ({"sections": [{}] * 31}, "sections"),
    ({"sections": [{"heading": ""}]}, "heading"), ({"sections": [{"heading": "h", "body": "x" * 8001}]}, "body"),
    ({"sections": [{"heading": "h", "note": {"tone": "loud", "text": "x"}}]}, "tone"),
    ({"sections": [{"heading": "h", "table": {"columns": ["a", "b"], "rows": [["only one"]]}}]}, "row"),
    ({"sections": [{"heading": "h", "table": {"columns": [str(i) for i in range(13)], "rows": []}}]}, "columns"),
    ({"sections": [{"heading": "h", "table": {"columns": ["a"], "rows": [[{"x": 1}]]}}]}, "cell"),
    ({"sections": [{"heading": "h", "table": {"columns": ["a"], "rows": [["x" * 201]]}}]}, "cell"),
    ({"sections": [{"heading": "h", "table": {"columns": ["a"], "rows": [], "align": ["middle"]}}]}, "align"),
    ({"author": "someone"}, "unknown field"),
])
def test_bad_papers_are_refused(patch, msg):
    errs = papers.validate(_paper(**patch))
    assert errs and any(msg in e for e in errs), errs


class FakeDB(dict):
    def __missing__(self, k):
        self[k] = Coll()
        return self[k]

    def __getattr__(self, k):            # pymongo style: db.fund_papers
        return self[k]


class Coll:
    def __init__(self): self.docs = {}
    def insert_one(self, d): self.docs[d["_id"]] = dict(d)
    def find_one(self, q, proj=None):
        d = self.docs.get(q["_id"])
        return dict(d) if d else None
    def update_one(self, q, u):
        d = self.docs.get(q["_id"])
        if d and all(d.get(k) == v for k, v in q.items()):
            d.update(u["$set"])
            return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()
    def find(self, q=None, proj=None):
        return Cursor([dict(d) for d in self.docs.values()])


class Cursor(list):
    def sort(self, key, direction): return Cursor(sorted(self, key=lambda d: d[key], reverse=direction < 0))


class FakeStore:
    def __init__(self):
        self.db = FakeDB()
        self.events = []
    def journal(self, ev, d): self.events.append((ev, d))
    def get_run(self, rid): return {"_id": rid} if rid == "R1" else None


def test_publish_list_get_archive():
    st = FakeStore()
    p = papers.publish(st, _paper(run_id="R1"))
    assert p["_id"].startswith("P") and p["author"] == "Claude" and p["status"] == "PUBLISHED"
    listed = papers.list_papers(st)
    assert [x["_id"] for x in listed] == [p["_id"]] and "sections" not in listed[0]
    assert papers.get_paper(st, p["_id"])["sections"][0]["heading"] == "Sell"
    papers.archive(st, p["_id"])
    assert papers.get_paper(st, p["_id"])["status"] == "ARCHIVED"
    assert [e for e, _ in st.events] == ["PAPER_PUBLISHED", "PAPER_ARCHIVED"]
    with pytest.raises(papers.PaperRefused, match="already archived"):
        papers.archive(st, p["_id"])


def test_publish_refuses_bad_content_and_unknown_runs():
    st = FakeStore()
    with pytest.raises(papers.PaperRefused, match="title"):
        papers.publish(st, _paper(title=""))
    with pytest.raises(papers.PaperRefused, match="run"):
        papers.publish(st, _paper(run_id="R404"))
    assert st.db["fund_papers"].docs == {} and st.events == []


def test_api_round_trip_and_desk_header(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.routers.fund as fr
    st = FakeStore()
    monkeypatch.setattr(fr, "_store", lambda: st)
    a = FastAPI()
    a.include_router(fr.router)
    c = TestClient(a, client=("127.0.0.1", 50000))
    assert c.post("/api/fund/papers", json=_paper()).status_code == 403            # no desk header
    h = {"X-Fund-Desk": "1"}
    assert c.post("/api/fund/papers", json=_paper(title=""), headers=h).status_code == 409
    pid = c.post("/api/fund/papers", json=_paper(), headers=h).json()["_id"]
    assert c.get("/api/fund/papers").json()[0]["_id"] == pid
    assert c.get(f"/api/fund/papers/{pid}").json()["sections"][0]["table"]["rows"] == [["DIVISLAB", 16]]
    assert c.get("/api/fund/papers/nope").status_code == 404
    assert c.post(f"/api/fund/papers/{pid}/archive", headers=h).json()["status"] == "ARCHIVED"
