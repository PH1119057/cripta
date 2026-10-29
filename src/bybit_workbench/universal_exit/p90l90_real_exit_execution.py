"""Fail-closed P90 REAL ExitDecision -> exact single account Execution command.

This is NOT the legacy SHADOW consumer. The already verified native/closed
P90 observation must be committed in the SAME outer source transaction.
No default safety provider, self-declared readiness, or synthetic signed
account facts are accepted. Only the private runtime may send Exchange POSTs.

Production use additionally requires independent live source continuity,
all-account supervisor protection, release parity, and owner LIVE-arm.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import ExitPlan
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)

from .contracts import ExitEvaluation, ExitEvaluationStatus
from .execution_bridge import (
    PreparedRuntimeExitCommand,
    prepare_exit_execution_request,
    prepare_runtime_exit_command,
)
from .execution_store import (
    persist_exit_execution_request,
    publish_runtime_exit_command,
)
from .loader import load_exit_binding


class P90RealExitExecutionUnverified(RuntimeError):
    """There is no proven account-specific right to queue the Exit command."""


SafetyPort = Callable[[StrategyPosition, ExitEvaluation, datetime], bool]


class P90RealExitExecutionPort:
    """Materialize an accepted REAL Exit decision under one account lock.

    The injected independent safety port must re-check fresh MAIN/SUB
    protection, pending mutation, live WS/source epochs and unique Execution
    ownership. It may never infer safety from a valid ExitDecision alone.
    Caller owns the outer native checkpoint transaction: failure rolls back
    MFE, observation, decision, request and command as a single unit.
    """

    def __init__(
        self,
        connection: Any,
        *,
        independently_verified_execution_safety: SafetyPort,
        account_by_strategy: Mapping[str, str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            not callable(independently_verified_execution_safety)
            or set(account_by_strategy) != set(ALLOWED_SYMBOLS)
            or len(set(account_by_strategy.values())) != len(ALLOWED_SYMBOLS)
            or any(
                not isinstance(account, str) or not account.startswith("ta-")
                or len(account) != 35
                for account in account_by_strategy.values()
            )
            or not callable(clock)
        ):
            raise P90RealExitExecutionUnverified(
                "P90_REAL_EXIT_EXACT_ACCOUNT_SAFETY_PROVIDER_MISSING"
            )
        self._db = connection
        self._safety = independently_verified_execution_safety
        self._accounts = dict(account_by_strategy)
        self._clock = clock

    def __call__(
        self,
        evaluation: ExitEvaluation,
        position: StrategyPosition,
        plan: ExitPlan,
    ) -> PreparedRuntimeExitCommand | None:
        if evaluation.status is not ExitEvaluationStatus.DECISION_CREATED:
            if evaluation.decision is not None:
                raise P90RealExitExecutionUnverified(
                    "P90_REAL_EXIT_NON_DECISION_CONTAINS_MUTATION"
                )
            return None
        decision = evaluation.decision
        now = self._clock()
        if decision is None or now.tzinfo is None or now.utcoffset() is None:
            raise P90RealExitExecutionUnverified(
                "P90_REAL_EXIT_DECISION_OR_CLOCK_UNVERIFIED"
            )
        approved = self._accounts.get(position.strategy_id)
        observation = evaluation.observation
        details = observation.attributes.to_dict().get("p90l90_real")
        if (
            position.strategy_version != VERSION
            or position.symbol not in ALLOWED_SYMBOLS.get(position.strategy_id, ())
            or position.trading_account_id != approved
            or position.identity_status != "PROVEN"
            or position.position_idx != 0
            or position.initial_protection_confirmed_at is None
            or not position.strategy_execution_binding_id
            or not position.execution_leg
            or plan.exit_plan_fingerprint != position.exit_plan_fingerprint
            or decision.strategy_position_id != position.strategy_position_id
            or observation.strategy_position_id != position.strategy_position_id
            or observation.event_kind not in (
                "P90L90_REAL_NATIVE_TRADE", "P90L90_REAL_CLOSED_5M"
            )
            or not isinstance(details, Mapping)
            or details.get("trading_account_id") != approved
            or details.get("strategy_execution_binding_id")
               != position.strategy_execution_binding_id
            or details.get("exchange_position_key")
               != position.exchange_position_key
            or details.get("actual_fill_confirmed") is not True
            or details.get("protection_exchange_confirmed") is not True
            or details.get("durable_cursor_verified") is not True
            or details.get("pending_exit_mutation") is not False
            or not any(
                ref.startswith("bybit:signed-position-list:exact-account:")
                for ref in observation.source_refs
            )
        ):
            raise P90RealExitExecutionUnverified(
                "P90_REAL_EXIT_NATIVE_SIGNED_LINEAGE_OR_PROTECTION_UNVERIFIED"
            )
        # This is an atomic extension of the native source checkpoint,
        # NEVER an independent autocommitted command-sending transaction.
        # A missing/IDLE connection state cannot prove same-event lineage.
        info = getattr(self._db, "info", None)
        if (
            info is None
            or getattr(
                getattr(info, "transaction_status", None), "name", None
            ) != "INTRANS"
        ):
            raise P90RealExitExecutionUnverified(
                "P90_REAL_EXIT_SOURCE_TRANSACTION_NOT_OPEN"
            )
        # No request is queued merely because a valid strategy decision
        # exists. Independent, current Exchange-side mutation safety must PASS.
        # The lock protects against parallel commands for the SAME account.
        with self._db.transaction():
            self._db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (approved,),
            )
            current, current_plan = load_exit_binding(
                self._db, strategy_position_id=position.strategy_position_id
            )
            if current != position or current_plan != plan:
                raise P90RealExitExecutionUnverified(
                    "P90_REAL_EXIT_DURABLE_POSITION_OR_PLAN_DRIFT"
                )
            if self._safety(position, evaluation, now) is not True:
                raise P90RealExitExecutionUnverified(
                    "P90_REAL_EXIT_ACCOUNT_MUTATION_SAFETY_UNVERIFIED"
                )
            request = prepare_exit_execution_request(
                decision, position, plan, now=now
            )
            prepared = prepare_runtime_exit_command(
                request, position, now=now
            )
            persist_exit_execution_request(self._db, request)
            publish_runtime_exit_command(
                self._db, prepared, now=now
            )
            return prepared
