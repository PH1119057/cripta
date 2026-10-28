"""Exactly the new P90 v2 MAIN/SUB Strategy Engine route, NO archived R1.

This verifies real Engine predicate and execution-intent output from the
same first-adjacent native crossing the durable PAPER consumer generates.
No DB, real Exchange order, wallet reservation or legacy R1 activation.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy, MarketFactEnvelope, StrategyActivation,
)
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90Crossing, P90L90EntryState,
)
from bybit_workbench.universal_entry.p90l90_crossing_fact import p90l90_crossing_fact
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
from bybit_workbench.universal_entry.registry import ActivePlanRegistry
from bybit_workbench.universal_entry.engine import UniversalEntryEngine
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

AT = datetime(2026, 10, 28, 9, 30, tzinfo=UTC)
EMBARGO = StrategyHardStopEmbargoPolicy.parse({
    "enabled": True, "hardstop_count": 2, "rolling_hours": 48,
    "new_entry_pause_hours": 48, "close_existing_positions": False,
    "domain": "STRATEGY_VERSION",
})


def test_p90_only_two_independent_v2_engines_emit_paper_intent_without_real_order():
    registry = ActivePlanRegistry()
    approved = {MAIN_ID: EMBARGO, SUB_ID: EMBARGO}
    cards = new_version_cards(approved_at=AT, policy_by_strategy_id=approved)
    fingerprints = {}
    for card in cards:
        act = StrategyActivation(
            activation_id="p90-ci-"+card.strategy_id,
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=True, enabled_at=AT,
        )
        registry.register_card(card)
        plan, _ = materialize_plans(card, act)
        assert registry.activate(act) == plan
        fingerprints[card.strategy_id] = plan.entry_plan_fingerprint
    engine = UniversalEntryEngine(registry)
    for index, card in enumerate(cards):
        symbol = card.symbols[0]
        cursor = TradeCursor(
            symbol, "p90-ci-exec-"+str(index), 1001+index,
            AT+timedelta(seconds=1),
        )
        native = MarketFactEnvelope(
            fact_id="trade-"+cursor.exec_id, symbol=symbol,
            event_kind="PUBLIC_TRADE",
            event_at=cursor.traded_at, observed_at=cursor.traded_at,
            received_at=cursor.traded_at+timedelta(milliseconds=100),
            source_refs=(f"bybit:publicTrade:{symbol}:{cursor.exec_id}",),
            attributes=FrozenPolicy.from_mapping({
                "price":"99","size":"1","taker_side":"Sell",
            }),
        )
        geom = P90L90EntryState(
            effective_at=AT,
            l53_low_raw=Decimal("100"), l53_high_raw=Decimal("110"),
            h9_5m_low_raw=Decimal("98"), h9_5m_high_raw=Decimal("112"),
            h9_15m_low_raw=Decimal("98"), h9_15m_high_raw=Decimal("112"),
            raw_h9_union_span=Decimal("14"),
            working_width_pct=Decimal("1"),
            previous_seven_day_median=Decimal("1"),
            long_depth_pct=Decimal("4"), short_depth_pct=None,
        )
        crossing = P90L90Crossing(
            side="LONG",event_at=cursor.traded_at,effective_at=AT,
            current_price=Decimal("99"), previous_price=Decimal("101"),
            boundary=Decimal("100"), depth_pct=Decimal("4"),
        )
        fact = p90l90_crossing_fact(
            entry_plan_fingerprint=fingerprints[card.strategy_id],
            source_fact=native,crossing=crossing,geometry=geom,
        )
        result = engine.process(
            fact, proven_p90_native_cursor=cursor,
            account_ref="BYBIT:UNIFIED", allow_new_signals=True,
            real_admission_required_for=frozenset(),
        )
        assert len(result) == 1
        evaluation = result[0]
        assert evaluation.signal.strategy_id == card.strategy_id
        assert evaluation.signal.symbol == symbol
        assert evaluation.signal.entry_plan_fingerprint == fingerprints[card.strategy_id]
        assert evaluation.execution_intent is not None
        assert evaluation.execution_request is None
        assert evaluation.signal.strategy_id in {MAIN_ID, SUB_ID}
