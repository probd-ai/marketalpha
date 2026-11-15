from datetime import date

import pandas as pd
import pytest

from fund import checks
from fund.engine.types import Order, Reason
from fund.ticket import build_ticket


def _sl(orders, before, after, scale=10.0, rf=None, ctx=None):
    return {"orders": orders, "scale": scale, "held_before": before, "held_after": after, "reset_factor": rf,
            "ctx": ctx or {}}


def test_scaling_rounding_and_sells_first():
    orders = [Order("AAA", "BUY", 1.26, 100.0, 126.0, Reason.NEW_LOWEST_22D),
              Order("BBB", "SELL", 2.0, 50.0, 99.0, Reason.GRID_DOWN_POSITIVE_MONTH)]
    lines, res = build_ticket({"Value core": _sl(orders, {"BBB": 2.0}, {"AAA": 1.26}, ctx={"BBB": {"roc22": 4.1}})},
                              {"AAA": 100.0, "BBB": 50.0})
    assert [(l.side, l.symbol, l.qty) for l in lines] == [("SELL", "BBB", 20), ("BUY", "AAA", 12)]
    assert lines[0].full_exit and "+4.1%" in lines[0].reason_text
    assert lines[1].limit_px == pytest.approx(100.3)
    assert [l.line_id for l in lines] == ["L001", "L002"]


def test_reset_nets_with_weekly_orders_and_tiny_nets_are_residual():
    orders = [Order("AAA", "BUY", 0.5, 100.0, 50.0, Reason.TOPUP_TO_TARGET)]
    sl = _sl(orders, {"AAA": 10.0, "CCC": 0.01}, {"AAA": 10.5, "CCC": 0.01}, scale=1.0, rf=0.9)
    lines, res = build_ticket({"Growth": sl}, {"AAA": 100.0, "CCC": 10.0})
    # AAA: +0.5 weekly, then -10% of 10.5 = -1.05 -> net -0.55 -> floor 0 -> residual ; CCC tiny -> residual
    assert lines == [] and {r["symbol"] for r in res} == {"AAA", "CCC"}


def test_freshness_knows_market_holidays():
    gandhi = {date(2026, 10, 2)}
    # Thu 10-01 is the last session before Mon 10-05 when Fri 10-02 is a holiday
    assert checks.freshness(date(2026, 10, 1), date(2026, 10, 5), date(2026, 10, 1), gandhi).status == "PASS"
    assert checks.freshness(date(2026, 10, 1), date(2026, 10, 5), date(2026, 10, 1)).status == "WARN"   # no calendar


def test_calendar_check_warns_without_holidays_for_the_fill_year():
    assert checks.calendar(date(2026, 10, 5), {2026}).status == "PASS"
    c = checks.calendar(date(2027, 1, 4), {2026})
    assert c.status == "WARN" and "2027" in c.detail


def test_fill_session_skips_a_holiday():
    from fund.engine.projection import next_session
    assert next_session(pd.Timestamp("2026-10-01"), (date(2026, 10, 2),)) == pd.Timestamp("2026-10-05")


def test_check_verdicts():
    assert checks.freshness(date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 25)).status == "PASS"   # Fri->Mon
    assert checks.freshness(date(2026, 9, 21), date(2026, 9, 28), date(2026, 9, 21)).status == "BLOCK"
    assert checks.coverage("x", list("ABCDEFGHIJKLMNOPQRSTU"), ["A"], []).status == "WARN"
    assert checks.coverage("x", list("ABCD"), ["A"], ["B"]).status == "BLOCK"
    assert checks.caps("g", {"A": 0.1049}, 0.10).status == "PASS"
    assert checks.caps("g", {"A": 0.12}, 0.10).status == "BLOCK"
    assert checks.concentration("v", {"A": 0.3}).status == "WARN"
    assert checks.cash("v", -0.01).status == "BLOCK"
    assert checks.liquidity([{"symbol": "A", "value": 60.0}], {"A": 1000.0}).status == "WARN"
    assert checks.verdict([checks.Check("a", "PASS", ""), checks.Check("b", "WARN", "")]) == "WARN"


def test_initial_build_buys_every_holding():
    orders = [Order("AAA", "BUY", 3.0, 100.0, 300.0, Reason.INITIAL_BUILD),
              Order("BBB", "BUY", 1.5, 50.0, 75.0, Reason.INITIAL_BUILD)]
    lines, _ = build_ticket({"Value core": _sl(orders, {}, {"AAA": 3.0, "BBB": 1.5}, scale=10.0)},
                            {"AAA": 100.0, "BBB": 50.0})
    assert [(l.symbol, l.side, l.qty) for l in lines] == [("AAA", "BUY", 30), ("BBB", "BUY", 15)]
    assert "Initial build" in lines[0].reason_text


# ---------------------------------------------------------------- small trims: deferred, carried forward, emitted once large
def _trim(sym, model_qty, held_after, scale=1.0):
    from fund.engine.types import Order, Reason
    return {"orders": [Order(sym, "SELL", model_qty, 204.05, model_qty * 204.05, Reason.TRIM_ABOVE_CAP)],
            "scale": scale, "held_before": {sym: held_after + model_qty}, "held_after": {sym: held_after},
            "reset_factor": None}


def test_a_small_trim_is_deferred_not_ticketed():
    lines, res = build_ticket({"Satellite": _trim("EMIL", 4, 551)}, {"EMIL": 204.05}, min_trim=5000)
    assert lines == []
    (d,) = [r for r in res if r.get("deferred_trim")]
    assert (d["sleeve"], d["symbol"], d["model_shares"]) == ("Satellite", "EMIL", pytest.approx(-4))


def test_deferred_trims_accumulate_until_they_are_worth_trading():
    carried = {("Satellite", "EMIL"): -20.0}                             # earlier weeks' hidden trims
    lines, res = build_ticket({"Satellite": _trim("EMIL", 6, 545)}, {"EMIL": 204.05}, min_trim=5000, carried=carried)
    (l,) = lines
    assert (l.side, l.symbol, l.qty) == ("SELL", "EMIL", 26) and not [r for r in res if r.get("deferred_trim")]


def test_carried_trim_still_below_threshold_rolls_forward_alone():
    lines, res = build_ticket({"Satellite": {"orders": [], "scale": 1.0, "held_before": {"EMIL": 551},
                                             "held_after": {"EMIL": 551}, "reset_factor": None}},
                              {"EMIL": 204.05}, min_trim=5000, carried={("Satellite", "EMIL"): -4.0})
    assert lines == [] and [r["model_shares"] for r in res if r.get("deferred_trim")] == [pytest.approx(-4)]


def test_carried_trim_nets_against_a_buy():
    from fund.engine.types import Order, Reason
    sl = {"orders": [Order("EMIL", "BUY", 30, 204.05, 6121.5, Reason.TOP5_NEW_TRANCHE)], "scale": 1.0,
          "held_before": {"EMIL": 551}, "held_after": {"EMIL": 581}, "reset_factor": None}
    lines, _ = build_ticket({"Satellite": sl}, {"EMIL": 204.05}, min_trim=5000, carried={("Satellite", "EMIL"): -4.0})
    (l,) = lines
    assert (l.side, l.qty) == ("BUY", 26)


def test_a_full_exit_ignores_carried_trims_and_large_trims_still_trade():
    lines, _ = build_ticket({"Satellite": _trim("EMIL", 551, 0)}, {"EMIL": 204.05}, min_trim=5000,
                            carried={("Satellite", "EMIL"): -4.0})
    assert [(l.side, l.qty, l.full_exit) for l in lines] == [("SELL", 551, True)]
    lines, _ = build_ticket({"Satellite": _trim("EMIL", 40, 500)}, {"EMIL": 204.05}, min_trim=5000)
    assert [(l.side, l.qty) for l in lines] == [("SELL", 40)]


def test_min_trim_zero_keeps_every_trim():
    lines, _ = build_ticket({"Satellite": _trim("EMIL", 4, 551)}, {"EMIL": 204.05})
    assert [(l.side, l.qty) for l in lines] == [("SELL", 4)]


def test_min_trim_is_a_ticket_setting_not_a_new_model_version():
    from fund.config import default_config, next_version, validate
    old = default_config()
    assert next_version(old, {**old, "min_trim_value": 10000.0}) == old["version"]
    assert any("minimum trim" in e for e in validate({**old, "min_trim_value": -1}, date(2026, 10, 4)))
