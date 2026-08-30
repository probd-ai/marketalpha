import pytest
from wave_models import WaveConfig, Signal

def _level_wave_dict():
    g = lambda L: [{"kind": "cross", "metric": "mean_long", "level": L, "direction": "up"},
                   {"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up", "negate": True}]
    return {
        "capital_N": 1_000_000, "tranches": 20, "reentry": True,
        "buy_cadence": "daily", "exit_mode": "full",
        "entry": [g(40), g(50), g(60)], "accumulate": [],
        "exit": [[{"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up"}],
                 [{"kind": "cross", "metric": "mean_long", "level": 40, "direction": "down"}]],
    }

def test_level_wave_config_parses():
    cfg = WaveConfig(**_level_wave_dict())
    assert cfg.tranches == 20
    assert cfg.entry[0][0].kind == "cross"
    assert cfg.entry[0][1].negate is True

def test_compare_rhs_accepts_metric_or_number():
    s_num = Signal(kind="compare", lhs_metric="mean_long", op=">", rhs={"number": 50})
    s_met = Signal(kind="compare", lhs_metric="truevx_score", op="<=", rhs={"metric": "mean_long"})
    assert s_num.rhs.number == 50
    assert s_met.rhs.metric == "mean_long"

def test_cross_in_entry_requires_daily_cadence():
    d = _level_wave_dict(); d["buy_cadence"] = "monthly"
    with pytest.raises(ValueError):
        WaveConfig(**d)

def test_tranches_must_be_positive():
    d = _level_wave_dict(); d["tranches"] = 0
    with pytest.raises(ValueError):
        WaveConfig(**d)
