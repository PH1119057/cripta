"""P90L90 native real Entry acceptance remains proof/account scoped.

No PostgreSQL, Exchange or StrategyActivation mutation in this test module.
Historical PAPER v2 and owner-approved REAL v3 keep distinct paths.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
    StrategyActivation,
    StrategyExecutionBindingRef,
    TechnicalReadiness,
)
from bybit_workbench.universal_entry.engine import (
    AccountAdmissionContext,
    UniversalEntryEngine,
)
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90Crossing,
    P90L90EntryState,
)
from bybit_workbench.universal_entry.p90l90_crossing_fact import p90l90_crossing_fact
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import real_v3_cards
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
from bybit_workbench.universal_entry.registry import ActivePlanRegistry
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

AT = datetime(2026, 10, 28, 18, 0, tzinfo=UTC)
P = Decimal
EMBARGO = StrategyHardStopEmbargoPolicy(
    enabled=True, hardstop_count=2, rolling_hours=48,
    new_entry_pause_hours=48, close_existing_positions=False,
    domain="STRATEGY_VERSION",
)


def _engine(*, real: bool) -> tuple[UniversalEntryEngine, str, str]:
    cards = (
        real_v3_cards(
            approved_at=AT, policy_by_strategy_id={
                MAIN_ID: EMBARGO, "p90l90_legacy_sub_dot_arb_apt": EMBARGO,
            },
        )
        if real else new_version_cards(
            approved_at=AT, policy_by_strategy_id={
                MAIN_ID: EMBARGO, "p90l90_legacy_sub_dot_arb_apt": EMBARGO,
            },
        )
    )
    card = cards[0]
    activation = StrategyActivation(
        activation_id="activation-native-p90",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True, enabled_at=AT,
        operator="CI", source="native-test",
    )
    registry = ActivePlanRegistry()
    registry.register_card(card)
    entry = registry.activate(activation)
    return UniversalEntryEngine(registry), activation.activation_id, entry.entry_plan_fingerprint


def _native_fact(plan_fingerprint: str, *, side: str = "LONG") -> tuple[
    MarketFactEnvelope, TradeCursor,
]:
    event_at = AT + timedelta(seconds=1)
    cursor = TradeCursor(
        symbol="LTCUSDT", exec_id="bybit-native-1", seq=7, traded_at=event_at,
    )
    current = P("89") if side == "LONG" else P("111")
    previous = P("91") if side == "LONG" else P("109")
    boundary = P("90") if side == "LONG" else P("110")
    source_fact = MarketFactEnvelope(
        fact_id="trade-bybit-native-1", event_kind="PUBLIC_TRADE",
        symbol="LTCUSDT", observed_at=event_at, event_at=event_at,
        received_at=event_at + timedelta(milliseconds=20),
        source_refs=("bybit:publicTrade:LTCUSDT:bybit-native-1",),
        attributes=FrozenPolicy.from_mapping({"price": current}),
    )
    state = P90L90EntryState(
        effective_at=AT, l53_low_raw=P("90"), l53_high_raw=P("110"),
        h9_5m_low_raw=P("85"), h9_5m_high_raw=P("115"),
        h9_15m_low_raw=P("85"), h9_15m_high_raw=P("115"),
        raw_h9_union_span=P("30"), working_width_pct=P("3"),
        previous_seven_day_median=P("3"),
        long_depth_pct=P("5"), short_depth_pct=P("5"),
    )
    crossing = P90L90Crossing(
        side=side, event_at=event_at, effective_at=AT,
        current_price=current, previous_price=previous, boundary=boundary,
        depth_pct=P("5"),
    )
    return p90l90_crossing_fact(
        entry_plan_fingerprint=plan_fingerprint,
        source_fact=source_fact, crossing=crossing, geometry=state,
    ), cursor


def _admissions(activation: str) -> dict[str, tuple[AccountAdmissionContext, ...]]:
    ref = StrategyExecutionBindingRef(
        "binding-p90-main", activation, "PRIMARY", "ta-main",
    )
    return {activation: (AccountAdmissionContext(
        binding=ref, capacity=None,
        technical_readiness=TechnicalReadiness(False, AT, "no live arm"),
        account_ref="BYBIT:UNIFIED",
        exchange_position_key="venue-bybit:ta-main:LTCUSDT:0",
        entry_admission_port=None,
    ),)}


@pytest.mark.parametrize("side", ("LONG", "SHORT"))
def test_native_real_fact_requires_separate_account_and_release_proof(side: str) -> None:
    engine, activation, plan = _engine(real=True)
    fact, cursor = _native_fact(plan, side=side)
    with pytest.raises(RuntimeError, match="REAL_NATIVE_ADMISSION_NOT_VERIFIED"):
        engine.process(
            fact, proven_p90_native_cursor=cursor,
            real_admission_required_for=frozenset({activation}),
        )
    with pytest.raises(RuntimeError, match="REAL_NATIVE_ADMISSION_NOT_VERIFIED"):
        engine.process(
            fact, proven_p90_native_cursor=cursor,
            real_admission_required_for=frozenset({activation}),
            account_admissions=_admissions(activation),
        )
    # A complete externally attested account scope permits native candidate
    # validation, not an order: local technical readiness remains false.
    engine._verify_p90_precomputed_fact(
        fact, cursor,
        real_admission_required_for=frozenset({activation}),
        real_admission_ready_for=frozenset({activation}),
        entry_admission_port=None,
        account_admissions=_admissions(activation),
    )


@pytest.mark.parametrize("side", ("LONG", "SHORT"))
def test_native_real_rejects_false_favorable_target_and_wrong_source(side: str) -> None:
    engine, activation, plan = _engine(real=True)
    fact, cursor = _native_fact(plan, side=side)
    attrs = fact.attributes.to_dict()
    nested = dict(attrs["p90l90"])
    nested["opposite_raw_l53_target"] = "80" if side == "LONG" else "120"
    attrs["p90l90"] = nested
    tampered = replace(fact, attributes=FrozenPolicy.from_mapping(attrs))
    args = {
        "real_admission_required_for": frozenset({activation}),
        "real_admission_ready_for": frozenset({activation}),
        "entry_admission_port": None,
        "account_admissions": _admissions(activation),
    }
    with pytest.raises(RuntimeError, match="REAL_NATIVE_TP_FACT_INVALID"):
        engine._verify_p90_precomputed_fact(tampered, cursor, **args)
    with pytest.raises(RuntimeError, match="NATIVE_PROVENANCE_INVALID"):
        engine._verify_p90_precomputed_fact(
            replace(fact, source_refs=("invented:publicTrade",)), cursor, **args,
        )


def test_real_card_cannot_ride_paper_native_path() -> None:
    engine, activation, plan = _engine(real=True)
    fact, cursor = _native_fact(plan)
    with pytest.raises(RuntimeError, match="PRECOMPUTED_REAL_ADMISSION_FORBIDDEN"):
        engine.process(fact, proven_p90_native_cursor=cursor)
    with pytest.raises(RuntimeError, match="REAL_NATIVE_ADMISSION_NOT_VERIFIED"):
        engine.process(
            fact, proven_p90_native_cursor=cursor,
            real_admission_required_for=frozenset({activation}),
            real_admission_ready_for=frozenset({activation}),
            account_admissions={activation: ()},
        )


def test_historical_paper_v2_never_upgrades_to_real_entry() -> None:
    engine, activation, plan = _engine(real=False)
    fact, cursor = _native_fact(plan)
    engine._verify_p90_precomputed_fact(
        fact, cursor, real_admission_required_for=frozenset(),
        real_admission_ready_for=None, entry_admission_port=None,
        account_admissions=None,
    )
    with pytest.raises(RuntimeError, match="PRECOMPUTED_REAL_ADMISSION_FORBIDDEN"):
        engine.process(
            fact, proven_p90_native_cursor=cursor,
            real_admission_required_for=frozenset({activation}),
            real_admission_ready_for=frozenset({activation}),
            account_admissions=_admissions(activation),
        )
