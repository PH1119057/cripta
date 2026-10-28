"""Independent P90L90 PAPER consumer on the existing Strategy/Entry/Exit owners.

The consumer is deliberately NOT registered as live Observer ready. The live
Observer must not enable P90 until exact source/activation/position/replay
gates are independently proven. The path below is for a restored, verified
PostgreSQL transport source only; it creates no Bybit requests or real orders.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .contracts import FrozenPolicy, MarketFactEnvelope
from .engine import UniversalEntryEngine
from .p90l90_causal_transport_join import P90TransportEvent
from .p90l90_durable_paper_processor import P90L90DurablePaperProcessor
from .p90l90_immutable_bar_ledger import P90L90ImmutableBarLedger
from .p90l90_legacy_strategy import MAIN_ID, MAIN_SYMBOLS, SUB_ID, SUB_SYMBOLS
from .p90l90_strategy_v2 import VERSION as PAPER_VERSION
from .paper_runtime import PaperTradeRuntime
from .registry import ActivePlanRegistry
from .storage import StrategyEntryStore
from .transport_continuity import TradeCursor

_OWNER_SYMBOLS = {MAIN_ID: MAIN_SYMBOLS, SUB_ID: SUB_SYMBOLS}


class P90L90ObserverPaperConsumer:
    """A dedicated PAPER-only Entry and Exit consumer, never a second Engine state.

    Entry crossings are generated solely by the source-pinned atomic transport
    checkpoint and fed into universal Entry predicates without MarketWatch.
    P90 Exit remains in PaperTradeRuntime under exact DB position/account
    bindings. Ordinary Strategies MUST use a separate engine/normal PAPER path.
    """

    def __init__(
        self,
        connection: Any,
        *,
        bundles: tuple[object, ...],
        source_commit: str,
        provenance: FrozenPolicy,
        producer_release_commit: str | None = None,
        accepted_source_commits: frozenset[str] | None = None,
        shared_candle_source: bool = False,
    ) -> None:
        if not bundles:
            raise ValueError("P90L90 consumer needs exact enabled Strategy bundle")
        registry = ActivePlanRegistry()
        self._by_symbol: dict[str, Any] = {}
        self._connection = connection
        self._provenance = provenance
        p90_bundles: list[Any] = []
        for raw in bundles:
            bundle: Any = raw
            card, activation, plan = bundle.card, bundle.activation, bundle.entry_plan
            expected = _OWNER_SYMBOLS.get(card.strategy_id)
            if (
                expected is None
                or card.strategy_version != PAPER_VERSION
                or not activation.enabled
                or activation.strategy_id != card.strategy_id
                or activation.strategy_version != card.strategy_version
                or activation.strategy_config_fingerprint != card.strategy_config_fingerprint
                or tuple(sorted(plan.symbols)) != tuple(sorted(expected))
                or plan.strategy_activation_id != activation.activation_id
            ):
                raise RuntimeError("P90L90_CONSUMER_UNVERIFIED_EXACT_STRATEGY")
            registry.register_card(card)
            materialized = registry.activate(activation)
            if materialized != plan:
                raise RuntimeError("P90L90_CONSUMER_ENTRY_PLAN_FINGERPRINT_MISMATCH")
            p90_bundles.append(bundle)
            for symbol in plan.symbols:
                if symbol in self._by_symbol:
                    raise RuntimeError("P90L90_CONSUMER_DUPLICATE_SYMBOL_OWNER")
                self._by_symbol[symbol] = bundle
        self._entry = UniversalEntryEngine(registry)
        self._store = StrategyEntryStore(connection)
        self._paper = PaperTradeRuntime(connection, bundles=tuple(p90_bundles))
        if shared_candle_source:
            from .p90l90_shared_candle_bridge import P90SharedCandleLedger

            self._archive = P90SharedCandleLedger(
                connection, source_commit=source_commit,
                producer_release_commit=producer_release_commit,
                accepted_source_commits=accepted_source_commits,
            )
        else:
            self._archive = P90L90ImmutableBarLedger(
                connection, source_commit=source_commit,
                producer_release_commit=producer_release_commit,
                accepted_source_commits=accepted_source_commits,
            )
        self._processors: dict[str, P90L90DurablePaperProcessor] = {}
        for symbol, bundle in self._by_symbol.items():
            self._processors[symbol] = P90L90DurablePaperProcessor(
                connection,
                source_commit=source_commit,
                producer_release_commit=producer_release_commit,
                accepted_source_commits=accepted_source_commits,
                bar_ledger=self._archive if shared_candle_source else None,
                on_native_exit=self._on_native_exit,
                on_closed_candle=self._on_closed_candle,
                candidate_entry_plan_fingerprint=(
                    bundle.entry_plan.entry_plan_fingerprint
                ),
                on_proven_candidate=self._on_candidate,
            )
        self._recovered = False

    def recover_all(self) -> None:
        """Restore exact committed as-of native Entry and Exit state; no writes.

        Any missing bar, source commit mismatch, stale geometry or absent
        checkpoint raises before the first PAPER effect.
        """
        if self._recovered:
            raise RuntimeError("P90L90_CONSUMER_DOUBLE_RECOVERY")
        for symbol in sorted(self._processors):
            join, entry = self._processors[symbol].recover(symbol=symbol)
            if entry.state is None:
                raise RuntimeError(
                    "P90L90_CONSUMER_SEED_NOT_CAUSALLY_WARMED"
                )
            if entry.state.effective_at > join.closed_5m_at:
                raise RuntimeError("P90L90_CONSUMER_FUTURE_ENTRY_GEOMETRY")
            # The existing Entry checkpoint must match an independently
            # reconstructed seven-day/ATR200 geometry from immutable candles.
            # Also verify that the paired 15m watermark names an actual bar.
            last_15 = self._archive.closed_at_or_before(
                symbol=symbol, timeframe="15",
                through=join.closed_15m_at,
            )
            if last_15 != join.closed_15m_at:
                raise RuntimeError("P90L90_CONSUMER_UNVERIFIED_15M_WATERMARK")
            bars = self._archive.history(
                symbol=symbol, timeframe="5",
                through=join.closed_5m_at, limit=260,
            )
            self._paper.bootstrap_p90_l53_history(symbol, bars)
        self._recovered = True

    def recovery_watermarks(self, symbol: str) -> dict[str, datetime]:
        """Source checkpoint boundaries for exact on-demand REST gap fetch."""
        if not self._recovered:
            raise RuntimeError("P90_SHARED_RECOVERY_BEFORE_CHECKPOINT_PROOF")
        if symbol not in self._processors:
            raise ValueError("P90_SHARED_UNRECOGNIZED_SYMBOL")
        if not hasattr(self._archive, "restart_watermarks"):
            raise RuntimeError("P90_SHARED_STORE_NOT_SELECTED")
        return self._archive.restart_watermarks(symbol)

    @property
    def recovered_symbols(self) -> frozenset[str]:
        """Only full exact restart may make symbols eligible for live PAPER."""
        return frozenset(self._processors) if self._recovered else frozenset()

    def accept(self, event: P90TransportEvent) -> tuple[P90TransportEvent, ...]:
        """One source fact; all dependent PAPER effects commit atomically."""
        if not self._recovered:
            raise RuntimeError("P90L90_CONSUMER_NOT_RESTORED")
        processor = self._processors.get(event.symbol)
        if processor is None:
            raise RuntimeError("P90L90_CONSUMER_SYMBOL_HAS_NO_STRATEGY_OWNER")
        return processor.accept(event)

    def _on_closed_candle(self, fact: MarketFactEnvelope) -> None:
        if fact.event_kind != "CANDLE_CLOSED":
            raise RuntimeError("P90L90_CONSUMER_EXPECTED_CLOSED_CANDLE")
        if str(fact.attributes.to_dict().get("timeframe")) == "5":
            # This read sees the just-appended immutable bar on the SAME
            # PostgreSQL connection and inside the causal checkpoint tx.
            bars = self._archive.history(
                symbol=fact.symbol, timeframe="5",
                through=fact.event_at, limit=1,
            )
            self._paper.on_p90_closed_candle(bars[0])

    def _on_native_exit(
        self, fact: MarketFactEnvelope, cursor: TradeCursor
    ) -> None:
        if fact.event_kind != "PUBLIC_TRADE":
            raise RuntimeError("P90L90_CONSUMER_EXIT_REQUIRES_NATIVE_PUBLIC_TRADE")
        attrs = fact.attributes.to_dict()
        self._paper.route_p90l90_native_trade(
            symbol=fact.symbol, exec_id=cursor.exec_id,
            exchange_sequence=cursor.seq, event_at=cursor.traded_at,
            price=Decimal(str(attrs["price"])),
        )

    def _on_candidate(
        self, fact: MarketFactEnvelope, cursor: TradeCursor
    ) -> None:
        bundle = self._by_symbol.get(fact.symbol)
        if bundle is None:
            raise RuntimeError("P90L90_CONSUMER_MISSING_CANDIDATE_OWNER")
        results = self._entry.process(
            fact,
            proven_p90_native_cursor=cursor,
            account_ref="BYBIT:UNIFIED",
            real_admission_required_for=frozenset(),
            allow_new_signals=True,
        )
        for evaluation in results:
            if (
                evaluation.signal.strategy_activation_id != bundle.activation.activation_id
                or evaluation.signal.strategy_id != bundle.card.strategy_id
                or evaluation.signal.entry_plan_fingerprint
                != bundle.entry_plan.entry_plan_fingerprint
                or evaluation.decision is not None
                or evaluation.execution_request is not None
            ):
                raise RuntimeError("P90L90_CONSUMER_CROSS_STRATEGY_OR_REAL_EFFECT")
            self._store.record_evaluation(
                evaluation, provenance=self._provenance,
            )
            self._paper.create_p90l90_native_paper_entry(
                evaluation, bundle, exec_id=cursor.exec_id,
                exchange_sequence=cursor.seq, event_at=cursor.traded_at,
                native_trade_price=Decimal(str(fact.attributes.to_dict()["price"])),
                raw_trade_fact_id="trade-" + cursor.exec_id,
            )
