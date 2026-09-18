from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import FrozenPolicy, TradeDirection


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
        if self.requested_at.tzinfo is None:
            raise ValueError("ExitExecutionRequest.requested_at must be timezone-aware")

    @classmethod
    def from_decision(
        cls,
        *,
        execution_request_id: str,
        decision: ExitDecision,
        position: StrategyPosition,
        requested_at: datetime,
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
        )
