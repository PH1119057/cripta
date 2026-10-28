"""Dedicated REAL P90L90 native source consumer; never simulates PAPER orders.

This adapter composes the existing source-pinned atomic 5m/15m / publicTrade
transport and Universal Entry. It does NOT itself issue Exchange commands.
The real Exit observation port MUST independently prove signed account state,
real fill/protection, complete native tick continuity and monotonic MFE.
Until those owners are provided, this consumer cannot start a real epoch.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from .contracts import FrozenPolicy, MarketFactEnvelope
from .engine import AccountAdmissionContext, UniversalEntryEngine
from .p90l90_durable_paper_processor import P90L90DurablePaperProcessor
from .p90l90_legacy_strategy import MAIN_ID, SUB_ID
from .p90l90_observer_paper_consumer import P90L90ObserverPaperConsumer
from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION
from .p90l90_real_native_candidate_owner import P90L90RealNativeCandidateOwner
from .p90l90_shared_candle_bridge import P90SharedCandleLedger
from .registry import ActivePlanRegistry
from .storage import StrategyEntryStore
from .transport_continuity import TradeCursor

AccountAdmissionProvider = Callable[
    [str, datetime],
    tuple[tuple[AccountAdmissionContext, ...], bool],
]
RealExitTickPort = Callable[[MarketFactEnvelope, TradeCursor], None]
RealClosedCandlePort = Callable[[MarketFactEnvelope], None]


class P90L90ObserverRealConsumer(P90L90ObserverPaperConsumer):
    """One real owner per native symbol; v2 PAPER state never used as a fill."""

    def __init__(
        self,
        connection: Any,
        *,
        bundles: tuple[object, ...],
        source_commit: str,
        provenance: FrozenPolicy,
        admission_provider: AccountAdmissionProvider,
        verified_exit_tick_port: RealExitTickPort,
        verified_closed_candle_port: RealClosedCandlePort,
        producer_release_commit: str | None = None,
        accepted_source_commits: frozenset[str] | None = None,
    ) -> None:
        if not bundles or len(bundles) != 2:
            raise ValueError("P90_REAL_EXACT_TWO_STRATEGY_BUNDLES_REQUIRED")
        self._by_symbol: dict[str, Any] = {}
        self._candidate_owners: dict[str, P90L90RealNativeCandidateOwner] = {}
        self._connection = connection
        self._provenance = provenance
        self._admission_provider = admission_provider
        self._verified_exit_tick_port = verified_exit_tick_port
        self._verified_closed_candle_port = verified_closed_candle_port
        registry = ActivePlanRegistry()
        ids: set[str] = set()
        for raw in bundles:
            bundle: Any = raw
            card = bundle.card
            activation = bundle.activation
            plan = bundle.entry_plan
            if (
                card.strategy_id not in (MAIN_ID, SUB_ID)
                or card.strategy_id in ids
                or card.strategy_version != VERSION
                or frozenset(card.symbols) != ALLOWED_SYMBOLS[card.strategy_id]
                or frozenset(plan.symbols) != ALLOWED_SYMBOLS[card.strategy_id]
                or not activation.enabled
                or activation.strategy_id != card.strategy_id
                or activation.strategy_version != VERSION
                or activation.strategy_config_fingerprint
                != card.strategy_config_fingerprint
                or plan.strategy_activation_id != activation.activation_id
            ):
                raise RuntimeError("P90_REAL_NATIVE_EXACT_CARD_OR_ACTIVATION_INVALID")
            ids.add(card.strategy_id)
            registry.register_card(card)
            if registry.activate(activation) != plan:
                raise RuntimeError("P90_REAL_NATIVE_ENTRY_PLAN_REBUILD_MISMATCH")
            for symbol in plan.symbols:
                if symbol in self._by_symbol:
                    raise RuntimeError("P90_REAL_NATIVE_DUPLICATE_SYMBOL_OWNER")
                self._by_symbol[symbol] = bundle
        if ids != {MAIN_ID, SUB_ID}:
            raise RuntimeError("P90_REAL_NATIVE_MISSING_INDEPENDENT_STRATEGY")
        self._entry = UniversalEntryEngine(registry)
        self._store = StrategyEntryStore(connection)
        self._archive = P90SharedCandleLedger(
            connection, source_commit=source_commit,
            producer_release_commit=producer_release_commit,
            accepted_source_commits=accepted_source_commits,
        )
        self._processors: dict[str, P90L90DurablePaperProcessor] = {}
        for symbol, bundle in self._by_symbol.items():
            self._candidate_owners[symbol] = P90L90RealNativeCandidateOwner(
                strategy_id=bundle.card.strategy_id,
                strategy_activation_id=bundle.activation.activation_id,
                strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
                entry_plan_fingerprint=bundle.entry_plan.entry_plan_fingerprint,
                engine=self._entry,
                store=self._store,
            )
            self._processors[symbol] = P90L90DurablePaperProcessor(
                connection,
                source_commit=source_commit,
                producer_release_commit=producer_release_commit,
                accepted_source_commits=accepted_source_commits,
                bar_ledger=self._archive,
                on_native_exit=self._on_native_exit,
                on_closed_candle=self._on_closed_candle,
                candidate_entry_plan_fingerprint=bundle.entry_plan.entry_plan_fingerprint,
                on_proven_candidate=self._on_candidate,
            )
        self._recovered = False
        self._gap_recovery_poisoned = False

    def recover_all(self) -> None:
        """Reconstruct ALL original durable native/closed-bar checkpoints."""
        if self._recovered:
            raise RuntimeError("P90_REAL_NATIVE_DOUBLE_RESTORE")
        for symbol in sorted(self._processors):
            join, entry = self._processors[symbol].recover(symbol=symbol)
            if entry.state is None or entry.state.effective_at > join.closed_5m_at:
                raise RuntimeError("P90_REAL_NATIVE_UNPROVEN_RESTORED_GEOMETRY")
            actual = self._archive.closed_at_or_before(
                symbol=symbol, timeframe="15", through=join.closed_15m_at
            )
            if actual != join.closed_15m_at:
                raise RuntimeError("P90_REAL_NATIVE_UNVERIFIED_15M_WATERMARK")
        self._recovered = True

    def catch_up_closed_histories(
        self, targets: Mapping[str, tuple[datetime, datetime]],
    ) -> int:
        """Cannot bridge a source gap while any REAL exposure/request exists."""
        active = self._connection.execute(
            """SELECT count(*) FROM runtime.position_ownership
                 WHERE strategy_id IN (%s,%s) AND strategy_version=%s
                   AND state IN ('OPEN','RECONCILIATION_REQUIRED')""",
            (MAIN_ID, SUB_ID, VERSION),
        ).fetchone()
        pending = self._connection.execute(
            """SELECT count(*)
                 FROM strategy_entry.execution_requests r
                WHERE r.strategy_id IN (%s,%s) AND r.strategy_version=%s
                  AND NOT EXISTS (
                     SELECT 1 FROM strategy_entry.execution_dispatches d
                      WHERE d.execution_request_id=r.execution_request_id
                  )""",
            (MAIN_ID, SUB_ID, VERSION),
        ).fetchone()
        if active != (0,) or pending != (0,):
            raise RuntimeError("P90_REAL_NATIVE_GAP_WITH_EXPOSURE_OR_PENDING_ORDER")
        # Existing no-native-cursor closed-candle recovery is guarded by exact
        # immutable source and checks the same transport checkpoint under lock.
        return super().catch_up_closed_histories(targets)

    def _on_native_exit(self, fact: MarketFactEnvelope, cursor: TradeCursor) -> None:
        if fact.event_kind != "PUBLIC_TRADE" or cursor.symbol != fact.symbol:
            raise RuntimeError("P90_REAL_NATIVE_EXIT_CURSOR_MISMATCH")
        self._verified_exit_tick_port(fact, cursor)

    def _on_closed_candle(self, fact: MarketFactEnvelope) -> None:
        if fact.event_kind != "CANDLE_CLOSED":
            raise RuntimeError("P90_REAL_NATIVE_CLOSED_BAR_KIND_INVALID")
        self._verified_closed_candle_port(fact)

    def _on_candidate(self, fact: MarketFactEnvelope, cursor: TradeCursor) -> None:
        owner = self._candidate_owners.get(fact.symbol)
        if owner is None:
            raise RuntimeError("P90_REAL_NATIVE_CANDIDATE_NO_EXACT_OWNER")
        at = datetime.now(UTC)
        admissions, proven = self._admission_provider(fact.symbol, at)
        owner.accept(
            fact, cursor,
            account_admissions=admissions,
            arm_and_source_verified=proven,
            admitted_at=at,
            allow_new_signals=True,
        )
