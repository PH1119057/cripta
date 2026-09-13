from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from .fingerprint import canonical_json, clone_json_object, fingerprint


class TradeDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class ContextMode(StrEnum):
    OFF = "OFF"
    OBSERVE = "OBSERVE"
    CONDITION = "CONDITION"
    RANKING = "RANKING"


class DataQuality(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


class ContextFailureAction(StrEnum):
    REJECT_SIGNAL = "REJECT_SIGNAL"
    BLOCK_ATTEMPT = "BLOCK_ATTEMPT"
    UNKNOWN = "UNKNOWN"


class EntryDecisionCode(StrEnum):
    ACCEPTED = "ACCEPTED"
    STRATEGY_CONDITION_REJECTED = "STRATEGY_CONDITION_REJECTED"
    INSUFFICIENT_AVAILABLE_FUNDS = "INSUFFICIENT_AVAILABLE_FUNDS"
    OPERATIONAL_SAFETY_BLOCKED = "OPERATIONAL_SAFETY_BLOCKED"
    STALE_OR_UNKNOWN_REQUIRED_STATE = "STALE_OR_UNKNOWN_REQUIRED_STATE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class NotificationKind(StrEnum):
    INSUFFICIENT_AVAILABLE_FUNDS = "INSUFFICIENT_AVAILABLE_FUNDS"
    OPERATIONAL_SAFETY_BLOCKED = "OPERATIONAL_SAFETY_BLOCKED"
    STALE_OR_UNKNOWN_REQUIRED_STATE = "STALE_OR_UNKNOWN_REQUIRED_STATE"
    EXCHANGE_REJECTED = "EXCHANGE_REJECTED"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"


class CooldownScope(StrEnum):
    PER_SYMBOL = "PER_SYMBOL"
    PER_STRATEGY = "PER_STRATEGY"
    PER_ACCOUNT = "PER_ACCOUNT"


@dataclass(frozen=True, slots=True)
class FrozenPolicy:
    payload_json: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object] | None = None) -> FrozenPolicy:
        return cls(canonical_json(dict(payload or {})))

    def to_dict(self) -> dict[str, object]:
        return clone_json_object(self.payload_json)


@dataclass(frozen=True, slots=True)
class NumericRule:
    enabled: bool = False
    value: Decimal | None = None
    unit: str | None = None
    scope: str | None = None

    def __post_init__(self) -> None:
        if self.enabled:
            if self.value is None:
                raise ValueError("enabled numeric rule requires value")
            if not self.unit:
                raise ValueError("enabled numeric rule requires explicit unit")
        elif self.value is not None or self.unit is not None or self.scope is not None:
            raise ValueError("disabled numeric rule cannot carry hidden value, unit or scope")


@dataclass(frozen=True, slots=True)
class CandidateCooldown:
    enabled: bool = False
    duration: Decimal | None = None
    unit: str | None = None
    scope: CooldownScope | None = None
    trigger_event: str | None = None
    anchor: str | None = None

    def __post_init__(self) -> None:
        if self.enabled:
            if self.duration is None or self.duration < 0:
                raise ValueError("enabled cooldown requires non-negative duration")
            if self.unit not in {"seconds", "minutes", "hours"}:
                raise ValueError("enabled cooldown requires explicit duration unit")
            if self.scope is None:
                raise ValueError("enabled cooldown requires scope")
            if self.trigger_event not in {"TOUCH", "SIGNAL", "ATTEMPT"}:
                raise ValueError("enabled cooldown requires explicit trigger_event")
            if not self.anchor:
                raise ValueError("enabled cooldown requires explicit anchor")
        elif (
            self.duration is not None
            or self.unit is not None
            or self.scope is not None
            or self.trigger_event is not None
            or self.anchor is not None
        ):
            raise ValueError(
                "disabled cooldown cannot carry hidden duration, unit, scope, trigger or anchor"
            )

    def as_seconds(self) -> Decimal:
        if not self.enabled or self.duration is None or self.unit is None:
            raise ValueError("disabled cooldown has no duration")
        factors = {
            "seconds": Decimal("1"),
            "minutes": Decimal("60"),
            "hours": Decimal("3600"),
        }
        return self.duration * factors[self.unit]


@dataclass(frozen=True, slots=True)
class EntryEmbargoPolicy:
    enabled: bool = False
    on_resolution: str | None = None
    duration: Decimal | None = None
    unit: str | None = None
    scope: CooldownScope | None = None
    anchor: str | None = None

    def __post_init__(self) -> None:
        if self.enabled:
            if self.on_resolution not in {"FAVORABLE", "ADVERSE"}:
                raise ValueError("enabled embargo requires explicit on_resolution")
            if self.duration is None or self.duration <= 0:
                raise ValueError("enabled embargo requires positive duration")
            if self.unit not in {"seconds", "minutes", "hours"}:
                raise ValueError("enabled embargo requires explicit duration unit")
            if self.scope is None:
                raise ValueError("enabled embargo requires scope")
            if not self.anchor:
                raise ValueError("enabled embargo requires explicit anchor")
        elif (
            self.on_resolution is not None
            or self.duration is not None
            or self.unit is not None
            or self.scope is not None
            or self.anchor is not None
        ):
            raise ValueError("disabled embargo cannot carry hidden policy")

    def as_seconds(self) -> Decimal:
        if not self.enabled or self.duration is None or self.unit is None:
            raise ValueError("disabled embargo has no duration")
        factors = {
            "seconds": Decimal("1"),
            "minutes": Decimal("60"),
            "hours": Decimal("3600"),
        }
        return self.duration * factors[self.unit]


@dataclass(frozen=True, slots=True)
class PostSignalOutcomePolicy:
    enabled: bool = False
    observation_event_kind: str | None = None
    reference_value_path: str | None = None
    observation_value_path: str | None = None
    metric: str | None = None
    favorable_threshold: Decimal | None = None
    adverse_threshold: Decimal | None = None
    horizon: Decimal | None = None
    horizon_unit: str | None = None
    resolution_semantics: str | None = None
    favorable_resulting_entry_state: str | None = None
    adverse_resulting_entry_state: str | None = None
    optional_embargo: EntryEmbargoPolicy = EntryEmbargoPolicy()

    def __post_init__(self) -> None:
        if self.enabled:
            required = {
                "observation_event_kind": self.observation_event_kind,
                "reference_value_path": self.reference_value_path,
                "observation_value_path": self.observation_value_path,
                "metric": self.metric,
                "favorable_threshold": self.favorable_threshold,
                "adverse_threshold": self.adverse_threshold,
                "horizon": self.horizon,
                "horizon_unit": self.horizon_unit,
                "resolution_semantics": self.resolution_semantics,
                "favorable_resulting_entry_state": self.favorable_resulting_entry_state,
                "adverse_resulting_entry_state": self.adverse_resulting_entry_state,
            }
            missing = tuple(
                name for name, value in required.items() if value is None or value == ""
            )
            if missing:
                raise ValueError(
                    "enabled post-signal outcome policy requires explicit " + ", ".join(missing)
                )
            if self.horizon is None or self.horizon <= 0:
                raise ValueError("post-signal horizon must be positive")
            if self.horizon_unit not in {"seconds", "minutes", "hours"}:
                raise ValueError("post-signal horizon requires explicit duration unit")
            if self.resolution_semantics != "FIRST_THRESHOLD":
                raise ValueError("unsupported post-signal resolution_semantics")
            if self.metric != "DIRECTIONAL_PERCENT_CHANGE":
                raise ValueError("unsupported post-signal metric")
        else:
            values = (
                self.observation_event_kind,
                self.reference_value_path,
                self.observation_value_path,
                self.metric,
                self.favorable_threshold,
                self.adverse_threshold,
                self.horizon,
                self.horizon_unit,
                self.resolution_semantics,
                self.favorable_resulting_entry_state,
                self.adverse_resulting_entry_state,
            )
            if any(value is not None for value in values) or self.optional_embargo.enabled:
                raise ValueError("disabled post-signal policy cannot carry hidden values")

    def horizon_seconds(self) -> Decimal:
        if not self.enabled or self.horizon is None or self.horizon_unit is None:
            raise ValueError("disabled post-signal policy has no horizon")
        factors = {
            "seconds": Decimal("1"),
            "minutes": Decimal("60"),
            "hours": Decimal("3600"),
        }
        return self.horizon * factors[self.horizon_unit]


@dataclass(frozen=True, slots=True)
class TouchPolicy:
    accepted_touch_numbers: tuple[int, ...] = ()
    accept_touch_from: int | None = None
    require_exit_from_zone: bool = False
    minimum_exit_distance: NumericRule = NumericRule()
    minimum_time_between_touches: NumericRule = NumericRule()
    maximum_touch_count: int | None = None
    reset_on: tuple[str, ...] = ()
    candidate_cooldown: CandidateCooldown = CandidateCooldown()

    def __post_init__(self) -> None:
        if any(number <= 0 for number in self.accepted_touch_numbers):
            raise ValueError("touch numbers must be positive")
        if len(set(self.accepted_touch_numbers)) != len(self.accepted_touch_numbers):
            raise ValueError("touch numbers must be unique")
        if self.accept_touch_from is not None and self.accept_touch_from <= 0:
            raise ValueError("accept_touch_from must be positive")
        if self.maximum_touch_count is not None and self.maximum_touch_count <= 0:
            raise ValueError("maximum_touch_count must be positive")

    def accepts(self, touch_number: int) -> bool:
        if self.maximum_touch_count is not None and touch_number > self.maximum_touch_count:
            return False
        if touch_number in self.accepted_touch_numbers:
            return True
        return self.accept_touch_from is not None and touch_number >= self.accept_touch_from


@dataclass(frozen=True, slots=True)
class SensorRequirement:
    sensor_id: str
    mode: ContextMode
    max_age_seconds: int | None = None
    min_quality: DataQuality | None = None
    on_missing: ContextFailureAction | None = None
    on_stale: ContextFailureAction | None = None
    on_partial: ContextFailureAction | None = None

    def __post_init__(self) -> None:
        if self.mode in {ContextMode.CONDITION, ContextMode.RANKING} and (
            self.on_missing is None or self.on_stale is None or self.on_partial is None
        ):
            raise ValueError("decision sensor needs explicit missing/stale/partial semantics")


@dataclass(frozen=True, slots=True)
class ContextRequirement:
    context_id: str
    mode: ContextMode
    max_age_seconds: int | None = None
    min_quality: DataQuality | None = None
    on_missing: ContextFailureAction | None = None
    on_stale: ContextFailureAction | None = None
    on_partial: ContextFailureAction | None = None

    def __post_init__(self) -> None:
        if self.mode in {ContextMode.CONDITION, ContextMode.RANKING} and (
            self.on_missing is None or self.on_stale is None or self.on_partial is None
        ):
            raise ValueError("decision context requires explicit missing/stale/partial semantics")


@dataclass(frozen=True, slots=True)
class StrategyCard:
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    name: str
    description: str
    scope: FrozenPolicy
    symbols: tuple[str, ...]
    direction_policy: tuple[TradeDirection, ...]
    entry_policy: FrozenPolicy
    exit_policy: FrozenPolicy
    capital_policy: FrozenPolicy
    protection_policy: FrozenPolicy
    lifecycle_policy: FrozenPolicy
    touch_policy: TouchPolicy
    market_sensor_policy: tuple[SensorRequirement, ...]
    mayak_context_policy: tuple[ContextRequirement, ...]
    dispatcher_context_policy: tuple[ContextRequirement, ...]
    approved_at: datetime
    approved_source: str

    @classmethod
    def build(
        cls,
        *,
        strategy_id: str,
        strategy_version: str,
        name: str,
        description: str,
        scope: FrozenPolicy,
        symbols: tuple[str, ...],
        direction_policy: tuple[TradeDirection, ...],
        entry_policy: FrozenPolicy,
        exit_policy: FrozenPolicy,
        capital_policy: FrozenPolicy,
        protection_policy: FrozenPolicy,
        lifecycle_policy: FrozenPolicy,
        touch_policy: TouchPolicy,
        market_sensor_policy: tuple[SensorRequirement, ...] = (),
        mayak_context_policy: tuple[ContextRequirement, ...] = (),
        dispatcher_context_policy: tuple[ContextRequirement, ...] = (),
        approved_at: datetime | None = None,
        approved_source: str = "owner",
    ) -> StrategyCard:
        identity = {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "scope": scope,
            "symbols": tuple(sorted(symbols)),
            "direction_policy": tuple(sorted(direction.value for direction in direction_policy)),
            "entry_policy": entry_policy,
            "exit_policy": exit_policy,
            "capital_policy": capital_policy,
            "protection_policy": protection_policy,
            "lifecycle_policy": lifecycle_policy,
            "touch_policy": touch_policy,
            "market_sensor_policy": market_sensor_policy,
            "mayak_context_policy": mayak_context_policy,
            "dispatcher_context_policy": dispatcher_context_policy,
        }
        return cls(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_config_fingerprint=fingerprint(identity),
            name=name,
            description=description,
            scope=scope,
            symbols=tuple(sorted(symbols)),
            direction_policy=tuple(direction_policy),
            entry_policy=entry_policy,
            exit_policy=exit_policy,
            capital_policy=capital_policy,
            protection_policy=protection_policy,
            lifecycle_policy=lifecycle_policy,
            touch_policy=touch_policy,
            market_sensor_policy=market_sensor_policy,
            mayak_context_policy=mayak_context_policy,
            dispatcher_context_policy=dispatcher_context_policy,
            approved_at=(approved_at or datetime.now(UTC)).astimezone(UTC),
            approved_source=approved_source,
        )


@dataclass(frozen=True, slots=True)
class StrategyActivation:
    activation_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    enabled: bool
    enabled_at: datetime
    disabled_at: datetime | None = None
    scope: FrozenPolicy = FrozenPolicy("{}")
    operator: str = "owner"
    source: str = "control"


@dataclass(frozen=True, slots=True)
class EntryPlan:
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    strategy_activation_id: str
    entry_plan_version: str
    entry_plan_fingerprint: str
    symbols: tuple[str, ...]
    directions: tuple[TradeDirection, ...]
    predicate: object
    watch_policy: FrozenPolicy
    entry_reference_policy: FrozenPolicy
    local_entry_policy: FrozenPolicy
    context_feature_policy: FrozenPolicy
    context_ranking_policy: FrozenPolicy
    touch_policy: TouchPolicy
    sensor_policy: tuple[SensorRequirement, ...]
    context_policy: tuple[ContextRequirement, ...]
    capital_policy: FrozenPolicy
    lifecycle_policy: FrozenPolicy
    post_signal_outcome_policy: PostSignalOutcomePolicy


@dataclass(frozen=True, slots=True)
class ExitPlan:
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    strategy_activation_id: str
    exit_plan_version: str
    exit_plan_fingerprint: str
    exit_policy: FrozenPolicy
    protection_policy: FrozenPolicy


@dataclass(frozen=True, slots=True)
class MarketFactEnvelope:
    fact_id: str
    event_kind: str
    symbol: str
    observed_at: datetime
    event_at: datetime
    received_at: datetime
    source_refs: tuple[str, ...]
    attributes: FrozenPolicy = FrozenPolicy("{}")
    direction: TradeDirection | None = None


@dataclass(frozen=True, slots=True)
class SensorObservation:
    sensor_id: str
    observed_at: datetime
    quality: DataQuality
    completeness: str
    payload: FrozenPolicy
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ObjectiveContext:
    context_id: str
    context_type: str
    observed_at: datetime
    quality: DataQuality
    completeness: str
    payload: FrozenPolicy
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TradingCapacitySnapshot:
    capacity_snapshot_id: str
    observed_at: datetime
    available_for_new_trading: Decimal | None
    quality: DataQuality
    source_ref: str


@dataclass(frozen=True, slots=True)
class TechnicalReadiness:
    ready: bool
    observed_at: datetime
    reason: str = "ready"


@dataclass(frozen=True, slots=True)
class SensorLink:
    sensor_id: str
    observed: bool
    consumed: bool
    observation_observed_at: datetime | None
    age_seconds: float | None
    quality: DataQuality | None
    status: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextLink:
    requirement_id: str
    context_id: str | None
    context_type: str | None
    mode: ContextMode
    observed: bool
    consumed: bool
    context_observed_at: datetime | None
    age_seconds: float | None
    quality: DataQuality | None
    status: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategySignal:
    signal_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    entry_plan_fingerprint: str
    strategy_activation_id: str
    symbol: str
    direction: TradeDirection
    detected_at: datetime
    source_refs: tuple[str, ...]
    fact_id: str


@dataclass(frozen=True, slots=True)
class StrategyAttempt:
    strategy_attempt_id: str
    signal_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    entry_plan_fingerprint: str
    strategy_activation_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EntryDecision:
    entry_decision_id: str
    strategy_attempt_id: str
    signal_id: str
    code: EntryDecisionCode
    reason: str
    decided_at: datetime
    capacity_snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    execution_request_id: str
    strategy_attempt_id: str
    entry_decision_id: str
    signal_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    entry_plan_fingerprint: str
    symbol: str
    direction: TradeDirection
    requested_at: datetime
    payload: FrozenPolicy


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    notification_id: str
    kind: NotificationKind
    strategy_id: str
    strategy_version: str
    signal_id: str
    strategy_attempt_id: str
    symbol: str
    direction: TradeDirection
    occurred_at: datetime
    reason: str
    requested_amount: Decimal | None = None
    available_amount: Decimal | None = None


@dataclass(frozen=True, slots=True)
class EntryEvaluation:
    signal: StrategySignal
    attempt: StrategyAttempt
    decision: EntryDecision
    execution_request: ExecutionRequest | None
    sensor_links: tuple[SensorLink, ...]
    context_links: tuple[ContextLink, ...]
    notifications: tuple[NotificationEvent, ...]
