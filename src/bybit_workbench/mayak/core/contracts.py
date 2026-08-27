from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol


class MayakDataStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    WARMUP = "WARMUP"
    VALID = "VALID"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    ERROR = "ERROR"


class MayakSeaState(StrEnum):
    """P2 output. UNAVAILABLE is the only legal P0/P1 value."""

    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    CALM = "CALM"
    ACTIVE = "ACTIVE"
    ROUGH = "ROUGH"
    STORM = "STORM"
    SHOCK = "SHOCK"


@dataclass(frozen=True, slots=True)
class MayakProvenance:
    core_version: str
    software_version: str
    dataset_fingerprint: str
    feature_spec_fingerprint: str
    universe: tuple[str, ...]
    period_start: datetime | None = None
    period_end: datetime | None = None

    def __post_init__(self) -> None:
        if self.period_start is not None:
            _require_utc(self.period_start, "period_start")
        if self.period_end is not None:
            _require_utc(self.period_end, "period_end")
        if self.period_start and self.period_end and self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")


@dataclass(frozen=True, slots=True)
class MayakObservation:
    symbol: str
    observed_at: datetime
    closed_at: datetime
    close: float
    volume: float

    def __post_init__(self) -> None:
        _require_utc(self.observed_at, "observed_at")
        _require_utc(self.closed_at, "closed_at")
        if self.closed_at > self.observed_at:
            raise ValueError("an observation cannot expose a bar before it closes")
        if self.close <= 0 or self.volume < 0:
            raise ValueError("close must be positive and volume non-negative")


@dataclass(frozen=True, slots=True)
class MayakMarketContext:
    observed_at: datetime
    context_version: str
    core_version: str
    data_status: MayakDataStatus
    data_confidence: float
    freshness: timedelta
    market_direction: float | None
    market_velocity: float | None
    market_acceleration: float | None
    directional_agreement: float | None
    btc: Mapping[str, float]
    eth: Mapping[str, float]
    breadth: Mapping[str, float]
    synchronization: Mapping[str, float]
    synchronization_persistence: Mapping[str, float]
    dispersion: float | None
    normalized_displacement: float | None
    sea_state: MayakSeaState
    score: float | None
    previous_state: MayakSeaState | None
    regime_started_at: datetime | None
    time_in_regime: timedelta | None
    transition_speed: float | None
    normalization_progress: float | None
    provenance: MayakProvenance

    def __post_init__(self) -> None:
        _require_utc(self.observed_at, "observed_at")
        if not 0.0 <= self.data_confidence <= 1.0:
            raise ValueError("data_confidence must be between zero and one")
        if self.freshness < timedelta(0):
            raise ValueError("freshness cannot be negative")
        for name in ("btc", "eth", "breadth", "synchronization", "synchronization_persistence"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        if self.data_status is not MayakDataStatus.VALID:
            if self.sea_state not in {MayakSeaState.UNKNOWN, MayakSeaState.UNAVAILABLE}:
                raise ValueError("invalid data cannot publish a current sea state")
            if self.score is not None:
                raise ValueError("invalid data cannot publish a score")


class MarketDataProvider(Protocol):
    def observations(self, *, until: datetime) -> Sequence[MayakObservation]: ...


class MayakContextProvider(Protocol):
    def current(self) -> MayakMarketContext: ...

    def history(self, *, since: datetime, until: datetime) -> Sequence[MayakMarketContext]: ...


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")
