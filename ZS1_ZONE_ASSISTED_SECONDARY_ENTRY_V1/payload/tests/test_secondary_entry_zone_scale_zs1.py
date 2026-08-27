from __future__ import annotations

import json

from bybit_workbench.research.secondary_entry_zone_scale_zs1 import (
    classify_zone_relation,
    metric,
    primary_benchmark_pnl,
)


def _se1_row() -> dict[str, str]:
    return {
        "scale_entry_at": "2026-06-01T01:00:00+00:00",
        "target_hits_json": json.dumps({"0.50": None, "1.00": None, "1.10": None, "2.00": None, "3.00": None}),
        "secondary_exit_reason": "structural_stop",
        "structural_stop_distance_from_scale_pct": "0.4",
    }


def test_primary_benchmark_winner_is_plus_10() -> None:
    row = _se1_row()
    row["target_hits_json"] = json.dumps({"1.10": {"at": "x"}})
    assert primary_benchmark_pnl(row) == 10.0


def test_primary_benchmark_structural_stop_includes_cost() -> None:
    row = _se1_row()
    assert primary_benchmark_pnl(row) == -5.0


def test_primary_benchmark_unresolved_horizon_is_none() -> None:
    row = _se1_row()
    row["secondary_exit_reason"] = "horizon"
    assert primary_benchmark_pnl(row) is None


def test_zone_resolution_before_scale_is_causal() -> None:
    row = _se1_row()
    zone = {
        "structure_resolved": "True",
        "structure_state": "obstacle_clean_break_with",
        "structure_sign": "favorable",
        "zone_outcome_at": "2026-06-01T00:30:00+00:00",
    }
    relation, state, sign, delay = classify_zone_relation(row, zone)
    assert relation == "resolved_before_scale"
    assert state == "obstacle_clean_break_with"
    assert sign == "favorable"
    assert delay == 1800.0


def test_zone_resolution_after_scale_is_not_used_as_filter() -> None:
    row = _se1_row()
    zone = {
        "structure_resolved": "True",
        "structure_state": "protective_hold_reclaim",
        "structure_sign": "favorable",
        "zone_outcome_at": "2026-06-01T01:30:00+00:00",
    }
    relation, _state, _sign, delay = classify_zone_relation(row, zone)
    assert relation == "resolved_after_scale"
    assert delay == -1800.0


def test_metric_uses_only_resolved_rows_for_ev() -> None:
    rows = [
        {
            "primary_benchmark_pnl_usd": 10.0,
            "hit_plus_1p10": True,
            "hit_plus_0p50": True,
            "hit_plus_1p00": True,
            "hit_plus_2p00": False,
            "hit_plus_3p00": False,
        },
        {
            "primary_benchmark_pnl_usd": -5.0,
            "hit_plus_1p10": False,
            "hit_plus_0p50": False,
            "hit_plus_1p00": False,
            "hit_plus_2p00": False,
            "hit_plus_3p00": False,
        },
        {
            "primary_benchmark_pnl_usd": None,
            "hit_plus_1p10": False,
            "hit_plus_0p50": False,
            "hit_plus_1p00": False,
            "hit_plus_2p00": False,
            "hit_plus_3p00": False,
        },
    ]
    result = metric("x", rows)
    assert result.rows == 3
    assert result.resolved == 2
    assert result.ev_usd == 2.5
