from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.research.secondary_entry_se2 import (
    EXPECTED_SE1_RUN_CONTRACT_SHA256,
    Candidate,
    CausalFeatures,
    Economics,
    Event,
    Outcome,
    RobustnessProtocol,
    _resolved_pnl,
    _validate_se1_contract,
    candidate_grid,
    evaluate_candidate,
    load_events,
)


def _features(
    *,
    zero_crossings: int = 3,
    touch_to_scale: float = 600.0,
    launch_to_scale: float = 60.0,
    adverse: float = 0.50,
    rebound: float = 0.30,
    stop_distance: float = 0.40,
    symbol: str = "BTCUSDT",
) -> CausalFeatures:
    return CausalFeatures(
        symbol=symbol,
        touch_at=datetime(2026, 5, 20, tzinfo=UTC),
        min_adverse_depth_pct=adverse,
        rebound_confirmation_pct=rebound,
        zero_crossings_before_scale=zero_crossings,
        seconds_touch_to_launch=touch_to_scale - launch_to_scale,
        seconds_launch_to_scale=launch_to_scale,
        seconds_touch_to_scale=touch_to_scale,
        launch_move_vs_main_pct=-0.55,
        scale_entry_move_vs_main_pct=-0.25,
        structural_stop_distance_from_scale_pct=stop_distance,
    )


def _outcome(*, win: bool, stop: bool = True) -> Outcome:
    return Outcome(
        secondary_exit_reason="structural_stop" if stop else "horizon",
        target_hits={
            "0.50": win,
            "1.00": win,
            "1.10": win,
            "2.00": False,
            "3.00": False,
        },
        secondary_mfe_to_exit_pct=1.2 if win else 0.2,
        secondary_mae_to_exit_pct=-0.1 if win else -0.4,
        secondary_mfe_to_horizon_pct=1.5 if win else 0.4,
        secondary_mae_to_horizon_pct=-0.2 if win else -0.6,
    )


def test_candidate_grid_is_predeclared_and_unique() -> None:
    grid = candidate_grid()

    assert len(grid) == 1800
    assert len({candidate.candidate_id for candidate in grid}) == len(grid)


def test_clean_launch_crossing_filter_is_causal_and_exact() -> None:
    candidate = Candidate("Z", 0.50, 0.30, max_zero_crossings=3)

    assert candidate.matches(_features(zero_crossings=3))
    assert not candidate.matches(_features(zero_crossings=4))
    assert not candidate.matches(_features(adverse=0.75))


def test_rebound_speed_uses_only_launch_to_scale_path() -> None:
    candidate = Candidate(
        "V",
        0.50,
        0.30,
        min_rebound_speed_pct_per_min=0.20,
    )

    assert candidate.matches(_features(launch_to_scale=60.0))
    assert not candidate.matches(_features(launch_to_scale=180.0))


def test_candidate_match_does_not_depend_on_future_outcome() -> None:
    candidate = Candidate("Z", 0.50, 0.30, max_zero_crossings=3)
    features = _features()
    winner = Event(features, _outcome(win=True))
    loser = Event(features, _outcome(win=False))

    assert candidate.matches(winner.features)
    assert candidate.matches(loser.features)


def test_primary_economics_uses_secondary_fill_stop_distance_and_cost() -> None:
    economics = Economics(margin_usd=100.0, leverage=10.0, primary_cost_pct_notional=0.10)
    winner = Event(_features(stop_distance=0.40), _outcome(win=True))
    loser = Event(_features(stop_distance=0.40), _outcome(win=False))

    assert _resolved_pnl(winner, economics) == pytest.approx(10.0)
    assert _resolved_pnl(loser, economics) == pytest.approx(-5.0)


def test_robustness_gate_requires_cross_symbol_and_temporal_stability() -> None:
    candidate = Candidate("Z", 0.50, 0.30, max_zero_crossings=3)
    events: list[Event] = []
    symbols = (
        "UNIUSDT",
        "LINKUSDT",
        "BTCUSDT",
        "ETHUSDT",
        "XRPUSDT",
        "1000PEPEUSDT",
        "SOLUSDT",
        "DOGEUSDT",
        "ADAUSDT",
    )
    dates = (
        datetime(2026, 5, 20, tzinfo=UTC),
        datetime(2026, 6, 20, tzinfo=UTC),
        datetime(2026, 7, 20, tzinfo=UTC),
    )
    for index in range(90):
        symbol = symbols[index % len(symbols)]
        fold_date = dates[index % len(dates)]
        features = _features(symbol=symbol)
        features = CausalFeatures(**{**features.__dict__, "touch_at": fold_date})
        events.append(Event(features, _outcome(win=index % 2 == 0)))

    metric = evaluate_candidate(
        candidate,
        events,
        Economics(),
        RobustnessProtocol(),
        base_ev=-1.0,
        base_triggered=900,
    )

    assert metric.resolved == 90
    assert metric.primary_ev_usd > 0
    assert metric.evaluable_temporal_folds == 3
    assert metric.evaluable_symbols == 9


def _write_event_csv(path: Path, *, symbol: str) -> None:
    row = {
        "symbol": symbol,
        "touch_at": "2026-05-20T00:00:00+00:00",
        "min_adverse_depth_pct": "0.50",
        "rebound_confirmation_pct": "0.30",
        "trigger_status": "triggered",
        "zero_crossings_before_scale": "3",
        "seconds_touch_to_launch": "300",
        "seconds_launch_to_scale": "60",
        "seconds_touch_to_scale": "360",
        "launch_move_vs_main_pct": "-0.55",
        "scale_entry_move_vs_main_pct": "-0.25",
        "structural_stop_distance_from_scale_pct": "0.40",
        "secondary_exit_reason": "structural_stop",
        "secondary_mfe_to_exit_pct": "1.20",
        "secondary_mae_to_exit_pct": "-0.40",
        "secondary_mfe_to_horizon_pct": "1.50",
        "secondary_mae_to_horizon_pct": "-0.60",
        "target_hits_json": json.dumps(
            {
                "0.50": {"at": "x"},
                "1.00": {"at": "x"},
                "1.10": {"at": "x"},
                "2.00": None,
                "3.00": None,
            }
        ),
    }
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def test_loader_rejects_symbols_outside_all9(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    _write_event_csv(path, symbol="BNBUSDT")

    with pytest.raises(ValueError, match="unexpected symbol"):
        load_events(path)


def test_se1_contract_validation_requires_exact_frozen_hash(tmp_path: Path) -> None:
    events = tmp_path / "secondary_entry_events.csv"
    events.write_text("placeholder", encoding="utf-8")
    contract = {
        "contract_sha256": EXPECTED_SE1_RUN_CONTRACT_SHA256,
        "expected_counts": {
            "UNIUSDT": 113,
            "LINKUSDT": 114,
            "BTCUSDT": 119,
            "ETHUSDT": 130,
            "XRPUSDT": 125,
            "1000PEPEUSDT": 117,
            "SOLUSDT": 91,
            "DOGEUSDT": 143,
            "ADAUSDT": 111,
        },
    }
    (tmp_path / "run_contract.json").write_text(json.dumps(contract), encoding="utf-8")

    assert _validate_se1_contract(events) == EXPECTED_SE1_RUN_CONTRACT_SHA256
