"""Immediate signed first-fill P90 REAL Universal Exit handoff.

Account-local Execution must obtain complete original MarketSource coverage,
authenticated private position inventory, exact signed fill execution IDs and
the saved StrategyPosition/ExitPlan before invoking this under the same
PostgreSQL transaction. No new Exchange REST/POST occurs here, and no
source-proof provider is synthesized. Existing account-scoped Execution is
the *only* owner that may publish a Bybit mutation command.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import ExitPlan

from .contracts import ExitActionKind, ExitEvaluation, ExitEvaluationStatus
from .engine import UniversalExitEngine
from .p90l90_real_observation import (
    P90RealPostfillSourceWindow,
    ScopedPrivatePositionEvidence,
    build_real_postfill_exit_observation,
)
from .storage import PostgresExitShadowStore

PositionProvider = Callable[[str], Sequence[tuple[StrategyPosition, ExitPlan]]]
ExecutionPort = Callable[[ExitEvaluation, StrategyPosition, ExitPlan], object]


class P90RealPostfillHandoffUnverified(RuntimeError):
    """No complete real first-fill ownership/source/protection proof."""


class P90RealPostfillExitPort:
    """One first fill -> ExitDecision -> normal account-local command ledger."""

    def __init__(
        self,
        connection: Any,
        *,
        position_provider: PositionProvider,
        exit_store: PostgresExitShadowStore,
        execution_port: ExecutionPort,
    ) -> None:
        if (
            not callable(position_provider)
            or exit_store is None
            or not callable(execution_port)
        ):
            raise P90RealPostfillHandoffUnverified(
                "P90_REAL_POSTFILL_INDEPENDENT_OWNERS_REQUIRED"
            )
        self._db = connection
        self._positions = position_provider
        self._exit_store = exit_store
        self._execution_port = execution_port
        self._engine = UniversalExitEngine()

    def __call__(
        self,
        *,
        position: StrategyPosition,
        plan: ExitPlan,
        private: ScopedPrivatePositionEvidence,
        coverage: P90RealPostfillSourceWindow,
        signed_fill_execution_ids: tuple[str, ...],
        exact_entry_order_id: str,
        received_at: datetime,
    ) -> ExitEvaluation:
        # Never claim an atomic handoff from a detached, autocommit/IDLE
        # account connection. The first-fill owner must persist position and
        # Exit decision/request in the same durable ownership transaction.
        info = getattr(self._db, "info", None)
        if getattr(
            getattr(info, "transaction_status", None), "name", None
        ) != "INTRANS":
            raise P90RealPostfillHandoffUnverified(
                "P90_REAL_POSTFILL_OWNER_TRANSACTION_NOT_OPEN"
            )
        candidates = self._positions(position.symbol)
        if len(candidates) != 1 or candidates[0] != (position, plan):
            raise P90RealPostfillHandoffUnverified(
                "P90_REAL_POSTFILL_POSITION_OR_EXIT_PLAN_NOT_DURABLE"
            )
        observation = build_real_postfill_exit_observation(
            position=position, private=private, coverage=coverage,
            signed_fill_execution_ids=signed_fill_execution_ids,
            exact_entry_order_id=exact_entry_order_id,
            received_at=received_at,
        )
        evaluation = self._engine.evaluate(position, plan, observation)
        if evaluation.status is ExitEvaluationStatus.BLOCKED:
            raise P90RealPostfillHandoffUnverified(
                f"P90_REAL_POSTFILL_EXIT_PLAN_DENIED:{evaluation.reason}"
            )
        if (
            evaluation.status is ExitEvaluationStatus.DECISION_CREATED
            and (
                evaluation.decision is None
                or evaluation.decision.action_kind is not ExitActionKind.SET_TRAILING
            )
        ):
            raise P90RealPostfillHandoffUnverified(
                "P90_REAL_POSTFILL_ONLY_EXCHANGE_TRAILING_ALLOWED"
            )
        self._exit_store.record(evaluation, position=position, plan=plan)
        if evaluation.status is ExitEvaluationStatus.DECISION_CREATED:
            self._execution_port(evaluation, position, plan)
        return evaluation
