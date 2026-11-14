from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.research.mfe_giveback_clean_zone_p52 import (
    ZoneEvent,
    causal_events_for_signal,
    classify_structure,
)


@pytest.mark.parametrize(
    ("direction", "role", "outcome", "state", "sign"),
    [
        ("Long", "support", "bounce", "protective_hold_reclaim", "favorable"),
        ("Long", "support", "false_break_reclaim", "protective_hold_reclaim", "favorable"),
        ("Long", "support", "clean_break", "protective_clean_break_against", "adverse"),
        ("Long", "resistance", "bounce", "obstacle_rejection_against", "adverse"),
        ("Long", "resistance", "clean_break", "obstacle_clean_break_with", "favorable"),
        ("Short", "resistance", "bounce", "protective_hold_reclaim", "favorable"),
        ("Short", "resistance", "clean_break", "protective_clean_break_against", "adverse"),
        ("Short", "support", "bounce", "obstacle_rejection_against", "adverse"),
        ("Short", "support", "clean_break", "obstacle_clean_break_with", "favorable"),
    ],
)
def test_structure_classification_is_mirrored(
    direction: str, role: str, outcome: str, state: str, sign: str
) -> None:
    assert classify_structure(direction, role, outcome) == (state, sign)


def test_causal_filter_uses_event_start_and_resolved_outcome_clock() -> None:
    activation = datetime(2026, 6, 1, 12, tzinfo=UTC)
    limit = activation + timedelta(hours=2)
    events = [
        ZoneEvent(
            "BTCUSDT", 1, 1, "support", activation - timedelta(minutes=1),
            "bounce", activation + timedelta(minutes=5)
        ),
        ZoneEvent(
            "BTCUSDT", 2, 2, "support", activation + timedelta(minutes=5),
            "bounce", activation + timedelta(minutes=15)
        ),
        ZoneEvent(
            "BTCUSDT", 3, 3, "support", activation + timedelta(minutes=10),
            "clean_break", limit + timedelta(seconds=1)
        ),
    ]
    kept = causal_events_for_signal(events, activation_at=activation, baseline_limit=limit)
    assert [item.phase_id for item in kept] == [2]


def test_giveback_start_can_tighten_event_start_gate() -> None:
    activation = datetime(2026, 6, 1, 12, tzinfo=UTC)
    giveback = activation + timedelta(minutes=30)
    events = [
        ZoneEvent(
            "ETHUSDT", 1, 1, "support", activation + timedelta(minutes=20),
            "bounce", activation + timedelta(minutes=35)
        ),
        ZoneEvent(
            "ETHUSDT", 2, 2, "support", activation + timedelta(minutes=31),
            "clean_break", activation + timedelta(minutes=45)
        ),
    ]
    kept = causal_events_for_signal(
        events,
        activation_at=activation,
        event_start_at=giveback,
        baseline_limit=activation + timedelta(hours=2),
    )
    assert [item.phase_id for item in kept] == [2]
