"""Security review findings for the fund desk API (2026-09-27): each test pins one fix."""
import math
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fund import service
from fund.config import default_config, validate

LOCAL = ("127.0.0.1", 50000)


# ---------------------------------------------------------------- #2 CSRF: state changes need the desk header + an allowed origin
@pytest.fixture
def client(monkeypatch):
    import app.routers.fund as fr

    class Stub:
        def get_run(self, run_id):
            return None
    monkeypatch.setattr(fr, "_store", lambda: Stub())
    a = FastAPI()
    a.include_router(fr.router)
    return TestClient(a, client=LOCAL)


def test_post_without_desk_header_is_refused(client):
    r = client.post("/api/fund/runs/R1/discard")
    assert r.status_code == 403 and "X-Fund-Desk" in r.json()["detail"]


def test_post_from_foreign_origin_is_refused(client):
    r = client.post("/api/fund/runs/R1/discard", headers={"X-Fund-Desk": "1", "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_post_with_header_and_allowed_origin_reaches_handler(client):
    r = client.post("/api/fund/runs/R1/discard", headers={"X-Fund-Desk": "1", "Origin": "http://localhost:3300"})
    assert r.status_code == 409          # handler ran: the stub store has no such draft


def test_get_needs_no_desk_header(client, monkeypatch):
    r = client.get("/api/fund/runs/R1")
    assert r.status_code == 404


def test_remote_host_is_refused(monkeypatch):
    import app.routers.fund as fr
    a = FastAPI()
    a.include_router(fr.router)
    r = TestClient(a, client=("192.168.1.50", 5)).get("/api/fund/runs/R1")
    assert r.status_code == 403


# ---------------------------------------------------------------- #3 config input: types, bounds, allow-lists (no operator injection)
def _cfg(**kw):
    c = default_config()
    c.update(kw)
    return c


UNIS = {"NIFTY50", "NIFTY200", "NIFTY500MICRO750"}
SYMS = {"GOLDBEES", "MON100"}


def test_default_config_is_valid():
    assert validate(default_config(), date(2026, 9, 27), UNIS, SYMS) == []


@pytest.mark.parametrize("patch, msg", [
    ({"universe": {"$ne": ""}}, "universe"),
    ({"universe": "NIFTYFAKE"}, "universe"),
    ({"params": {"n": 0}}, "n must be"),
    ({"params": {"n": 10, "x": 1}}, "unknown parameter"),
])
def test_bad_grid_sleeve_rejected(patch, msg):
    c = default_config()
    c["sleeves"][0] = {**c["sleeves"][0], **patch}
    assert any(msg in e for e in validate(c, date(2026, 9, 27), UNIS, SYMS))


def test_bad_tranche_and_asset_rejected():
    c = default_config()
    c["sleeves"][3]["params"] = {"top": 5, "weeks": 8, "slot_div": 40, "max_w": 1.5}
    c["sleeves"][1]["symbol"] = "NOTANETF"
    errs = validate(c, date(2026, 9, 27), UNIS, SYMS)
    assert any("max_w" in e for e in errs) and any("NOTANETF" in e for e in errs)


@pytest.mark.parametrize("patch", [{"capital": float("inf")}, {"capital": "45L"}, {"slippage_bp": 500},
                                   {"limit_band": 0.5}, {"reset_cost": -0.1}, {"name": "x" * 200},
                                   {"backfill_weekday": 5}])
def test_bad_fund_fields_rejected(patch):
    assert validate(_cfg(**patch), date(2026, 9, 27), UNIS, SYMS)


def test_unknown_sleeve_keys_rejected():
    c = default_config()
    c["sleeves"][0]["_id"] = "x"
    assert any("unknown field" in e for e in validate(c, date(2026, 9, 27), UNIS, SYMS))


# ---------------------------------------------------------------- #4 execution records: quantities and prices are checked
class FakeStore:
    def __init__(self, status="PUBLISHED"):
        self.run = {"_id": "R1", "status": status, "ticket": [
            {"line_id": "L001", "sleeve": "Value core", "symbol": "AAA", "side": "BUY", "qty": 10, "ref_px": 100.0}]}
        self.recs, self.events = {}, []

    def get_run(self, rid): return self.run if rid == "R1" else None
    def record_execution(self, rid, rec): self.recs[rec["line_id"]] = rec
    def executions(self, rid=None): return list(self.recs.values())
    def set_status(self, rid, st, **kw): self.run["status"] = st
    def journal(self, ev, d): self.events.append(ev)


def test_done_always_records_the_ticket_quantity():
    st = FakeStore()
    service.record_executions(st, "R1", [{"line_id": "L001", "status": "done", "qty": -5, "price": 100.5}])
    assert st.recs["L001"]["qty"] == 10 and st.run["status"] == "CLOSED"


@pytest.mark.parametrize("item, msg", [
    ({"status": "done", "price": 0}, "fill price"),
    ({"status": "done", "price": math.nan}, "fill price"),
    ({"status": "done", "price": math.inf}, "fill price"),
    ({"status": "done", "price": 1000.0}, "20%"),              # typo guard: 10x the reference price
    ({"status": "partial", "qty": 10, "price": 100.0}, "partial"),
    ({"status": "partial", "qty": 0, "price": 100.0}, "partial"),
    ({"status": "filled", "price": 100.0}, "status"),
])
def test_bad_execution_rejected(item, msg):
    with pytest.raises(service.RunRefused, match=msg):
        service.record_executions(FakeStore(), "R1", [{"line_id": "L001", **item}])


def test_duplicate_lines_in_one_request_rejected():
    with pytest.raises(service.RunRefused, match="more than once"):
        service.record_executions(FakeStore(), "R1", [{"line_id": "L001", "status": "skipped"},
                                                      {"line_id": "L001", "status": "done", "price": 100.0}])


def test_executions_need_a_published_run():
    with pytest.raises(service.RunRefused, match="published"):
        service.record_executions(FakeStore("DRAFT"), "R1", [{"line_id": "L001", "status": "skipped"}])


# ---------------------------------------------------------------- #6 CSV export: escaped, no spreadsheet formula injection
def test_csv_escapes_and_neutralises_formulas():
    from app.routers.fund import ticket_csv
    out = ticket_csv([{"line_id": "L001", "sleeve": "=cmd|' /C calc'!A0", "symbol": "M&M", "side": "BUY", "qty": 1,
                       "ref_px": 1.0, "limit_px": 1.0, "value": 1.0, "full_exit": False,
                       "reason_text": 'has "quotes", commas'}])
    row = out.splitlines()[1]
    assert "'=cmd" in row and '"has ""quotes"", commas"' in row


@pytest.mark.parametrize("patch", [{"rebalance_months": []}, {"rebalance_months": [13]}, {"rebalance_months": [1, 1]},
                                   {"rebalance_months": "1,7"}, {"rebalance_band": 0.9}, {"rebalance_band": "5"}])
def test_bad_rebalance_settings_rejected(patch):
    assert validate(_cfg(**patch), date(2026, 9, 27), UNIS, SYMS)


def test_rebalance_band_can_be_off():
    assert validate(_cfg(rebalance_band=None), date(2026, 9, 27), UNIS, SYMS) == []
