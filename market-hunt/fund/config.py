"""Fund configuration: defaults, validation, and model versioning (spec §2, §5.0)."""
from __future__ import annotations

import copy
import math
from datetime import date

from .engine.fund import validate_model_start

DEFAULT_CONFIG = {
    "_id": "main",
    "name": "tvagent Fund",
    "capital": 4_500_000.0,
    "model_start": "2020-10-01",
    "backfill_weekday": 0,          # weekday used for model history before the first published run (0 = Monday)
    "slippage_bp": 5.0,
    "reset_cost": 0.0015,
    "limit_band": 0.003,
    "rebalance_months": [1, 7],      # fund-level rebalance on the first run day of these months
    "rebalance_band": 0.05,          # ...and on any month's first run day when a sleeve is > 5 pts from target
    "version": 1,
    "sleeves": [
        {"id": "value", "label": "Value core", "kind": "grid10", "universe": "NIFTY50", "weight": 0.28,
         "params": {"n": 10}},
        {"id": "gold", "label": "Gold", "kind": "asset", "symbol": "GOLDBEES", "weight": 0.08},
        {"id": "ndx", "label": "Nasdaq-100", "kind": "asset", "symbol": "MON100", "weight": 0.04},
        {"id": "growth", "label": "Growth", "kind": "tranche", "universe": "NIFTY200", "weight": 0.35,
         "params": {"top": 5, "weeks": 8, "slot_div": 40, "max_w": 0.10}},
        {"id": "satellite", "label": "Satellite", "kind": "tranche", "universe": "NIFTY500MICRO750", "weight": 0.25,
         "params": {"top": 5, "weeks": 8, "slot_div": 40, "max_w": 0.10}},
    ],
}

MATERIAL = ("capital", "model_start", "sleeves", "backfill_weekday", "slippage_bp", "reset_cost", "rebalance_months",
            "rebalance_band")


SLEEVE_KEYS = {"id", "label", "kind", "universe", "symbol", "weight", "params"}
PARAMS = {  # name: (type, min, max)
    "grid10": {"n": (int, 1, 50)},
    "tranche": {"top": (int, 1, 20), "weeks": (int, 1, 52), "slot_div": (int, 1, 1000), "max_w": (float, 0.01, 1.0),
                "deploy_cash": (bool, None, None)},
    "asset": {},
}


def _num(v, lo, hi, integer=False) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    if integer and int(v) != v:
        return False
    return math.isfinite(v) and lo <= v <= hi


def validate(cfg: dict, today: date, universes: set | None = None, symbols: set | None = None) -> list[str]:
    """Every field is type- and range-checked; sleeve universes / ETF symbols must be in the allow-lists when given
    (the router passes the indices and symbols that exist in the database)."""
    errors = []
    name = cfg.get("name", "")
    if not isinstance(name, str) or not 1 <= len(name) <= 80:
        errors.append("name must be 1-80 characters")
    try:
        validate_model_start(date.fromisoformat(str(cfg["model_start"])), today)
    except (ValueError, KeyError) as e:
        errors.append(str(e))
    if not _num(cfg.get("capital"), 1_000, 1e11):
        errors.append("capital must be a number between ₹1,000 and ₹10,000 Cr")
    if not _num(cfg.get("slippage_bp", 0), 0, 100):
        errors.append("slippage must be between 0 and 100 bp")
    if not _num(cfg.get("reset_cost", 0), 0, 0.02):
        errors.append("reset cost must be between 0% and 2%")
    if not _num(cfg.get("limit_band", 0), 0, 0.05):
        errors.append("limit band must be between 0% and 5%")
    if not _num(cfg.get("backfill_weekday", 0), 0, 4, integer=True):
        errors.append("backfill_weekday must be 0 (Mon) .. 4 (Fri)")
    months = cfg.get("rebalance_months", [1, 7])
    if (not isinstance(months, list) or not months or len(set(months)) != len(months)
            or not all(_num(m, 1, 12, integer=True) for m in months)):
        errors.append("rebalance months must be a non-empty list of distinct months 1-12")
    band = cfg.get("rebalance_band")
    if band is not None and not _num(band, 0.005, 0.5):
        errors.append("rebalance band must be between 0.5 and 50 points (or off)")
    sleeves = cfg.get("sleeves")
    if not isinstance(sleeves, list) or not 1 <= len(sleeves) <= 12:
        return errors + ["sleeves must be a list of 1-12 sleeves"]
    ids = []
    for s in sleeves:
        if not isinstance(s, dict):
            errors.append("each sleeve must be an object")
            continue
        sid = s.get("id")
        tag = f"sleeve {sid!r}"
        extra = set(s) - SLEEVE_KEYS
        if extra:
            errors.append(f"{tag}: unknown field(s) {sorted(extra)}")
        if not isinstance(sid, str) or not sid.isidentifier() or len(sid) > 30:
            errors.append(f"{tag}: id must be a short identifier")
        ids.append(sid)
        if not isinstance(s.get("label"), str) or not 1 <= len(s["label"]) <= 40:
            errors.append(f"{tag}: label must be 1-40 characters")
        kind = s.get("kind")
        if kind not in PARAMS:
            errors.append(f"{tag}: unknown kind {kind!r}")
            continue
        if not _num(s.get("weight"), 0, 1):
            errors.append(f"{tag}: weight must be between 0 and 100%")
        if kind == "asset":
            sym = s.get("symbol")
            if not isinstance(sym, str) or (symbols is not None and sym not in symbols):
                errors.append(f"{tag}: asset symbol {sym!r} is not a known instrument")
        else:
            uni = s.get("universe")
            if not isinstance(uni, str) or (universes is not None and uni not in universes):
                errors.append(f"{tag}: universe {uni!r} has no point-in-time membership data")
        params = s.get("params") or {}
        if not isinstance(params, dict):
            errors.append(f"{tag}: params must be an object")
            continue
        for k, v in params.items():
            spec = PARAMS[kind].get(k)
            if spec is None:
                errors.append(f"{tag}: unknown parameter {k!r}")
            elif spec[0] is bool:
                if not isinstance(v, bool):
                    errors.append(f"{tag}: {k} must be true/false")
            elif not _num(v, spec[1], spec[2], integer=spec[0] is int):
                errors.append(f"{tag}: {k} must be between {spec[1]} and {spec[2]}")
    if len(set(ids)) != len(ids):
        errors.append("sleeve ids must be unique")
    try:
        total = sum(float(s.get("weight", 0)) for s in sleeves if isinstance(s, dict))
        if abs(total - 1.0) > 1e-6:
            errors.append(f"sleeve weights must sum to 100% (got {total * 100:.2f}%)")
    except (TypeError, ValueError):
        errors.append("sleeve weights must be numbers")
    return errors


def next_version(old: dict, new: dict) -> int:
    """A material change (capital, model start, sleeves, costs) starts a new model version."""
    changed = any(old.get(k) != new.get(k) for k in MATERIAL)
    return int(old.get("version", 1)) + (1 if changed else 0)


def default_config() -> dict:
    return copy.deepcopy(DEFAULT_CONFIG)
