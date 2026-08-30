# Sync-plan builder tests — pure, deterministic, no I/O.
from broker_sync import build_sync_plan, normalize_settings

SIM = [
    {"symbol": "AAA", "quantity": 10.5, "market_value": 50000.0, "current_price": 4761.9},
    {"symbol": "BBB", "quantity": 100.0, "market_value": 30000.0, "current_price": 300.0},
    {"symbol": "CCC", "quantity": 40.0, "market_value": 20000.0, "current_price": 500.0},
]  # sim total 100k

LIVE = [
    {"symbol": "BBB", "quantity": 100.0, "last_price": 300.0, "market_value": 30000.0},
    {"symbol": "ZZZ", "quantity": 5.0, "last_price": 1000.0, "market_value": 5000.0},
]  # live total 35k


def test_settings_normalization_guards():
    s = normalize_settings({"capital_mode": "bogus", "tolerance_percent": -3})
    assert s["capital_mode"] == "account" and s["tolerance_percent"] == 0.0
    s = normalize_settings({"capital_mode": "fixed"})  # fixed without amount
    assert s["capital_mode"] == "account"
    assert normalize_settings(None)["product"] == "CNC"


def test_account_mode_scales_to_live_plus_cash():
    plan = build_sync_plan(sim_book=SIM, live_holdings=LIVE, available_cash=65000.0,
                           settings={"capital_mode": "account"})
    # target_total = 35k + 65k = 100k -> factor 1.0
    assert plan["totals"]["target_total"] == 100000.0
    assert plan["totals"]["scale_factor"] == 1.0
    by_sym = {a["symbol"]: a for a in plan["actions"]}
    assert by_sym["AAA"]["action"] == "BUY" and by_sym["AAA"]["reason"] == "missing"
    assert by_sym["AAA"]["qty"] == int(50000.0 // 4761.9)
    assert "BBB" not in by_sym  # exactly on target -> within tolerance
    # ZZZ unknown -> NOT sold by default, lands in skipped
    assert "ZZZ" not in by_sym
    assert any("ZZZ" == s["symbol"] for s in plan["skipped"])


def test_sells_sequence_before_buys():
    live = LIVE + [{"symbol": "CCC", "quantity": 400.0, "last_price": 500.0,
                    "market_value": 200000.0}]  # way over target -> SELL drift
    plan = build_sync_plan(sim_book=SIM, live_holdings=live, available_cash=0.0,
                           settings={"capital_mode": "sim"})
    kinds = [a["action"] for a in plan["actions"]]
    assert "SELL" in kinds and "BUY" in kinds
    assert kinds == sorted(kinds, key=lambda k: 0 if k == "SELL" else 1)
    seqs = [a["seq"] for a in plan["actions"]]
    assert seqs == list(range(1, len(seqs) + 1))


def test_sell_unknown_enabled_liquidates_foreign_names():
    plan = build_sync_plan(sim_book=SIM, live_holdings=LIVE, available_cash=0.0,
                           settings={"capital_mode": "sim", "sell_unknown": True})
    zzz = [a for a in plan["actions"] if a["symbol"] == "ZZZ"]
    assert zzz and zzz[0]["action"] == "SELL" and zzz[0]["reason"] == "not_in_sim"
    assert zzz[0]["qty"] == 5


def test_tolerance_band_suppresses_small_drift():
    live = [{"symbol": "BBB", "quantity": 98.0, "last_price": 300.0, "market_value": 29400.0}]
    plan = build_sync_plan(sim_book=[SIM[1]], live_holdings=live, available_cash=600.0,
                           settings={"capital_mode": "sim", "tolerance_percent": 5})
    assert plan["actions"] == []  # 2% drift < 5% band


def test_underfunded_plan_flagged_not_feasible():
    plan = build_sync_plan(sim_book=SIM, live_holdings=[], available_cash=1000.0,
                           settings={"capital_mode": "sim"})
    assert plan["feasible"] is False
    assert any("NOT FUNDED" in w for w in plan["warnings"])


def test_funded_plan_is_feasible():
    plan = build_sync_plan(sim_book=SIM, live_holdings=[], available_cash=200000.0,
                           settings={"capital_mode": "sim"})
    assert plan["feasible"] is True


def test_exclude_symbols_skips_both_directions():
    live = LIVE + [{"symbol": "AAA", "quantity": 100.0, "last_price": 4761.9,
                    "market_value": 476190.0}]
    plan = build_sync_plan(sim_book=SIM, live_holdings=live, available_cash=0.0,
                           settings={"capital_mode": "sim", "sell_unknown": True,
                                     "exclude_symbols": ["aaa", "ZZZ"]})
    syms = {a["symbol"] for a in plan["actions"]}
    assert "AAA" not in syms and "ZZZ" not in syms
    reasons = {s["symbol"]: s["reason"] for s in plan["skipped"]}
    assert "excluded" in reasons["AAA"] and "excluded" in reasons["ZZZ"]


def test_liquidity_participation_cap_and_slices():
    # CCC target 20k but ADV only 40k -> 10% cap = 4k -> 8 shares @500
    plan = build_sync_plan(sim_book=SIM, live_holdings=[], available_cash=200000.0,
                           settings={"capital_mode": "sim"},
                           adv_values={"CCC": 40000.0})
    ccc = [a for a in plan["actions"] if a["symbol"] == "CCC"][0]
    assert ccc["liquidity_capped"] is True and ccc["qty"] == 8
    assert ccc["suggested_slices"] == 5  # 40 desired / 8 per day
    assert any("CCC" in w and "slices" in w for w in plan["warnings"])


def test_limit_prices_attached():
    plan = build_sync_plan(sim_book=SIM, live_holdings=[], available_cash=200000.0,
                           settings={"capital_mode": "sim", "limit_band_percent": 1.0})
    buy = plan["actions"][0]
    assert buy["order_type"] == "LIMIT"
    assert abs(buy["limit_price"] - buy["last_price"] * 1.01) < 0.02


def test_sell_qty_capped_at_live_quantity():
    live = [{"symbol": "BBB", "quantity": 10.0, "last_price": 300.0, "market_value": 3000.0}]
    sim = [{"symbol": "BBB", "quantity": 1.0, "market_value": 300.0, "current_price": 300.0}]
    plan = build_sync_plan(sim_book=sim, live_holdings=live, available_cash=0.0,
                           settings={"capital_mode": "sim", "tolerance_percent": 0,
                                     "min_order_value": 0})
    sell = plan["actions"][0]
    assert sell["action"] == "SELL" and sell["qty"] <= 10


def test_every_action_row_carries_execution_schema():
    plan = build_sync_plan(sim_book=SIM, live_holdings=[], available_cash=200000.0,
                           settings={"capital_mode": "sim"})
    required = {"seq", "action", "symbol", "exchange", "security_id", "product",
                "order_type", "sim_qty", "live_qty", "qty", "last_price", "est_value",
                "target_value", "live_value", "reason", "drift_percent", "status"}
    for a in plan["actions"]:
        assert required <= set(a), f"missing keys in {a}"
        assert a["status"] == "planned" and a["product"] == "CNC"


def test_fresh_prices_take_priority():
    live = [{"symbol": "BBB", "quantity": 100.0, "last_price": 300.0, "market_value": 30000.0}]
    plan = build_sync_plan(sim_book=SIM, live_holdings=live, available_cash=200000.0,
                           settings={"capital_mode": "sim", "limit_band_percent": 1.0},
                           fresh_prices={"AAA": 5000.0})
    aaa = [a for a in plan["actions"] if a["symbol"] == "AAA"][0]
    assert aaa["last_price"] == 5000.0          # fresh quote beats sim close (4761.9)
    assert abs(aaa["limit_price"] - 5050.0) < 0.01
    assert plan["fresh_quotes"] == 1
