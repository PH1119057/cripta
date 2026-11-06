"""Atomic P90 REAL native-Exit composition via the universal Exit engine.

Dependency ports are independently authenticated source-cursor coverage and
signed account-local private inventory. No default implementation exists,
no PAPER fallback, no account substitution or Exchange order is sent here.
MUST be called under the outer P90 native source checkpoint DB transaction.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import (
    ExitPlan,
    MarketFactEnvelope,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_entry.trade_mirror import MirrorTrade
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

from .contracts import ExitEvaluation, ExitEvaluationStatus
from .engine import UniversalExitEngine
from .p90l90_real_mfe import P90RealMfeUnverified
from .p90l90_real_mfe_ledger import P90RealMfeLedger
from .p90l90_real_observation import (
    ScopedPrivatePositionEvidence,
    build_real_native_exit_observation,
)
from .p90l90_real_pending import pending_private_evaluation
from .storage import PostgresExitShadowStore

PositionProvider = Callable[[str], Sequence[tuple[StrategyPosition, ExitPlan]]]
ExecutionPort = Callable[[ExitEvaluation, StrategyPosition, ExitPlan], object]
SourceCoverageProvider = Callable[[StrategyPosition, TradeCursor], tuple[bool, bool]]
NativePrefixProvider = Callable[[StrategyPosition, TradeCursor], Sequence[MirrorTrade]]
NativeTieOrderProvider = Callable[[StrategyPosition, TradeCursor], bool]
SignedPrivateProvider = Callable[
    [StrategyPosition, TradeCursor], ScopedPrivatePositionEvidence,
]


class P90RealExitNativePort:
    """Evaluate and atomically append MFE + Exit observation/decision for a tick."""

    def __init__(
        self,
        *,
        position_provider: PositionProvider,
        independently_verified_source_coverage: SourceCoverageProvider,
        independently_signed_private_evidence: SignedPrivateProvider,
        source_commit: str,
        mfe_ledger: P90RealMfeLedger,
        exit_store: PostgresExitShadowStore,
        verified_execution_port: ExecutionPort | None = None,
        verified_native_prefix: NativePrefixProvider | None = None,
        private_state_pending: Callable[[StrategyPosition], bool] | None = None,
        independently_verified_native_tie_order: NativeTieOrderProvider | None = None,
    ) -> None:
        self._positions = position_provider
        self._coverage = independently_verified_source_coverage
        self._private = independently_signed_private_evidence
        self._source_commit = source_commit
        self._mfe = mfe_ledger
        self._exit_store = exit_store
        self._execution_port = verified_execution_port
        self._engine = UniversalExitEngine()
        self._prefix = verified_native_prefix
        self._pending = private_state_pending
        self._tie_order = independently_verified_native_tie_order

    def _prove_same_time_order(
        self, position: StrategyPosition, cursor: TradeCursor,
    ) -> bool:
        return (
            self._tie_order(position, cursor)
            if self._tie_order is not None else False
        )

    def __call__(
        self,
        fact: MarketFactEnvelope,
        cursor: TradeCursor,
    ) -> tuple[ExitEvaluation, ...]:
        if (
            fact.event_kind != "PUBLIC_TRADE"
            or fact.symbol != cursor.symbol
            or cursor.traded_at != fact.event_at
            or fact.observed_at != fact.event_at
            or not fact.source_refs
            or fact.received_at < fact.event_at
        ):
            raise P90RealMfeUnverified("P90_REAL_EXIT_NATIVE_ENVELOPE_UNPROVEN")
        try:
            price = Decimal(str(fact.attributes.to_dict()["price"]))
        except (KeyError, ValueError, InvalidOperation, TypeError):
            raise P90RealMfeUnverified("P90_REAL_EXIT_NATIVE_PRICE_INVALID") from None
        if not price.is_finite() or price <= 0:
            raise P90RealMfeUnverified("P90_REAL_EXIT_NATIVE_PRICE_INVALID")
        matching = self._positions(fact.symbol)
        if len(matching) > 1:
            raise P90RealMfeUnverified("P90_REAL_EXIT_DUPLICATE_PHYSICAL_POSITION")
        results: list[ExitEvaluation] = []
        for position, plan in matching:
            if (
                position.symbol != fact.symbol
                or position.strategy_id not in ALLOWED_SYMBOLS
                or position.strategy_version != VERSION
                or plan.strategy_id != position.strategy_id
                or plan.strategy_version != VERSION
                or plan.exit_plan_fingerprint != position.exit_plan_fingerprint
                or position.initial_protection_confirmed_at is None
            ):
                raise P90RealMfeUnverified("P90_REAL_EXIT_WRONG_POSITION_OR_PLAN")
            if self._prefix is not None:
                for trade in self._prefix(position, cursor):
                    earlier = TradeCursor(
                        symbol=trade.symbol, seq=trade.seq, exec_id=trade.exec_id,
                        traded_at=trade.traded_at,
                    )
                    first, contiguous = self._coverage(position, earlier)
                    self._mfe.on_trade(
                        position=position, source_commit=self._source_commit,
                        fact_id="trade-" + trade.exec_id, cursor=earlier,
                        price=Decimal(trade.price), received_at=trade.received_at,
                        source_refs=(f"bybit:publicTrade:{trade.symbol}:{trade.exec_id}",),
                        first_fill_forward_coverage_proven=first,
                        native_continuity_proven=contiguous,
                        prove_same_timestamp_order=(
                            lambda earlier=earlier, position=position: (
                                self._prove_same_time_order(position, earlier)
                            )
                        ),
                    )
            first_contiguous, subsequent_contiguous = self._coverage(position, cursor)
            mfe = self._mfe.on_trade(
                position=position,
                source_commit=self._source_commit,
                fact_id=fact.fact_id,
                cursor=cursor,
                price=price,
                received_at=fact.received_at,
                source_refs=fact.source_refs,
                first_fill_forward_coverage_proven=first_contiguous,
                native_continuity_proven=subsequent_contiguous,
                prove_same_timestamp_order=(
                    lambda position=position, cursor=cursor: (
                        self._prove_same_time_order(position, cursor)
                    )
                ),
            )
            if self._pending is not None and self._pending(position):
                evaluation = pending_private_evaluation(position, fact)
                self._exit_store.record(evaluation, position=position, plan=plan)
                results.append(evaluation)
                continue
            private = self._private(position, cursor)
            observation = build_real_native_exit_observation(
                position=position, checkpoint=mfe, cursor=cursor,
                price=price, native_fact_id=fact.fact_id,
                native_source_refs=fact.source_refs, private=private,
                received_at=max(fact.received_at, private.observed_at),
            )
            evaluation = self._engine.evaluate(position, plan, observation)
            if evaluation.status is ExitEvaluationStatus.BLOCKED:
                raise P90RealMfeUnverified(
                    f"P90_REAL_EXIT_POLICY_DENIED:{evaluation.reason}"
                )
            if (
                evaluation.status is ExitEvaluationStatus.DECISION_CREATED
                and self._execution_port is None
            ):
                raise P90RealMfeUnverified(
                    "P90_REAL_EXIT_EXECUTION_OWNER_MISSING"
                )
            self._exit_store.record(evaluation, position=position, plan=plan)
            if evaluation.status is ExitEvaluationStatus.DECISION_CREATED:
                assert self._execution_port is not None
                self._execution_port(evaluation, position, plan)
            results.append(evaluation)
        return tuple(results)
