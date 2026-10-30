"""Account-scoped P90L90 REAL native Entry callback for the atomic source owner.

This callback is NOT a replacement for native market-data checkpoint recovery,
a producer release proof, the account LIVE arm or the universal Exit owner.
It must be called INSIDE the same PostgreSQL transaction that advances the
original bybit publicTrade cursor, after exact causal geometry reconstruction.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from .contracts import EntryEvaluation, FrozenPolicy, MarketFactEnvelope
from .engine import AccountAdmissionContext, UniversalEntryEngine
from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION
from .storage import StrategyEntryStore
from .transport_continuity import TradeCursor


class P90L90RealNativeCandidateOwner:
    """Pure Entry routing plus exact-identity durable evaluation persistence.

    No route exists without one independently proven account-scoped binding
    and positive external real-epoch/arm admission. A blocked selection
    NEVER silently creates a PAPER order or simulation position.
    """

    def __init__(
        self,
        *,
        strategy_id: str,
        strategy_activation_id: str,
        strategy_config_fingerprint: str,
        entry_plan_fingerprint: str,
        engine: UniversalEntryEngine,
        store: StrategyEntryStore,
        provenance: FrozenPolicy,
    ) -> None:
        if (
            strategy_id not in ALLOWED_SYMBOLS
            or not strategy_activation_id.strip()
            or not strategy_config_fingerprint.strip()
            or not entry_plan_fingerprint.strip()
        ):
            raise ValueError("P90_REAL_NATIVE_OWNER_IDENTITY_INVALID")
        self._strategy_id = strategy_id
        self._activation_id = strategy_activation_id
        self._config_fingerprint = strategy_config_fingerprint
        self._entry_fingerprint = entry_plan_fingerprint
        source = provenance.to_dict()
        source_commit = source.get("source_commit")
        if (
            not isinstance(source_commit, str)
            or len(source_commit) != 40
            or any(char not in "0123456789abcdef" for char in source_commit)
            or not str(source.get("data_lineage_id") or "").strip()
        ):
            raise ValueError("P90_REAL_NATIVE_OWNER_LINEAGE_ATTESTATION_REQUIRED")
        self._engine = engine
        self._store = store
        self._provenance = provenance

    def accept(
        self,
        fact: MarketFactEnvelope,
        cursor: TradeCursor,
        *,
        account_admissions: Sequence[AccountAdmissionContext],
        arm_and_source_verified: bool,
        admitted_at: datetime,
        allow_new_signals: bool,
    ) -> tuple[EntryEvaluation, ...]:
        """Original native candidate -> UniversalEntryEngine -> durable signal.

        The caller owns the source checkpoint transaction and must supply a
        separately proven, current account binding + release-scoped live arm.
        Only its exact account's admission port can reserve funds/physical slot.
        """
        if admitted_at.tzinfo is None:
            raise RuntimeError("P90_REAL_NATIVE_ADMISSION_TIME_INVALID")
        if (
            not arm_and_source_verified
            or len(account_admissions) != 1
            or fact.symbol not in ALLOWED_SYMBOLS[self._strategy_id]
            or cursor.symbol != fact.symbol
            or fact.event_kind != "P90L90_RAW_H9_PREV7D_CROSSING"
        ):
            raise RuntimeError("P90_REAL_NATIVE_OWNER_SOURCE_OR_ARM_UNVERIFIED")
        scope = account_admissions[0]
        binding = scope.binding
        if (
            binding.strategy_activation_id != self._activation_id
            or binding.execution_leg != "PRIMARY"
            or not binding.trading_account_id
            or not binding.strategy_execution_binding_id
            or scope.entry_admission_port is None
            or not scope.technical_readiness.ready
            or scope.capacity is None
        ):
            raise RuntimeError("P90_REAL_NATIVE_OWNER_EXACT_ACCOUNT_NOT_READY")
        evaluations = self._engine.process(
            fact,
            proven_p90_native_cursor=cursor,
            real_admission_required_for=frozenset({self._activation_id}),
            real_admission_ready_for=frozenset({self._activation_id}),
            account_admissions={self._activation_id: (scope,)},
            account_ref=scope.account_ref,
            admission_time=admitted_at.astimezone(UTC),
            allow_new_signals=allow_new_signals,
        )
        for evaluation in evaluations:
            signal = evaluation.signal
            if (
                signal.strategy_id != self._strategy_id
                or signal.strategy_version != VERSION
                or signal.strategy_config_fingerprint != self._config_fingerprint
                or signal.strategy_activation_id != self._activation_id
                or signal.entry_plan_fingerprint != self._entry_fingerprint
                or evaluation.decision is not None
                or evaluation.execution_request is not None
                or evaluation.notifications
                or len(evaluation.account_outcomes) != 1
                or evaluation.account_outcomes[0].binding != binding
            ):
                raise RuntimeError("P90_REAL_NATIVE_OWNER_CROSS_ACCOUNT_OR_PAPER_EFFECT")
            self._store.record_evaluation(
                evaluation, provenance=self._provenance,
            )
        return evaluations
