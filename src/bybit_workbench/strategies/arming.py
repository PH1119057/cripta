from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain.types import AppMode, AppState
from bybit_workbench.historical.gate import (
    HistoricalEligibilityGate,
    HistoricalEligibilityQuery,
    HistoricalGateDecision,
)

from .registry import StrategyKind, StrategyRegistry


@dataclass(frozen=True, slots=True)
class ArmedStrategy:
    strategy_id: str
    strategy_version: str
    parameters: dict[str, object]
    historical_gate: HistoricalGateDecision
    requires_historical_validation: bool = True


class StrategyArmingService:
    def __init__(
        self,
        registry: StrategyRegistry,
        gate: HistoricalEligibilityGate,
        state_machine: AppStateMachine,
    ) -> None:
        self.registry = registry
        self.gate = gate
        self.state_machine = state_machine

    def arm(
        self,
        strategy_id: str,
        parameters: Mapping[str, object] | None,
        *,
        mode: AppMode,
        historical_query: HistoricalEligibilityQuery | None = None,
    ) -> ArmedStrategy:
        if mode not in {AppMode.TESTNET, AppMode.LIVE}:
            raise PermissionError("automatic strategy arming requires Mainnet or legacy Testnet")
        if self.state_machine.state is not AppState.READY:
            raise PermissionError("engine must be READY before strategy arming")
        registration = self.registry.get(strategy_id)
        if registration.kind is StrategyKind.RESERVED:
            raise PermissionError("strategy rules are not formalized or implemented")
        resolved = registration.resolve_parameters(parameters)
        decision = self.gate.require(registration, resolved, historical_query)
        self.state_machine.transition(
            AppState.ARMED,
            f"strategy {strategy_id} passed arming gates",
        )
        return ArmedStrategy(
            strategy_id,
            registration.metadata.version,
            resolved,
            decision,
            registration.requires_historical_validation,
        )
