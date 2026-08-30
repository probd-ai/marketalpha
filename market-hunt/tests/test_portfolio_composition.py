from portfolio_models import Sleeve, Portfolio
from portfolio_composition import build_rebalance_plan, validate_portfolio


def test_portfolio_model_defaults_and_serialization():
    p = Portfolio(
        name="Growth Book",
        inception_date="2025-06-21",
        sleeves=[Sleeve(id="sleeve_1", label="Mid", strategy_id="strategy_1",
                        weight=100, universe="NIFTYMIDCAP150")],
    )
    assert p.total_capital == 100000
    assert p.point_in_time_membership is True
    assert p.benchmark_symbol == "Nifty 50"
    s = p.sleeves[0]
    assert s.rebalance_frequency == "monthly" and s.max_holdings == 25
    assert s.rebalance_date == "mid"
    dumped = p.model_dump()
    assert dumped["sleeves"][0]["universe"] == "NIFTYMIDCAP150"
    assert dumped["name"] == "Growth Book"


def _valid_portfolio_dict():
    return {
        "name": "P", "inception_date": "2024-01-01", "total_capital": 1000000,
        "sleeves": [
            {"strategy_id": "s1", "weight": 60, "universe": "NIFTY500"},
            {"strategy_id": "s2", "weight": 40, "universe": "NIFTYMIDCAP150"},
        ],
    }


def test_validate_accepts_good_portfolio():
    assert validate_portfolio(_valid_portfolio_dict(), {"s1", "s2"}) == []


def test_validate_rejects_bad_weights():
    p = _valid_portfolio_dict()
    p["sleeves"][0]["weight"] = 50  # now sums to 90
    errs = validate_portfolio(p, {"s1", "s2"})
    assert any("sum to 100" in e for e in errs)


def test_validate_rejects_unknown_strategy():
    errs = validate_portfolio(_valid_portfolio_dict(), {"s1"})  # s2 unknown
    assert any("unknown strategy_id: s2" in e for e in errs)


def test_validate_rejects_no_sleeves():
    p = _valid_portfolio_dict(); p["sleeves"] = []
    errs = validate_portfolio(p, set())
    assert any("at least one sleeve" in e for e in errs)


def test_validate_rejects_early_inception():
    p = _valid_portfolio_dict(); p["inception_date"] = "2021-06-01"
    errs = validate_portfolio(p, {"s1", "s2"})
    assert any("2022-01-01" in e for e in errs)


from portfolio_composition import blend_curve, benchmark_curve


def test_blend_curve_sums_and_forward_fills():
    a = [{"date": "2024-01-01", "portfolio_value": 100},
         {"date": "2024-01-02", "portfolio_value": 110}]
    b = [{"date": "2024-01-01", "portfolio_value": 200},
         {"date": "2024-01-03", "portfolio_value": 260}]  # missing 01-02
    curve = blend_curve([a, b])
    vals = {c["date"]: c["portfolio_value"] for c in curve}
    assert vals["2024-01-01"] == 300            # 100 + 200
    assert vals["2024-01-02"] == 310            # 110 + (ff 200)
    assert vals["2024-01-03"] == 370            # (ff 110) + 260
    assert [c["date"] for c in curve] == ["2024-01-01", "2024-01-02", "2024-01-03"]


def test_benchmark_curve_scales_to_capital():
    dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
    closes = {"2024-01-01": 100.0, "2024-01-02": 110.0, "2024-01-03": 90.0}
    bm = benchmark_curve(dates, closes, total_capital=1000.0)
    assert bm["2024-01-01"] == 1000.0           # base
    assert bm["2024-01-02"] == 1100.0           # +10%
    assert bm["2024-01-03"] == 900.0            # -10% vs base


def test_benchmark_curve_forward_fills_missing_close():
    dates = ["2024-01-01", "2024-01-02"]
    closes = {"2024-01-01": 50.0}               # 01-02 missing
    bm = benchmark_curve(dates, closes, total_capital=500.0)
    assert bm["2024-01-02"] == 500.0            # carried forward


from portfolio_composition import union_holdings


def test_union_holdings_aggregates_shared_symbol():
    mid = [{"symbol": "DIXON", "quantity": 10, "current_price": 100,
            "market_value": 1000, "pnl": 200}]
    micro = [{"symbol": "DIXON", "quantity": 5, "current_price": 100,
              "market_value": 500, "pnl": 50},
             {"symbol": "KAYNES", "quantity": 4, "current_price": 250,
              "market_value": 1000, "pnl": -100}]
    book = union_holdings([("Mid", mid), ("Micro", micro)], blended_final=2500.0)
    by_sym = {h["symbol"]: h for h in book}

    dixon = by_sym["DIXON"]
    assert dixon["quantity"] == 15 and dixon["market_value"] == 1500
    assert "Mid" in dixon["sleeve"] and "Micro" in dixon["sleeve"]
    assert dixon["weight"] == 60.0                      # 1500/2500
    # cost = (1000-200)+(500-50)=1250 ; pnl=250 ; pnl% = 20.0
    assert dixon["pnl"] == 250 and dixon["pnl_percent"] == 20.0

    assert by_sym["KAYNES"]["pnl_percent"] == round(-100 / 1100 * 100, 2)
    assert [h["symbol"] for h in book] == ["DIXON", "KAYNES"]   # sorted by mkt value


from portfolio_composition import max_drawdown, sharpe_ratio, summary_metrics


def test_max_drawdown_basic():
    assert max_drawdown([100, 120, 90, 110]) == round((90 - 120) / 120 * 100, 2)  # -25.0
    assert max_drawdown([100, 110, 120]) == 0.0


def test_sharpe_zero_for_flat_series():
    assert sharpe_ratio([100, 100, 100]) == 0.0


def test_summary_metrics_returns_and_alpha():
    curve = [
        {"date": "2024-01-01", "portfolio_value": 1000, "benchmark_value": 1000},
        {"date": "2024-01-02", "portfolio_value": 1200, "benchmark_value": 1100},
    ]
    s = summary_metrics(curve, total_capital=1000.0)
    assert s["total_value"] == 1200 and s["total_pnl"] == 200
    assert s["total_return"] == 20.0 and s["benchmark_return"] == 10.0
    assert s["alpha"] == 10.0
    assert "max_drawdown" in s and "sharpe_ratio" in s


from portfolio_composition import compose_portfolio


def _sleeve_run(values, holdings_last, added=None, exited=None):
    results = []
    dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
    for i, (d, v) in enumerate(zip(dates, values)):
        last = i == len(values) - 1
        results.append({
            "date": d, "portfolio_value": v,
            "holdings": holdings_last if last else [],
            "new_added": (added or []) if last else [],
            "exited": (exited or []) if last else [],
        })
    final_ret = (values[-1] / values[0] - 1) * 100
    return {"results": results, "summary": {"total_return": round(final_ret, 2)},
            "point_in_time_coverage": {"total_ever_members": 10, "covered": 9, "missing": ["X"]}}


def test_compose_portfolio_end_to_end():
    sleeves = [
        {"id": "sl1", "label": "Mid", "weight": 60},
        {"id": "sl2", "label": "Micro", "weight": 40},
    ]
    runs = [
        _sleeve_run([600, 660, 720], [{"symbol": "DIXON", "quantity": 5,
                    "current_price": 100, "market_value": 500, "pnl": 50}],
                    added=["DIXON"], exited=["OLD"]),
        _sleeve_run([400, 420, 480], [{"symbol": "KAYNES", "quantity": 2,
                    "current_price": 200, "market_value": 400, "pnl": 80}],
                    added=["KAYNES"]),
    ]
    closes = {"2024-01-01": 100.0, "2024-01-02": 105.0, "2024-01-03": 110.0}
    out = compose_portfolio(
        sleeves=sleeves, sleeve_runs=runs, total_capital=1000.0,
        benchmark_closes=closes, portfolio_meta={"id": "p1", "name": "P", "point_in_time": True},
        as_of="2024-01-03")

    assert out["curve"][-1]["portfolio_value"] == 1200          # 720 + 480
    assert out["curve"][-1]["benchmark_value"] == 1100.0        # +10% on 1000
    assert out["summary"]["total_return"] == 20.0
    assert out["summary"]["benchmark_return"] == 10.0 and out["summary"]["alpha"] == 10.0
    assert {h["symbol"] for h in out["current_book"]} == {"DIXON", "KAYNES"}
    attr = {s["id"]: s for s in out["sleeves"]}
    assert attr["sl1"]["contribution"] == round(20.0 * 60 / 100, 2)   # 12.0
    assert attr["sl1"]["coverage"]["missing"] == ["X"]
    assert out["rebalance_actions"]["next_due"] is None
    syms_added = {a["symbol"] for a in out["rebalance_actions"]["add"]}
    assert syms_added == {"DIXON", "KAYNES"}
    assert out["portfolio"]["as_of"] == "2024-01-03"


def test_validate_rejects_out_of_range_weight():
    p = _valid_portfolio_dict()
    p["sleeves"] = [{"strategy_id": "s1", "weight": 150, "universe": "NIFTY500"},
                    {"strategy_id": "s2", "weight": -50, "universe": "NIFTYMIDCAP150"}]
    errs = validate_portfolio(p, {"s1", "s2"})
    assert any("weight" in e and ("between" in e or "0" in e) for e in errs)


# --------------------------------------------------------------------------- rebalance plan
def _book(*rows):
    """current_book rows as compose_portfolio emits them."""
    return [{"symbol": s, "quantity": q, "current_price": p, "market_value": round(q * p, 2)}
            for s, q, p in rows]


def test_plan_sizes_new_entrants_and_full_exits():
    """A pick not held is a BUY for its full target; a holding not picked is a SELL
    of the entire position — the two numbers an operator keys into the terminal."""
    plan = build_rebalance_plan(
        current_book=_book(("OLD", 10, 100.0)),
        targets=[{"symbol": "NEW", "sleeve": "Mid", "target_value": 1000.0}],
        prices={"OLD": 100.0, "NEW": 200.0},
    )
    rows = {r["symbol"]: r for r in plan["rows"]}
    assert rows["NEW"]["action"] == "buy"
    assert rows["NEW"]["delta_value"] == 1000.0 and rows["NEW"]["delta_qty"] == 5
    assert rows["OLD"]["action"] == "sell"
    assert rows["OLD"]["delta_value"] == -1000.0 and rows["OLD"]["delta_qty"] == -10
    assert rows["OLD"]["target_value"] == 0.0


def test_plan_resizes_a_held_name_that_is_still_picked():
    """Equal-weight rebalancing resizes survivors too — drift must show as add/trim,
    not be hidden because the name appears in both books."""
    plan = build_rebalance_plan(
        current_book=_book(("A", 10, 100.0), ("B", 10, 100.0)),
        targets=[{"symbol": "A", "sleeve": "Mid", "target_value": 1500.0},
                 {"symbol": "B", "sleeve": "Mid", "target_value": 500.0}],
        prices={"A": 100.0, "B": 100.0},
    )
    rows = {r["symbol"]: r for r in plan["rows"]}
    assert rows["A"]["action"] == "add" and rows["A"]["delta_value"] == 500.0
    assert rows["B"]["action"] == "trim" and rows["B"]["delta_value"] == -500.0


def test_plan_suppresses_noise_trades_below_threshold():
    """Tiny drift should read as HOLD — otherwise every cycle lists 20 pointless orders."""
    plan = build_rebalance_plan(
        current_book=_book(("A", 10, 100.0)),
        targets=[{"symbol": "A", "sleeve": "Mid", "target_value": 1005.0}],
        prices={"A": 100.0},
        min_trade_value=50.0,
    )
    row = plan["rows"][0]
    assert row["action"] == "hold" and row["delta_qty"] == 0 and row["delta_value"] == 0.0


def test_plan_summary_totals_and_turnover():
    plan = build_rebalance_plan(
        current_book=_book(("OLD", 10, 100.0), ("KEEP", 10, 100.0)),
        targets=[{"symbol": "KEEP", "sleeve": "Mid", "target_value": 1000.0},
                 {"symbol": "NEW", "sleeve": "Mid", "target_value": 1000.0}],
        prices={"OLD": 100.0, "KEEP": 100.0, "NEW": 100.0},
    )
    s = plan["summary"]
    assert s["buy_value"] == 1000.0 and s["sell_value"] == 1000.0
    assert s["net_cash"] == 0.0
    assert s["positions_before"] == 2 and s["positions_after"] == 2
    assert s["new_positions"] == 1 and s["exited_positions"] == 1
    # one name of a two-name book replaced == 50% turnover
    assert s["turnover_percent"] == 50.0


def test_plan_aggregates_a_symbol_targeted_by_two_sleeves():
    """The operator trades one combined book — a name both sleeves want is ONE order."""
    plan = build_rebalance_plan(
        current_book=[],
        targets=[{"symbol": "A", "sleeve": "Mid", "target_value": 1000.0},
                 {"symbol": "A", "sleeve": "First", "target_value": 500.0}],
        prices={"A": 100.0},
    )
    assert len(plan["rows"]) == 1
    row = plan["rows"][0]
    assert row["target_value"] == 1500.0 and row["delta_qty"] == 15
    assert row["sleeve"] == "First, Mid"


def test_plan_flags_a_name_with_no_price_instead_of_sizing_it():
    """No price means no honest quantity. Report it; never guess a fill size."""
    plan = build_rebalance_plan(
        current_book=[],
        targets=[{"symbol": "GHOST", "sleeve": "Mid", "target_value": 1000.0}],
        prices={},
    )
    row = plan["rows"][0]
    assert row["action"] == "unpriced" and row["delta_qty"] == 0
    assert plan["summary"]["unpriced"] == ["GHOST"]
    assert plan["summary"]["buy_value"] == 0.0


def test_plan_treats_an_unchanged_position_as_hold():
    """A sleeve that is not due this cycle re-targets its holdings at their current
    value. Delta is exactly zero and must read as hold, not a zero-sized trim."""
    plan = build_rebalance_plan(
        current_book=_book(("A", 10, 100.0)),
        targets=[{"symbol": "A", "sleeve": "First", "target_value": 1000.0}],
        prices={"A": 100.0},
    )
    row = plan["rows"][0]
    assert row["action"] == "hold" and row["delta_value"] == 0.0
    assert plan["summary"]["buy_value"] == 0.0 and plan["summary"]["sell_value"] == 0.0
    assert plan["summary"]["turnover_percent"] == 0.0


def test_plan_book_after_is_the_whole_resulting_portfolio():
    """The point of the preview is seeing the book you'll own — every surviving and
    new position with its post-rebalance weight, not just the names that moved."""
    plan = build_rebalance_plan(
        current_book=_book(("OLD", 10, 100.0), ("KEEP", 10, 100.0)),
        targets=[{"symbol": "KEEP", "sleeve": "Mid", "target_value": 1000.0},
                 {"symbol": "NEW", "sleeve": "Mid", "target_value": 3000.0}],
        prices={"OLD": 100.0, "KEEP": 100.0, "NEW": 100.0},
        symbol_meta={"KEEP": {"index_tier": "Midcap 150"}, "NEW": {"index_tier": "Microcap 250"}},
    )
    book = plan["book_after"]
    assert [b["symbol"] for b in book] == ["NEW", "KEEP"]        # sorted by value
    assert "OLD" not in {b["symbol"] for b in book}              # exited names are gone
    assert book[0]["weight"] == 75.0 and book[1]["weight"] == 25.0
    assert book[0]["status"] == "new" and book[1]["status"] == "kept"
    assert book[0]["tier"] == "Microcap 250"


def test_plan_mix_compares_tier_exposure_before_and_after():
    """'How will the book look' is mostly a question about tier mix."""
    plan = build_rebalance_plan(
        current_book=_book(("OLD", 10, 100.0)),
        targets=[{"symbol": "NEW", "sleeve": "Mid", "target_value": 1000.0}],
        prices={"OLD": 100.0, "NEW": 100.0},
        symbol_meta={"OLD": {"index_tier": "Smallcap 250"}, "NEW": {"index_tier": "Microcap 250"}},
    )
    before = {m["name"]: m for m in plan["mix_before"]}
    after = {m["name"]: m for m in plan["mix_after"]}
    assert before["Smallcap 250"]["weight"] == 100.0
    assert after["Microcap 250"]["weight"] == 100.0
    assert "Smallcap 250" not in after


def test_plan_book_after_falls_back_to_other_without_meta():
    plan = build_rebalance_plan(
        current_book=[], targets=[{"symbol": "A", "sleeve": "M", "target_value": 100.0}],
        prices={"A": 10.0})
    assert plan["book_after"][0]["tier"] == "Other"
    assert plan["mix_after"][0]["name"] == "Other"
