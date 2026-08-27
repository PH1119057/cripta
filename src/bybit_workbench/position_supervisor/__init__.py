"""Causal shadow-only supervision of confirmed exchange positions."""

from .adapters import OrderedEventAdapter, SupervisorEventEnvelope, process_events
from .engine import PositionSupervisor
from .models import (
    FeatureEvidence,
    PositionEvent,
    PositionIdentity,
    PositionSnapshot,
    Quality,
    SupervisorState,
)
from .registry import ExchangePosition, SupervisorRegistry

__all__ = [
    "FeatureEvidence",
    "PositionEvent",
    "PositionIdentity",
    "PositionSnapshot",
    "PositionSupervisor",
    "SupervisorState",
    "Quality",
    "ExchangePosition",
    "SupervisorRegistry",
    "OrderedEventAdapter",
    "SupervisorEventEnvelope",
    "process_events",
]
