"""P90 verified native crossing must reuse the real Entry engine, no Watch rerun."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
    StrategyActivation,
    TradeDirection,
)
from bybit_workbench.universal_entry.engine import UniversalEntryEngine
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90Crossing,
    P90L90EntryState,
)
from bybit_workbench.universal_entry.p90l90_crossing_fact import p90l90_crossing_fact
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
from bybit_workbench.universal_entry.registry import ActivePlanRegistry
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)


def _engine():
    embargo = StrategyHardStopEmbargoPolicy.parse({
        "enabled": True, "hardstop_count": 2,
        "rolling_hours": 48, "new_entry_pause_hours": 48,
        "close_existing_positions": False, "domain": "STRATEGY_VERSION",
    })
    cards = new_version_cards(
        approved_at=B,
        policy_by_strategy_id={MAIN_ID: embargo, SUB_ID: embargo},
    )
    card = next(x for x in cards if x.strategy_id == MAIN_ID)
    activation = StrategyActivation(
        activation_id="p90-native-test",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True, enabled_at=B,
    )
    reg = ActivePlanRegistry()
    reg.register_card(card)
    plan = reg.activate(activation)
    return UniversalEntryEngine(reg), plan


def _candidate(entry_fp: str):
    at = B+timedelta(seconds=2)
    native = MarketFactEnvelope(
        fact_id="trade-exec-123", event_kind="PUBLIC_TRADE",
        symbol="LTCUSDT", observed_at=at, event_at=at,
        received_at=at+timedelta(milliseconds=60),
        source_refs=("bybit:publicTrade:LTCUSDT:exec-123",),
        attributes=FrozenPolicy.from_mapping({
            "price": "99", "size": "5", "taker_side": "Sell",
        }),
    )
    geometry = P90L90EntryState(
        effective_at=B,
        l53_low_raw=Decimal("100"), l53_high_raw=Decimal("110"),
        h9_5m_low_raw=Decimal("98"), h9_5m_high_raw=Decimal("112"),
        h9_15m_low_raw=Decimal("98"), h9_15m_high_raw=Decimal("112"),
        raw_h9_union_span=Decimal("14"),
        working_width_pct=Decimal("1"), previous_seven_day_median=Decimal("1"),
        long_depth_pct=Decimal("4"), short_depth_pct=None,
    )
    crossing = P90L90Crossing(
        side="LONG", effective_at=B, event_at=at,
        previous_price=Decimal("101"), current_price=Decimal("99"),
        boundary=Decimal("100"), depth_pct=Decimal("4"),
    )
    candidate = p90l90_crossing_fact(
        entry_plan_fingerprint=entry_fp,
        source_fact=native, crossing=crossing, geometry=geometry,
    )
    return candidate, TradeCursor("LTCUSDT","exec-123",123,at)


def test_p90_precomputed_uses_native_candidate_but_never_updates_market_watch():
    engine, plan = _engine()
    candidate, cursor = _candidate(plan.entry_plan_fingerprint)
    output = engine.process(
        candidate, proven_p90_native_cursor=cursor,
        allow_new_signals=True, account_ref="BYBIT:UNIFIED",
    )
    assert len(output) == 1
    assert output[0].signal.fact_id == candidate.fact_id
    assert output[0].signal.direction == TradeDirection.LONG
    assert output[0].decision is None
    assert output[0].request is None
    # An independent Watch on a raw public trade would still be unseeded.
    fresh, _ = _engine()
    raw = replace(candidate, event_kind="PUBLIC_TRADE")
    assert fresh.process(raw) == ()


@pytest.mark.parametrize("corruption", ["native-id", "time", "fingerprint", "fill"])
def test_p90_native_candidate_provenance_fails_before_any_engine_state(corruption):
    engine, plan = _engine()
    candidate, cursor = _candidate(plan.entry_plan_fingerprint)
    if corruption == "native-id":
        cursor = replace(cursor, exec_id="other")
    elif corruption == "time":
        cursor = replace(cursor, traded_at=cursor.traded_at+timedelta(seconds=1))
    elif corruption == "fingerprint":
        candidate = replace(candidate, fact_id="watch-p90l90-forged")
    else:
        attrs = candidate.attributes.to_dict()
        attrs["modeled_exchange_fill"] = True
        candidate = replace(candidate, attributes=FrozenPolicy.from_mapping(attrs))
    with pytest.raises(RuntimeError, match="P90L90_PRECOMPUTED_"):
        engine.process(candidate, proven_p90_native_cursor=cursor)


def test_p90_candidate_cannot_enter_real_admission_or_use_raw_input():
    engine, plan = _engine()
    candidate, cursor = _candidate(plan.entry_plan_fingerprint)
    with pytest.raises(RuntimeError, match="REAL_ADMISSION_FORBIDDEN"):
        engine.process(
            candidate, proven_p90_native_cursor=cursor,
            real_admission_required_for=frozenset({"p90-native-test"}),
        )
    with pytest.raises(RuntimeError, match="SOURCE_OR_PLAN_INVALID"):
        engine.process(
            replace(candidate, event_kind="PUBLIC_TRADE"),
            proven_p90_native_cursor=cursor,
        )
