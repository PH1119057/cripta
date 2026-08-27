from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bybit_workbench.mayak.research.event_truth import AnchorType, load_discovery_events

ROOT = Path(__file__).parents[1]


def test_all9_event_truth_is_exact_and_causal() -> None:
    events = load_discovery_events(ROOT)
    assert len(events) == 1063
    assert all(event.anchor_type is AnchorType.ENTRY_DECISION for event in events)
    assert all(event.anchor_time == event.entry_time for event in events)
    assert all(event.anchor_time.tzinfo == UTC for event in events)
    assert min(event.entry_time for event in events) >= datetime(2026, 5, 18, tzinfo=UTC)


def test_future_metadata_never_moves_feature_cutoff() -> None:
    event = load_discovery_events(ROOT)[0]
    assert event.anchor_time == event.entry_time
    assert event.outcome_time is None or event.outcome_time >= event.anchor_time
