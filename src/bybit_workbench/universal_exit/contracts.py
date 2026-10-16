from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import FrozenPolicy, TradeDirection


class ExitEvaluationStatus(StrEnum):
    NO_EXECUTABLE_EXIT_RULES = "NO_EXECUTABLE_EXIT_RULES"
    NO_MATCH = "NO_MATCH"
    DECISION_CREATED = "DECISION_CREATED"
    BLOCKED = "BLOCKED"


class ExitRepeatPolicy(StrEnum):
    ONCE_PER_POSITION = "ONCE_PER_POSITION"
    EACH_MATCH = "EACH_MATCH"


@dataclass(frozen=True, slots=True)
class ExitObservation:
    observation_id: str
    strategy_position_id: str
    symbol: str
    event_at: datetime
    observed_at: datetime
    received_at: datetime
    event_kind: str
    attributes: FrozenPolicy
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("observation_id", "strategy_position_id", "symbol", "event_kind"):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"ExitObservation requires {field}")
        for field in ("event_at", "observed_at", "received_at"):
            if getattr(self, field).tzinfo is None:
                raise ValueError(f"ExitObservation.{field} must be timezone-aware")
        if not self.source_refs or any(not str(ref).strip() for ref in self.source_refs):
            raise ValueError("ExitObservation requires non-empty source_refs")


@dataclass(frozen=True, slots=True)
class ExitEvaluation:
    evaluation_id: str
    observation: ExitObservation
    status: ExitEvaluationStatus
    reason: str
    matched_rule_ids: tuple[str, ...] = ()
    decision: ExitDecision | None = None
    selected_priority: int | None = None
    repeat_policy: ExitRepeatPolicy | None = None

    def __post_init__(self) -> None:
        if not self.evaluation_id.strip():
            raise ValueError("ExitEvaluation requires evaluation_id")
        if self.status is ExitEvaluationStatus.DECISION_CREATED:
            if self.decision is None:
                raise ValueError("DECISION_CREATED evaluation requires decision")
            if self.selected_priority is None or self.repeat_policy is None:
                raise ValueError("DECISION_CREATED requires selected rule metadata")
        elif (
            self.decision is not None
            or self.selected_priority is not None
            or self.repeat_policy is not None
        ):
            raise ValueError("non-decision evaluation cannot carry selected rule metadata")


class ExitActionKind(StrEnum):
    SET_STOP = "SET_STOP"
    SET_TP = "SET_TP"
    SET_PROTECTION = "SET_PROTECTION"
    SET_TRAILING = "SET_TRAILING"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"


@dataclass(frozen=True, slots=True)
class ExitDecision:
    exit_decision_id: str
    strategy_position_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    exit_plan_fingerprint: str
    rule_id: str
    action_kind: ExitActionKind
    requested_mutation: FrozenPolicy
    source_refs: tuple[str, ...]
    decided_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "exit_decision_id",
            "strategy_position_id",
            "strategy_id",
            "strategy_version",
            "strategy_config_fingerprint",
            "exit_plan_fingerprint",
            "rule_id",
        ):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"ExitDecision requires {field}")
        if self.decided_at.tzinfo is None:
            raise ValueError("ExitDecision.decided_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ExitExecutionRequest:
    execution_request_id: str
    exit_decision_id: str
    strategy_position_id: str

    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    exit_plan_fingerprint: str

    account_ref: str
    exchange_position_key: str
    position_idx: int

    symbol: str
    direction: TradeDirection
    action_kind: ExitActionKind
    requested_mutation: FrozenPolicy
    requested_at: datetime
    expires_at: datetime
    strategy_execution_binding_id: str | None = None
    execution_leg: str | None = None
    trading_account_id: str | None = None
    identity_status: str = "LEGACY_UNKNOWN"

    def __post_init__(self) -> None:
        for field in (
            "execution_request_id",
            "exit_decision_id",
            "strategy_position_id",
            "strategy_id",
            "strategy_version",
            "strategy_config_fingerprint",
            "exit_plan_fingerprint",
            "account_ref",
            "exchange_position_key",
            "symbol",
        ):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"ExitExecutionRequest requires {field}")
        if self.position_idx < 0:
            raise ValueError("position_idx cannot be negative")
        if self.requested_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("ExitExecutionRequest timestamps must be timezone-aware")
        if self.expires_at <= self.requested_at:
            raise ValueError("ExitExecutionRequest.expires_at must be after requested_at")
        if self.identity_status not in {"PROVEN", "LEGACY_UNKNOWN"}:
            raise ValueError("ExitExecutionRequest identity_status is invalid")
        identity_fields = (
            self.strategy_execution_binding_id,
            self.execution_leg,
            self.trading_account_id,
        )
        if self.identity_status == "PROVEN":
            if any(value is None or not str(value).strip() for value in identity_fields):
                raise ValueError("PROVEN ExitExecutionRequest requires binding/account lineage")
        elif any(value is not None and str(value).strip() for value in identity_fields):
            raise ValueError(
                "LEGACY_UNKNOWN ExitExecutionRequest cannot claim binding/account lineage"
            )

    @classmethod
    def from_decision(
        cls,
        *,
        execution_request_id: str,
        decision: ExitDecision,
        position: StrategyPosition,
        requested_at: datetime,
        expires_at: datetime,
    ) -> ExitExecutionRequest:
        if decision.strategy_position_id != position.strategy_position_id:
            raise ValueError("ExitDecision/StrategyPosition identity mismatch")
        expected = (
            position.strategy_id,
            position.strategy_version,
            position.strategy_config_fingerprint,
            position.exit_plan_fingerprint,
        )
        actual = (
            decision.strategy_id,
            decision.strategy_version,
            decision.strategy_config_fingerprint,
            decision.exit_plan_fingerprint,
        )
        if actual != expected:
            raise ValueError("ExitDecision/StrategyPosition lineage mismatch")
        return cls(
            execution_request_id=execution_request_id,
            exit_decision_id=decision.exit_decision_id,
            strategy_position_id=position.strategy_position_id,
            strategy_id=position.strategy_id,
            strategy_version=position.strategy_version,
            strategy_config_fingerprint=position.strategy_config_fingerprint,
            exit_plan_fingerprint=position.exit_plan_fingerprint,
            account_ref=position.account_ref,
            exchange_position_key=position.exchange_position_key,
            position_idx=position.position_idx,
            symbol=position.symbol,
            direction=position.direction,
            action_kind=decision.action_kind,
            requested_mutation=decision.requested_mutation,
            requested_at=requested_at.astimezone(UTC),
            expires_at=expires_at.astimezone(UTC),
            strategy_execution_binding_id=position.strategy_execution_binding_id,
            execution_leg=position.execution_leg,
            trading_account_id=position.trading_account_id,
            identity_status=position.identity_status,
        )