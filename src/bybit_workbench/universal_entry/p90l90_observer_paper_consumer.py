"""Independent P90L90 PAPER consumer on the existing Strategy/Entry/Exit owners.

The consumer is deliberately NOT registered as live Observer ready. The live
Observer must not enable P90 until exact source/activation/position/replay
gates are independently proven. The path below is for a restored, verified
PostgreSQL transport source only; it creates no Bybit requests or real orders.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .contracts import FrozenPolicy, MarketFactEnvelope
from .engine import UniversalEntryEngine
from .fingerprint import canonical_json
from .p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
    P90TransportEvent,
)
from .p90l90_causal_fact_codec import encode_p90l90_fact
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
        # Both stores expose the same causal read contract, but only the
        # MarketSource bridge is allowed in production after migration.
        self._archive: Any
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

    def catch_up_closed_histories(
        self, targets: Mapping[str, tuple[datetime, datetime]],
    ) -> int:
        """Atomically advance source-verified closed bars, NEVER missed trades.

        Only a previously disabled, trade-cursor-free P90 epoch with NO PAPER
        position/order can safely be caught up by OHLC alone. This preserves
        original source facts and writes audit of each previous checkpoint
        before its normal forward-only generation advance. Any uncertainty
        fails before the first effect; real/native trade cursor comes later.
        """
        if not self._recovered:
            raise RuntimeError("P90_SHARED_RECOVERY_BEFORE_CHECKPOINT_PROOF")
        from .p90l90_shared_candle_bridge import P90SharedCandleLedger

        if not isinstance(self._archive, P90SharedCandleLedger):
            raise RuntimeError("P90_SHARED_CANDLE_STORE_NOT_SELECTED")
        if set(targets) != set(self._processors):
            raise RuntimeError("P90_SHARED_RECOVERY_MISSING_STRATEGY_SYMBOL")
        total = 0
        with self._connection.transaction():
            forbidden = self._connection.execute(
                """SELECT count(*) FROM strategy_entry.paper_positions
                    WHERE strategy_id IN (%s,%s) AND state='OPEN'""",
                (MAIN_ID, SUB_ID),
            ).fetchone()
            if forbidden != (0,):
                raise RuntimeError("P90_SHARED_RECOVERY_OPEN_PAPER_POSITION")
            orders = self._connection.execute(
                """SELECT count(*) FROM strategy_entry.paper_orders
                    WHERE strategy_id IN (%s,%s)""",
                (MAIN_ID, SUB_ID),
            ).fetchone()
            if orders != (0,):
                raise RuntimeError("P90_SHARED_RECOVERY_LEGACY_PAPER_ORDER")
            # Preflight ALL symbols before mutating any checkpoint, including
            # complete shared-source 5m/15m continuity and old native cursor.
            plans: dict[str, tuple[tuple[str, Any], ...]] = {}
            originals: dict[str, tuple[Any, ...]] = {}
            for symbol in sorted(targets):
                join, entry = self._processors[symbol].recover(symbol=symbol)
                if (
                    join.pending or entry.previous_price is not None
                    or entry.previous_at is not None or entry.seen
                    or join.last_emitted_trade_sequence is not None
                ):
                    raise RuntimeError("P90_SHARED_RECOVERY_NATIVE_TRADES_UNPROVEN")
                row = self._connection.execute(
                    """SELECT checkpoint_json,entry_checkpoint_json,
                              source_commit,generation
                         FROM strategy_entry.p90l90_causal_transport_checkpoints
                        WHERE symbol=%s FOR UPDATE""",
                    (symbol,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("P90_SHARED_RECOVERY_CHECKPOINT_MISSING")
                originals[symbol] = row
                target5, target15 = targets[symbol]
                plans[symbol] = self._archive.closed_gap_plan(
                    symbol=symbol, through_5m=target5,
                    through_15m=target15,
                )
            # The existing atomic processor emits only actual closed OHLC
            # boundary facts; it sees no historical PUBLIC_TRADE, so no
            # past Entry/Exit event can be fabricated.
            for symbol in sorted(targets):
                items = plans[symbol]
                for timeframe, candle in items:
                    fact = MarketFactEnvelope(
                        fact_id=(
                            f"p90-source-gap:{symbol}:{timeframe}:"
                            f"{candle.closed_at.isoformat()}"
                        ),
                        event_kind="CANDLE_CLOSED",
                        symbol=symbol, event_at=candle.closed_at,
                        observed_at=candle.closed_at,
                        received_at=datetime.now(UTC),
                        source_refs=(
                            f"bybit:REST:linear:kline:{symbol}:"
                            f"{timeframe}:{candle.opened_at.isoformat()}",
                        ),
                        attributes=FrozenPolicy.from_mapping({
                            "timeframe": timeframe,
                            "opened_at": candle.opened_at.isoformat(),
                            "closed_at": candle.closed_at.isoformat(),
                            "open": str(candle.open),
                            "high": str(candle.high),
                            "low": str(candle.low),
                            "close": str(candle.close),
                            "volume": str(candle.volume),
                        }),
                    )
                    self._processors[symbol].accept(
                        encode_p90l90_fact(fact),
                    )
                if not items:
                    continue
                before = originals[symbol]
                after = self._connection.execute(
                    """SELECT checkpoint_json,entry_checkpoint_json,
                              source_commit,generation
                         FROM strategy_entry.p90l90_causal_transport_checkpoints
                        WHERE symbol=%s FOR UPDATE""",
                    (symbol,),
                ).fetchone()
                if (
                    after is None or after[2] != before[2]
                    or after[3] != before[3] + len(items)
                ):
                    raise RuntimeError("P90_SHARED_RECOVERY_GENERATION_DRIFT")
                before_join = P90L90CausalTransportJoin.restore(before[0])
                target5, target15 = targets[symbol]
                self._connection.execute(
                    """INSERT INTO strategy_entry.p90l90_candle_gap_recovery_audit
                       (symbol,old_generation,new_generation,
                        old_checkpoint_json,old_entry_checkpoint_json,
                        old_source_commit,old_5m_closed_at,old_15m_closed_at,
                        through_5m_closed_at,through_15m_closed_at,
                        recovered_bar_count,market_source_id,
                        producer_release_commit)
                       VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,
                               %s,%s,%s,%s,%s)""",
                    (
                        symbol, before[3], after[3],
                        canonical_json(before[0]),
                        canonical_json(before[1]), before[2],
                        before_join.closed_5m_at,
                        before_join.closed_15m_at, target5, target15,
                        len(items), self._archive.source_id(symbol),
                        self._provenance.to_dict()["source_commit"],
                    ),
                )
                total += len(items)
        return total

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
