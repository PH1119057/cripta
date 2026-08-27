from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .engine import PositionSupervisor
from .models import PositionEvent, PositionSnapshot


@dataclass(frozen=True)
class SupervisorEventEnvelope:
    """One normalized causal input, independent of its transport."""

    sequence: int
    event: PositionEvent


class OrderedEventAdapter:
    """Normalizes live or replay inputs and rejects sequence gaps/duplicates."""

    def __init__(self, events: Iterable[SupervisorEventEnvelope]) -> None:
        self._events = events

    def __iter__(self) -> Iterator[PositionEvent]:
        expected: int | None = None
        for envelope in self._events:
            if expected is None:
                expected = envelope.sequence
            if envelope.sequence != expected:
                raise ValueError(
                    f"event sequence violation: expected {expected}, got {envelope.sequence}"
                )
            yield envelope.event
            expected += 1


def process_events(
    supervisor: PositionSupervisor, adapter: Iterable[PositionEvent]
) -> list[PositionSnapshot]:
    """The sole event-processing path shared by live and historical adapters."""
    return [supervisor.update(event) for event in adapter]
