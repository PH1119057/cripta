"""P90L90 native real Entry acceptance remains proof/account scoped.

No PostgreSQL, Exchange or StrategyActivation mutation in this test module.
Historical PAPER v2 and owner-approved REAL v3 keep distinct paths.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.contracts import (
    DataQuality,
    EntryDecisionCode,
    FrozenPolicy,
    MarketFactEnvelope,
    StrategyActivation,
    StrategyExecutionBindingRef,
    TechnicalReadiness,
    TradingCapacitySnapshot,
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
from bybit_workbench.universal_entry.p90l90_real_native_candidate_owner import (
    P90L90RealNativeCandidateOwner,
)
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
    evaluations = engine.process(
        fact, proven_p90_native_cursor=cursor,
        real_admission_required_for=frozenset({activation}),
        real_admission_ready_for=frozenset({activation}),
        account_admissions=_admissions(activation),
    )
    assert len(evaluations) == 1
    evaluation = evaluations[0]
    assert evaluation.decision is None
    assert evaluation.execution_request is None
    assert len(evaluation.account_outcomes) == 1
    outcome = evaluation.account_outcomes[0]
    assert outcome.decision.code is EntryDecisionCode.OPERATIONAL_SAFETY_BLOCKED
    assert outcome.execution_request is None


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
    with pytest.raises(RuntimeError, match="REAL_NATIVE_CURSOR_REQUIRED"):
        engine.process(fact)
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


class _MemoryAdmissionPort:
    """In-memory receipt for decision math only; never an Exchange adapter."""

    def __init__(self) -> None:
        self.requests: list[object] = []

    def admit(self, request: object) -> object:
        self.requests.append(request)
        return SimpleNamespace(
            capital_reservation=SimpleNamespace(reservation_id="cap-main-10"),
            exchange_position_slot_claim_id="slot-main-ltc",
            position_mode_state_ref="one-way-main",
        )


@pytest.mark.parametrize("side,expected_tp", (("LONG", "110"), ("SHORT", "90")))
def test_native_real_v3_routes_approved_account_to_universal_execution_request(
    side: str, expected_tp: str,
) -> None:
    engine, activation, plan = _engine(real=True)
    fact, cursor = _native_fact(plan, side=side)
    port = _MemoryAdmissionPort()
    original = _admissions(activation)[activation][0]
    ready = replace(
        original,
        technical_readiness=TechnicalReadiness(True, AT, "external exact arm proof"),
        capacity=TradingCapacitySnapshot(
            capacity_snapshot_id="capacity-main",
            observed_at=AT,
            available_for_new_trading=P("100"),
            quality=DataQuality.HIGH,
            source_ref="runtime.account_state_generations:verified-main",
        ),
        entry_admission_port=port,  # type: ignore[arg-type]
    )
    results = engine.process(
        fact, proven_p90_native_cursor=cursor,
        real_admission_required_for=frozenset({activation}),
        real_admission_ready_for=frozenset({activation}),
        account_admissions={activation: (ready,)},
        admission_time=AT + timedelta(seconds=2),
    )
    assert len(results) == 1
    assert len(results[0].account_outcomes) == 1
    outcome = results[0].account_outcomes[0]
    assert outcome.decision.code is EntryDecisionCode.ACCEPTED
    assert outcome.execution_request is not None
    assert outcome.execution_request.trading_account_id == "ta-main"
    assert outcome.execution_request.capital_reservation_id == "cap-main-10"
    assert len(port.requests) == 1
    request_fact = outcome.execution_request.payload.to_dict()["signal_fact"]
    assert request_fact["attributes"]["p90l90"]["opposite_raw_l53_target"] == expected_tp
    assert request_fact["attributes"]["modeled_exchange_fill"] is False


class _EvaluationRecorder:
    def __init__(self) -> None:
        self.recorded: list[object] = []

    def record_evaluation(self, evaluation: object) -> None:
        self.recorded.append(evaluation)


@pytest.mark.parametrize("side", ("LONG", "SHORT"))
def test_real_native_atomic_callback_keeps_exact_one_account_and_no_paper(
    side: str,
) -> None:
    engine, activation, entry_fp = _engine(real=True)
    plan = engine._registry.plans_for("LTCUSDT")[0]
    fact, cursor = _native_fact(entry_fp, side=side)
    recorder = _EvaluationRecorder()
    owner = P90L90RealNativeCandidateOwner(
        strategy_id=MAIN_ID,
        strategy_activation_id=activation,
        strategy_config_fingerprint=plan.strategy_config_fingerprint,
        entry_plan_fingerprint=entry_fp,
        engine=engine,
        store=recorder,  # type: ignore[arg-type]
    )
    port = _MemoryAdmissionPort()
    raw = _admissions(activation)[activation][0]
    scoped = replace(
        raw,
        technical_readiness=TechnicalReadiness(True, AT, "exact private arm"),
        capacity=TradingCapacitySnapshot(
            capacity_snapshot_id="capacity-main",
            observed_at=AT,
            available_for_new_trading=P("100"),
            quality=DataQuality.HIGH,
            source_ref="runtime.account_state_generations:verified-main",
        ),
        entry_admission_port=port,  # type: ignore[arg-type]
    )
    for forbidden in (
        {"account_admissions": (scoped,), "arm_and_source_verified": False},
        {"account_admissions": (raw,), "arm_and_source_verified": True},
        {"account_admissions": (), "arm_and_source_verified": True},
    ):
        with pytest.raises(RuntimeError, match="P90_REAL_NATIVE_OWNER_"):
            owner.accept(
                fact, cursor, admitted_at=AT + timedelta(seconds=2),
                allow_new_signals=True, **forbidden,
            )
    assert not recorder.recorded
    assert not port.requests
    results = owner.accept(
        fact, cursor, account_admissions=(scoped,),
        arm_and_source_verified=True,
        admitted_at=AT + timedelta(seconds=2),
        allow_new_signals=True,
    )
    assert len(results) == 1
    assert recorder.recorded == list(results)
    assert len(results[0].account_outcomes) == 1
    account_outcome = results[0].account_outcomes[0]
    assert account_outcome.binding.trading_account_id == "ta-main"
    assert account_outcome.execution_request is not None
    assert account_outcome.execution_request.trading_account_id == "ta-main"
    assert len(port.requests) == 1
    assert results[0].decision is None
    assert results[0].execution_request is None
