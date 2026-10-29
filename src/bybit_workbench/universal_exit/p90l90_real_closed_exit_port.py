"""Exact native closed-5m P90 REAL Exit projection with immutable RAW36.

All providers are external trusted ports: a previously verified shared
MarketSource closed-candle ledger, persisted after-fill native MFE,
authenticated private MAIN/SUB account, and proven causal market continuity.
No provider is silently synthesized here. No Exchange order is sent here.
The caller must own one atomic source-checkpoint/Exit DB transaction.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from bybit_workbench.domain.models import Candle
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import (
    ExitPlan,
    FrozenPolicy,
    MarketFactEnvelope,
)
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

from .contracts import ExitEvaluation, ExitEvaluationStatus, ExitObservation
from .engine import UniversalExitEngine
from .p90l90_real_closed_geometry import (
    P90RealClosedGeometryUnverified,
    opposite_raw_l53_from_closed_fives,
)
from .p90l90_real_mfe_ledger import P90RealMfeLedger
from .p90l90_real_observation import (
    ScopedPrivatePositionEvidence,
    build_real_native_exit_observation,
)
from .storage import PostgresExitShadowStore

PositionProvider = Callable[[str], Sequence[tuple[StrategyPosition, ExitPlan]]]
ExecutionPort = Callable[[ExitEvaluation, StrategyPosition, ExitPlan], object]
ClosedHistoryProvider = Callable[[str, datetime], Sequence[Candle]]
PrivatePositionProvider = Callable[
    [StrategyPosition, datetime], ScopedPrivatePositionEvidence,
]
ClosedCausalSourceProof = Callable[[str, datetime], bool]


class P90RealClosedExitPort:
    """Project a verified bar-boundary dynamic RAW TP through Universal Exit."""

    def __init__(
        self,
        *,
        position_provider: PositionProvider,
        independently_verified_closed_history: ClosedHistoryProvider,
        independent_causal_source_proof: ClosedCausalSourceProof,
        independently_signed_private_evidence: PrivatePositionProvider,
        source_commit: str,
        mfe_ledger: P90RealMfeLedger,
        exit_store: PostgresExitShadowStore,
        verified_execution_port: ExecutionPort | None = None,
    ) -> None:
        self._positions = position_provider
        self._bars = independently_verified_closed_history
        self._continuity = independent_causal_source_proof
        self._private = independently_signed_private_evidence
        self._source_commit = source_commit
        self._mfe = mfe_ledger
        self._exit_store = exit_store
        self._execution_port = verified_execution_port
        self._engine = UniversalExitEngine()

    def __call__(self, fact: MarketFactEnvelope) -> tuple[ExitEvaluation, ...]:
        raw = fact.attributes.to_dict()
        if (
            fact.event_kind != "CANDLE_CLOSED"
            or raw.get("timeframe") != "5"
            or fact.event_at != fact.observed_at
            or fact.received_at < fact.event_at
            or not fact.source_refs
            or raw.get("closed_at") != fact.event_at.astimezone(UTC).isoformat()
            or not self._continuity(fact.symbol, fact.event_at)
        ):
            raise P90RealClosedGeometryUnverified(
                "P90_REAL_CLOSED_5M_SOURCE_OR_CONTINUITY_UNVERIFIED"
            )
        matches = self._positions(fact.symbol)
        if len(matches) > 1:
            raise P90RealClosedGeometryUnverified(
                "P90_REAL_CLOSED_5M_DUPLICATE_OWNER"
            )
        results: list[ExitEvaluation] = []
        for position, plan in matches:
            if (
                position.symbol != fact.symbol
                or position.strategy_id not in ALLOWED_SYMBOLS
                or position.strategy_version != VERSION
                or plan.strategy_id != position.strategy_id
                or plan.strategy_version != VERSION
                or plan.strategy_config_fingerprint
                != position.strategy_config_fingerprint
                or plan.exit_plan_fingerprint != position.exit_plan_fingerprint
                or fact.event_at <= position.fill_at
            ):
                raise P90RealClosedGeometryUnverified(
                    "P90_REAL_CLOSED_5M_POSITION_LINEAGE_INVALID"
                )
            candles = self._bars(fact.symbol, fact.event_at)
            target = opposite_raw_l53_from_closed_fives(
                symbol=fact.symbol, direction=position.direction,
                closed_at=fact.event_at, candles=candles,
            )
            if (
                str(candles[-1].open) != str(Decimal(str(raw.get("open"))))
                or str(candles[-1].high) != str(Decimal(str(raw.get("high"))))
                or str(candles[-1].low) != str(Decimal(str(raw.get("low"))))
                or str(candles[-1].close) != str(Decimal(str(raw.get("close"))))
            ):
                raise P90RealClosedGeometryUnverified(
                    "P90_REAL_CLOSED_5M_EMITTED_BAR_DRIFT"
                )
            mfe, native_fact_id, native_refs = self._mfe.load_latest(
                position=position, source_commit=self._source_commit,
            )
            if mfe.cursor_at >= fact.event_at:
                raise P90RealClosedGeometryUnverified(
                    "P90_REAL_CLOSED_5M_NATIVE_CURSOR_NONCAUSAL"
                )
            cursor = TradeCursor(
                symbol=mfe.symbol, seq=mfe.cursor_seq,
                exec_id=mfe.cursor_exec_id, traded_at=mfe.cursor_at,
            )
            private = self._private(position, fact.event_at)
            if private.observed_at < fact.event_at:
                raise P90RealClosedGeometryUnverified(
                    "P90_REAL_CLOSED_5M_SIGNED_INVENTORY_TOO_OLD"
                )
            native = build_real_native_exit_observation(
                position=position, checkpoint=mfe, cursor=cursor,
                price=mfe.last_native_price,
                native_fact_id=native_fact_id,
                native_source_refs=native_refs, private=private,
                received_at=max(fact.received_at, private.observed_at),
            )
            attrs = native.attributes.to_dict()
            real = attrs.get("p90l90_real")
            if not isinstance(real, dict):
                raise P90RealClosedGeometryUnverified(
                    "P90_REAL_CLOSED_5M_PRIVATE_FACT_INVALID"
                )
            real["opposite_raw_l53_target"] = str(target)
            real["closed_geometry_proven"] = True
            observation = ExitObservation(
                observation_id="p90-real-closed-" + fingerprint({
                    "position_id": position.strategy_position_id,
                    "plan": position.exit_plan_fingerprint,
                    "source_commit": self._source_commit,
                    "native_cursor": (mfe.cursor_seq, mfe.cursor_exec_id),
                    "closed_fact_id": fact.fact_id,
                })[:32],
                strategy_position_id=position.strategy_position_id,
                symbol=fact.symbol,
                event_at=fact.event_at,
                observed_at=fact.observed_at,
                received_at=max(fact.received_at, private.observed_at),
                event_kind="P90L90_REAL_CLOSED_5M",
                attributes=FrozenPolicy.from_mapping(attrs),
                source_refs=(
                    *native.source_refs, *fact.source_refs,
                    f"closed-candle:{fact.fact_id}",
                ),
            )
            evaluation = self._engine.evaluate(position, plan, observation)
            if evaluation.status is ExitEvaluationStatus.BLOCKED:
                raise P90RealClosedGeometryUnverified(
                    f"P90_REAL_CLOSED_5M_EXIT_POLICY_DENIED:{evaluation.reason}"
                )
            if evaluation.status is ExitEvaluationStatus.DECISION_CREATED:
                if self._execution_port is None:
                    raise P90RealClosedGeometryUnverified(
                        "P90_REAL_CLOSED_5M_EXECUTION_OWNER_MISSING"
                    )
            self._exit_store.record(evaluation, position=position, plan=plan)
            if evaluation.status is ExitEvaluationStatus.DECISION_CREATED:
                assert self._execution_port is not None
                self._execution_port(evaluation, position, plan)
            results.append(evaluation)
        return tuple(results)
