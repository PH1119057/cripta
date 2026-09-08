from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from .contracts import MarketFactEnvelope, StrategyAttempt, StrategySignal


def resolve_causal_value(
    path: str,
    *,
    fact: MarketFactEnvelope,
    signal: StrategySignal | None = None,
    attempt: StrategyAttempt | None = None,
) -> object | None:
    if path == "fact.observed_at":
        return fact.observed_at
    if path == "fact.event_at":
        return fact.event_at
    if path == "fact.received_at":
        return fact.received_at
    if path == "signal.detected_at":
        return None if signal is None else signal.detected_at
    if path == "attempt.created_at":
        return None if attempt is None else attempt.created_at
    if path.startswith("fact."):
        value: object = fact.attributes.to_dict()
        for part in path.removeprefix("fact.").split("."):
            if not isinstance(value, Mapping):
                return None
            value = value.get(part)
            if value is None:
                return None
        return value
    return None


def resolve_causal_timestamp(
    path: str,
    *,
    fact: MarketFactEnvelope,
    signal: StrategySignal | None = None,
    attempt: StrategyAttempt | None = None,
) -> datetime:
    value = resolve_causal_value(path, fact=fact, signal=signal, attempt=attempt)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"causal timestamp {path} must be timezone-aware")
        return value.astimezone(UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError(f"causal timestamp {path} must be timezone-aware")
        return parsed.astimezone(UTC)
    raise ValueError(f"causal timestamp anchor is unavailable: {path}")
