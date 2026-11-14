from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

ScalarValue: TypeAlias = str | float | int | bool


class FeatureStatus(StrEnum):
    VALID = "VALID"
    WARMUP = "WARMUP"
    STALE = "STALE"
    NO_DATA = "NO_DATA"
    ERROR = "ERROR"


class DispatcherDataQuality(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class FeatureKind(StrEnum):
    CATEGORICAL = "CATEGORICAL"
    NUMERIC = "NUMERIC"
    STATUS = "STATUS"


class RequirementMode(StrEnum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"
    TOLERATED = "TOLERATED"
    REJECTED = "REJECTED"


class MatchOperator(StrEnum):
    ONE_OF = "ONE_OF"
    NOT_ONE_OF = "NOT_ONE_OF"
    AT_LEAST = "AT_LEAST"
    AT_MOST = "AT_MOST"
    BETWEEN = "BETWEEN"


class SuitabilityStatus(StrEnum):
    EXCELLENT_MATCH = "EXCELLENT_MATCH"
    GOOD_MATCH = "GOOD_MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    POOR_MATCH = "POOR_MATCH"
    INCOMPATIBLE = "INCOMPATIBLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class FeatureValue:
    value: ScalarValue | None
    status: FeatureStatus = FeatureStatus.VALID
    confidence: float = 1.0
    observed_at: datetime | None = None
    transport_confidence: float = 1.0
    coverage_valid: int | None = None
    coverage_total: int | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("feature confidence must be between zero and one")
        if not 0.0 <= self.transport_confidence <= 1.0:
            raise ValueError("transport confidence must be between zero and one")
        if (self.coverage_valid is None) != (self.coverage_total is None):
            raise ValueError("coverage valid and total must be supplied together")
        if self.coverage_valid is not None and (
            self.coverage_valid < 0
            or self.coverage_total is None
            or self.coverage_total < self.coverage_valid
        ):
            raise ValueError("feature coverage is invalid")
        if self.observed_at is not None:
            _require_utc(self.observed_at, "observed_at")
        if self.status is FeatureStatus.VALID and self.value is None:
            raise ValueError("VALID feature must contain a value")
        if self.status is not FeatureStatus.VALID and self.value is not None:
            raise ValueError("non-VALID feature must not pretend to contain a current value")

    @property
    def usable(self) -> bool:
        return self.status is FeatureStatus.VALID and self.value is not None


@dataclass(frozen=True, slots=True)
class DispatcherMarketSnapshot:
    snapshot_id: str
    observed_at: datetime
    mayak_version: str
    architecture_version: str
    data_quality: DispatcherDataQuality
    features: Mapping[str, FeatureValue]
    provenance: Mapping[str, str] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id is required")
        _require_utc(self.observed_at, "observed_at")
        if not self.mayak_version.strip() or not self.architecture_version.strip():
            raise ValueError("Mayak version and architecture version are required")
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


@dataclass(frozen=True, slots=True)
class ProfileRule:
    feature_id: str
    mode: RequirementMode
    operator: MatchOperator
    expected: tuple[ScalarValue, ...]
    weight: float = 1.0
    reason_ru: str = ""

    def __post_init__(self) -> None:
        if not self.feature_id.strip():
            raise ValueError("feature_id is required")
        if self.weight <= 0:
            raise ValueError("rule weight must be positive")
        if not self.expected:
            raise ValueError("rule expected values are required")
        if (
            self.operator in {MatchOperator.AT_LEAST, MatchOperator.AT_MOST}
            and (len(self.expected) != 1 or not _is_number(self.expected[0]))
        ):
            raise ValueError(f"{self.operator} requires one numeric expected value")
        if self.operator is MatchOperator.BETWEEN:
            if len(self.expected) != 2 or not all(_is_number(item) for item in self.expected):
                raise ValueError("BETWEEN requires two numeric expected values")
            if float(self.expected[0]) > float(self.expected[1]):
                raise ValueError("BETWEEN lower bound exceeds upper bound")


@dataclass(frozen=True, slots=True)
class StrategyMarketProfile:
    profile_id: str
    version: str
    display_name_ru: str
    description_ru: str
    rules: tuple[ProfileRule, ...]

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.version.strip():
            raise ValueError("profile id and version are required")
        if not self.display_name_ru.strip():
            raise ValueError("profile display name is required")
        if not self.rules:
            raise ValueError("profile must contain at least one rule")


@dataclass(frozen=True, slots=True)
class RuleAssessment:
    feature_id: str
    mode: RequirementMode
    matched: bool | None
    available: bool
    actual: ScalarValue | None
    expected: tuple[ScalarValue, ...]
    confidence: float
    reason_ru: str


@dataclass(frozen=True, slots=True)
class DispatcherAssessment:
    assessment_id: str
    observed_at: datetime
    snapshot_id: str
    dispatcher_version: str
    profile_id: str
    profile_version: str
    suitability: float | None
    confidence: float
    status: SuitabilityStatus
    matched_required: tuple[str, ...]
    missing_required: tuple[str, ...]
    matched_preferred: tuple[str, ...]
    conflicting: tuple[str, ...]
    rejected_triggered: tuple[str, ...]
    missing_factors: tuple[str, ...]
    rules: tuple[RuleAssessment, ...]

    def __post_init__(self) -> None:
        _require_utc(self.observed_at, "observed_at")
        if self.suitability is not None and not 0.0 <= self.suitability <= 1.0:
            raise ValueError("suitability must be between zero and one")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")
