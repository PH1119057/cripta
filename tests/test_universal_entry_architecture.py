from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bybit_workbench.universal_entry import (
    ActivePlanRegistry,
    CandidateCooldown,
    ContextFailureAction,
    ContextMode,
    ContextRequirement,
    CooldownScope,
    DataQuality,
    EntryDecisionCode,
    FrozenPolicy,
    MarketFactEnvelope,
    NumericRule,
    ObjectiveContext,
    SensorObservation,
    SensorRequirement,
    StrategyActivation,
    StrategyCard,
    TechnicalReadiness,
    TouchPolicy,
    TradeDirection,
    TradingCapacitySnapshot,
    UniversalEntryEngine,
)
from bybit_workbench.universal_entry.catalog import CatalogEntry, ContextClass, SensorContextCatalog
from bybit_workbench.universal_entry.dsl import UnsupportedOperatorError, predicate_from_mapping

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
READY = TechnicalReadiness(True, NOW, "ready")


def policy(payload: dict[str, object] | None = None) -> FrozenPolicy:
    return FrozenPolicy.from_mapping(payload or {})


def make_card(
    name: str,
    *,
    direction: TradeDirection = TradeDirection.LONG,
    symbols: tuple[str, ...] = ("XRPUSDT",),
    touch_policy: TouchPolicy | None = None,
    predicate: dict[str, object] | None = None,
    sensors: tuple[SensorRequirement, ...] = (),
    contexts: tuple[ContextRequirement, ...] = (),
    capital: dict[str, object] | None = None,
) -> StrategyCard:
    entry: dict[str, object] = {
        "entry_plan_version": "1",
        "predicate": predicate or {"op": "TOUCH"},
    }
    return StrategyCard.build(
        strategy_id=name,
        strategy_version="1.0.0",
        name=name,
        description="test strategy",
        scope=policy({"kind": "symbols"}),
        symbols=symbols,
        direction_policy=(direction,),
        entry_policy=policy(entry),
        exit_policy=policy({"exit_plan_version": "1"}),
        capital_policy=policy(capital),
        protection_policy=policy({}),
        lifecycle_policy=policy({}),
        touch_policy=touch_policy or TouchPolicy(accepted_touch_numbers=(1,), accept_touch_from=1),
        market_sensor_policy=sensors,
        dispatcher_context_policy=contexts,
        approved_at=NOW,
    )


def activation(card: StrategyCard, suffix: str = "a", enabled: bool = True) -> StrategyActivation:
    return StrategyActivation(
        activation_id=f"act-{suffix}",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=enabled,
        enabled_at=NOW,
    )


def fact(
    index: int,
    *,
    symbol: str = "XRPUSDT",
    kind: str = "TOUCH",
    seconds: int = 0,
    attributes: dict[str, object] | None = None,
) -> MarketFactEnvelope:
    at = NOW + timedelta(seconds=seconds)
    return MarketFactEnvelope(
        fact_id=f"fact-{index}",
        event_kind=kind,
        symbol=symbol,
        observed_at=at,
        event_at=at,
        received_at=at,
        source_refs=(f"trade:{index}",),
        attributes=policy(attributes),
    )


def setup_engine(*cards: StrategyCard) -> tuple[ActivePlanRegistry, UniversalEntryEngine]:
    registry = ActivePlanRegistry()
    for index, card in enumerate(cards):
        registry.register_card(card)
        registry.activate(activation(card, str(index)))
    return registry, UniversalEntryEngine(registry)


def evaluate(
    engine: UniversalEntryEngine,
    event: MarketFactEnvelope,
    **kwargs: Any,
):
    kwargs.setdefault("technical_readiness", READY)
    return engine.process(event, **kwargs)


def capacity_policy(
    amount: str,
    *,
    max_age_seconds: int = 30,
    min_quality: DataQuality = DataQuality.MEDIUM,
) -> dict[str, object]:
    return {
        "require_capacity": True,
        "requested_amount": amount,
        "capacity_max_age_seconds": max_age_seconds,
        "capacity_min_quality": min_quality.value,
    }


def decision_requirement(context_id: str, mode: ContextMode) -> ContextRequirement:
    return ContextRequirement(
        context_id,
        mode,
        max_age_seconds=30,
        min_quality=DataQuality.MEDIUM,
        on_missing=ContextFailureAction.REJECT_SIGNAL,
        on_stale=ContextFailureAction.REJECT_SIGNAL,
        on_partial=ContextFailureAction.REJECT_SIGNAL,
    )


def sensor_requirement(sensor_id: str, mode: ContextMode) -> SensorRequirement:
    return SensorRequirement(
        sensor_id,
        mode,
        max_age_seconds=30,
        min_quality=DataQuality.MEDIUM,
        on_missing=ContextFailureAction.REJECT_SIGNAL,
        on_stale=ContextFailureAction.REJECT_SIGNAL,
        on_partial=ContextFailureAction.REJECT_SIGNAL,
    )


def test_one_strategy_creates_exact_signal_attempt_decision_and_request() -> None:
    card = make_card("s1")
    _, engine = setup_engine(card)
    result = evaluate(engine, fact(1))
    assert len(result) == 1
    item = result[0]
    assert item.signal.strategy_id == card.strategy_id
    assert item.signal.strategy_config_fingerprint == card.strategy_config_fingerprint
    assert item.attempt.signal_id == item.signal.signal_id
    assert item.decision.strategy_attempt_id == item.attempt.strategy_attempt_id
    assert item.decision.code is EntryDecisionCode.ACCEPTED
    assert item.execution_request is not None
    assert item.execution_request.entry_plan_fingerprint == item.signal.entry_plan_fingerprint


def test_five_simultaneous_strategies_are_independent() -> None:
    cards = tuple(make_card(f"s{index}") for index in range(5))
    _, engine = setup_engine(*cards)
    result = evaluate(engine, fact(1))
    assert len(result) == 5
    assert {item.signal.strategy_id for item in result} == {card.strategy_id for card in cards}
    assert len({item.signal.signal_id for item in result}) == 5


def test_opposite_long_short_strategies_on_same_symbol_both_signal() -> None:
    long = make_card("long", direction=TradeDirection.LONG)
    short = make_card("short", direction=TradeDirection.SHORT)
    _, engine = setup_engine(long, short)
    result = evaluate(engine, fact(1))
    assert {item.signal.direction for item in result} == {
        TradeDirection.LONG,
        TradeDirection.SHORT,
    }


def test_registry_has_no_winner_or_priority_api() -> None:
    registry = ActivePlanRegistry()
    forbidden = {"winner", "priority", "select_strategy", "other_strategy_won"}
    assert forbidden.isdisjoint(set(dir(registry)))


def test_disable_activation_removes_only_that_plan_and_card_is_immutable() -> None:
    first = make_card("first")
    second = make_card("second")
    registry = ActivePlanRegistry()
    for index, card in enumerate((first, second)):
        registry.register_card(card)
        registry.activate(activation(card, str(index)))
    original_fingerprint = first.strategy_config_fingerprint
    registry.set_enabled("act-0", False, changed_at=NOW + timedelta(seconds=1))
    plans = registry.active_entry_plans()
    assert len(plans) == 1 and plans[0].strategy_id == "second"
    assert first.strategy_config_fingerprint == original_fingerprint
    with pytest.raises(FrozenInstanceError):
        first.name = "changed"  # type: ignore[misc]


def test_reenable_activation_updates_control_timestamp_not_card() -> None:
    card = make_card("reenable")
    registry = ActivePlanRegistry()
    registry.register_card(card)
    registry.activate(activation(card, "x"))
    disabled = registry.set_enabled("act-x", False, changed_at=NOW + timedelta(seconds=1))
    enabled = registry.set_enabled("act-x", True, changed_at=NOW + timedelta(seconds=2))
    assert disabled.disabled_at == NOW + timedelta(seconds=1)
    assert enabled.enabled_at == NOW + timedelta(seconds=2)
    assert enabled.disabled_at is None
    assert card.strategy_config_fingerprint == enabled.strategy_config_fingerprint


def test_touch_policy_has_no_hidden_first_touch_default() -> None:
    touch = TouchPolicy()
    assert not touch.accepts(1)
    assert not touch.accepts(2)


def test_disabled_cooldown_has_no_hidden_timer() -> None:
    touch = TouchPolicy(
        accepted_touch_numbers=(1, 2),
        candidate_cooldown=CandidateCooldown(enabled=False),
    )
    card = make_card("no-cooldown", touch_policy=touch)
    _, engine = setup_engine(card)
    assert len(evaluate(engine, fact(1))) == 1
    assert len(evaluate(engine, fact(2, seconds=1))) == 1


def test_enabled_per_symbol_cooldown_blocks_only_same_symbol() -> None:
    touch = TouchPolicy(
        accepted_touch_numbers=(1, 2),
        candidate_cooldown=CandidateCooldown(
            True,
            Decimal("1"),
            "minutes",
            CooldownScope.PER_SYMBOL,
            "SIGNAL",
        ),
    )
    card = make_card("cooldown", symbols=("XRPUSDT", "SOLUSDT"), touch_policy=touch)
    _, engine = setup_engine(card)
    assert len(evaluate(engine, fact(1))) == 1
    assert evaluate(engine, fact(2, seconds=1)) == ()
    assert len(evaluate(engine, fact(3, symbol="SOLUSDT", seconds=1))) == 1


def test_per_account_cooldown_is_explicit_and_does_not_select_strategy() -> None:
    touch = TouchPolicy(
        accepted_touch_numbers=(1,),
        accept_touch_from=1,
        candidate_cooldown=CandidateCooldown(
            True,
            Decimal("1"),
            "minutes",
            CooldownScope.PER_ACCOUNT,
            "SIGNAL",
        ),
    )
    card = make_card("account-cooldown", symbols=("XRPUSDT", "SOLUSDT"), touch_policy=touch)
    _, engine = setup_engine(card)
    assert len(evaluate(engine, fact(1), account_ref="account-A")) == 1
    assert evaluate(engine, fact(2, symbol="SOLUSDT", seconds=1), account_ref="account-A") == ()
    assert len(evaluate(engine, fact(3, symbol="SOLUSDT", seconds=1), account_ref="account-B")) == 1


def test_per_account_cooldown_without_account_ref_fails_explicitly() -> None:
    touch = TouchPolicy(
        accepted_touch_numbers=(1,),
        candidate_cooldown=CandidateCooldown(
            True,
            Decimal("1"),
            "minutes",
            CooldownScope.PER_ACCOUNT,
            "SIGNAL",
        ),
    )
    card = make_card("account-required", touch_policy=touch)
    _, engine = setup_engine(card)
    with pytest.raises(ValueError, match="account_ref"):
        evaluate(engine, fact(1))


def test_touch_triggered_cooldown_can_start_before_full_strategy_match() -> None:
    touch = TouchPolicy(
        accepted_touch_numbers=(1,),
        accept_touch_from=1,
        candidate_cooldown=CandidateCooldown(
            True,
            Decimal("1"),
            "minutes",
            CooldownScope.PER_SYMBOL,
            "TOUCH",
        ),
    )
    predicate = {
        "op": "AND",
        "children": [
            {"op": "TOUCH"},
            {
                "op": "COMPARE",
                "params": {"path": "fact.allow", "comparator": "EQ", "value": True},
            },
        ],
    }
    card = make_card("touch-trigger", touch_policy=touch, predicate=predicate)
    _, engine = setup_engine(card)
    assert evaluate(engine, fact(1, attributes={"allow": False})) == ()
    assert evaluate(engine, fact(2, seconds=1, attributes={"allow": True})) == ()


def test_second_touch_is_generic_and_reset_restarts_counter() -> None:
    touch = TouchPolicy(accepted_touch_numbers=(2,), reset_on=("RESET",))
    card = make_card("second-touch", touch_policy=touch)
    _, engine = setup_engine(card)
    assert evaluate(engine, fact(1)) == ()
    assert len(evaluate(engine, fact(2, seconds=1))) == 1
    evaluate(engine, fact(3, kind="RESET", seconds=2))
    assert evaluate(engine, fact(4, seconds=3)) == ()
    assert len(evaluate(engine, fact(5, seconds=4))) == 1


def test_nth_and_fourth_plus_touch_need_no_engine_code_change() -> None:
    touch = TouchPolicy(accepted_touch_numbers=(3,), accept_touch_from=4)
    card = make_card("nth-touch", touch_policy=touch)
    _, engine = setup_engine(card)
    assert evaluate(engine, fact(1)) == ()
    assert evaluate(engine, fact(2, seconds=1)) == ()
    assert len(evaluate(engine, fact(3, seconds=2))) == 1
    assert len(evaluate(engine, fact(4, seconds=3))) == 1


def test_minimum_time_reject_does_not_increment_independent_touch_counter() -> None:
    touch = TouchPolicy(
        accepted_touch_numbers=(1, 2),
        minimum_time_between_touches=NumericRule(True, Decimal("10"), "seconds"),
    )
    card = make_card("touch-time", touch_policy=touch)
    _, engine = setup_engine(card)
    assert len(evaluate(engine, fact(1))) == 1
    assert evaluate(engine, fact(2, seconds=5)) == ()
    assert len(evaluate(engine, fact(3, seconds=10))) == 1


def test_touch_exit_distance_requires_matching_explicit_unit() -> None:
    touch = TouchPolicy(
        accepted_touch_numbers=(1, 2),
        minimum_exit_distance=NumericRule(True, Decimal("2"), "percent"),
    )
    card = make_card("touch-distance", touch_policy=touch)
    _, engine = setup_engine(card)
    assert len(evaluate(engine, fact(1))) == 1
    assert (
        evaluate(
            engine,
            fact(2, seconds=1, attributes={"exit_distance": "3", "exit_distance_unit": "atr"}),
        )
        == ()
    )
    assert (
        len(
            evaluate(
                engine,
                fact(
                    3,
                    seconds=2,
                    attributes={"exit_distance": "3", "exit_distance_unit": "percent"},
                ),
            )
        )
        == 1
    )


def test_long_sequence_is_not_truncated_by_hidden_event_count_constant() -> None:
    expected = ["STEP"] * 130 + ["TOUCH"]
    card = make_card(
        "long-sequence",
        predicate={"op": "SEQUENCE", "params": {"events": expected}},
    )
    _, engine = setup_engine(card)
    for index in range(130):
        assert evaluate(engine, fact(index, kind="STEP", seconds=index)) == ()
    assert len(evaluate(engine, fact(131, seconds=130))) == 1


def test_off_context_has_no_effect() -> None:
    requirement = ContextRequirement("dispatcher.coin", ContextMode.OFF)
    card = make_card("off", contexts=(requirement,))
    _, engine = setup_engine(card)
    result = evaluate(engine, fact(1))
    assert len(result) == 1
    assert result[0].context_links == ()


def test_observe_context_is_linked_but_not_consumed() -> None:
    requirement = ContextRequirement("dispatcher.coin", ContextMode.OBSERVE)
    card = make_card("observe", contexts=(requirement,))
    _, engine = setup_engine(card)
    context = ObjectiveContext(
        "dispatcher.coin",
        "DISPATCHER_COIN_CONTEXT",
        NOW,
        DataQuality.HIGH,
        "COMPLETE",
        policy({"score": 9}),
    )
    result = evaluate(engine, fact(1), contexts={context.context_id: context})
    assert len(result) == 1
    link = result[0].context_links[0]
    assert link.observed and not link.consumed and link.mode is ContextMode.OBSERVE


def test_condition_context_can_change_only_strategy_that_declares_it() -> None:
    requirement = decision_requirement("dispatcher.coin", ContextMode.CONDITION)
    predicate = {
        "op": "AND",
        "children": [
            {"op": "TOUCH"},
            {
                "op": "COMPARE",
                "params": {
                    "path": "context.dispatcher.coin.score",
                    "comparator": "GT",
                    "value": 5,
                },
            },
        ],
    }
    gated = make_card("gated", predicate=predicate, contexts=(requirement,))
    plain = make_card("plain")
    _, engine = setup_engine(gated, plain)
    low = ObjectiveContext(
        "dispatcher.coin",
        "DISPATCHER_COIN_CONTEXT",
        NOW,
        DataQuality.HIGH,
        "COMPLETE",
        policy({"score": 1}),
    )
    result = evaluate(engine, fact(1), contexts={low.context_id: low})
    assert [item.signal.strategy_id for item in result] == ["plain"]


def test_strategy_local_ranking_context_does_not_rank_strategies() -> None:
    requirement = decision_requirement("dispatcher.coin", ContextMode.RANKING)
    ranked = make_card(
        "ranked-local",
        contexts=(requirement,),
        predicate={
            "op": "AND",
            "children": [
                {"op": "TOUCH"},
                {
                    "op": "COMPARE",
                    "params": {
                        "path": "context.dispatcher.coin.score",
                        "comparator": "GTE",
                        "value": 5,
                    },
                },
            ],
        },
    )
    plain = make_card("plain-local")
    _, engine = setup_engine(ranked, plain)
    context = ObjectiveContext(
        "dispatcher.coin",
        "DISPATCHER_COIN_CONTEXT",
        NOW,
        DataQuality.HIGH,
        "COMPLETE",
        policy({"score": 9}),
    )
    result = evaluate(engine, fact(1), contexts={context.context_id: context})
    assert {item.signal.strategy_id for item in result} == {"ranked-local", "plain-local"}


def test_missing_stale_and_partial_context_never_become_neutral_zero() -> None:
    requirement = decision_requirement("dispatcher.coin", ContextMode.CONDITION)
    card = make_card("required-context", contexts=(requirement,))
    _, engine = setup_engine(card)
    assert evaluate(engine, fact(1)) == ()
    stale = ObjectiveContext(
        "dispatcher.coin",
        "DISPATCHER_COIN_CONTEXT",
        NOW - timedelta(seconds=40),
        DataQuality.HIGH,
        "COMPLETE",
        policy({}),
    )
    assert evaluate(engine, fact(2, seconds=1), contexts={stale.context_id: stale}) == ()
    low = ObjectiveContext(
        "dispatcher.coin",
        "DISPATCHER_COIN_CONTEXT",
        NOW,
        DataQuality.LOW,
        "PARTIAL",
        policy({}),
    )
    assert evaluate(engine, fact(3, seconds=2), contexts={low.context_id: low}) == ()


def test_sensor_condition_uses_exact_observation_and_records_consumption() -> None:
    requirement = sensor_requirement("flow.delta", ContextMode.CONDITION)
    card = make_card(
        "sensor-card",
        sensors=(requirement,),
        predicate={
            "op": "AND",
            "children": [
                {"op": "TOUCH"},
                {
                    "op": "COMPARE",
                    "params": {
                        "path": "sensor.flow.delta.value",
                        "comparator": "GT",
                        "value": 0,
                    },
                },
            ],
        },
    )
    _, engine = setup_engine(card)
    sensor = SensorObservation(
        "flow.delta",
        NOW,
        DataQuality.HIGH,
        "COMPLETE",
        policy({"value": "1"}),
        ("trade-window:1",),
    )
    result = evaluate(engine, fact(1), sensors={sensor.sensor_id: sensor})
    assert len(result) == 1
    link = result[0].sensor_links[0]
    assert link.consumed and link.source_refs == ("trade-window:1",)


def test_stale_sensor_never_becomes_zero_or_neutral() -> None:
    requirement = sensor_requirement("flow.delta", ContextMode.CONDITION)
    card = make_card("stale-sensor", sensors=(requirement,))
    _, engine = setup_engine(card)
    sensor = SensorObservation(
        "flow.delta",
        NOW - timedelta(seconds=40),
        DataQuality.HIGH,
        "COMPLETE",
        policy({"value": "99"}),
    )
    assert evaluate(engine, fact(1), sensors={sensor.sensor_id: sensor}) == ()


def test_capacity_insufficient_is_attempt_reason_not_strategy_selection() -> None:
    card = make_card("money", capital=capacity_policy("50"))
    _, engine = setup_engine(card)
    capacity = TradingCapacitySnapshot(
        "cap-1",
        NOW,
        Decimal("20"),
        DataQuality.HIGH,
        "exchange:test",
    )
    result = evaluate(engine, fact(1), capacity=capacity)
    assert result[0].decision.code is EntryDecisionCode.INSUFFICIENT_AVAILABLE_FUNDS
    assert result[0].execution_request is None
    assert result[0].notifications[0].available_amount == Decimal("20")


def test_capacity_quality_threshold_is_plan_data_not_engine_default() -> None:
    low_ok = make_card(
        "low-ok",
        capital=capacity_policy("10", min_quality=DataQuality.LOW),
    )
    medium_required = make_card(
        "medium-required",
        capital=capacity_policy("10", min_quality=DataQuality.MEDIUM),
    )
    _, engine = setup_engine(low_ok, medium_required)
    capacity = TradingCapacitySnapshot(
        "cap-low",
        NOW,
        Decimal("100"),
        DataQuality.LOW,
        "exchange:test",
    )
    result = evaluate(engine, fact(1), capacity=capacity)
    by_strategy = {item.signal.strategy_id: item for item in result}
    assert by_strategy["low-ok"].decision.code is EntryDecisionCode.ACCEPTED
    assert (
        by_strategy["medium-required"].decision.code
        is EntryDecisionCode.STALE_OR_UNKNOWN_REQUIRED_STATE
    )


def test_capacity_freshness_threshold_is_plan_data() -> None:
    strict = make_card("strict-age", capital=capacity_policy("10", max_age_seconds=5))
    relaxed = make_card("relaxed-age", capital=capacity_policy("10", max_age_seconds=30))
    _, engine = setup_engine(strict, relaxed)
    capacity = TradingCapacitySnapshot(
        "cap-age",
        NOW - timedelta(seconds=10),
        Decimal("100"),
        DataQuality.HIGH,
        "exchange:test",
    )
    result = evaluate(engine, fact(1), capacity=capacity)
    by_strategy = {item.signal.strategy_id: item for item in result}
    assert (
        by_strategy["strict-age"].decision.code is EntryDecisionCode.STALE_OR_UNKNOWN_REQUIRED_STATE
    )
    assert by_strategy["relaxed-age"].decision.code is EntryDecisionCode.ACCEPTED


def test_capacity_policy_requires_explicit_freshness_and_quality() -> None:
    card = make_card("bad-capital", capital={"require_capacity": True, "requested_amount": "1"})
    registry = ActivePlanRegistry()
    registry.register_card(card)
    with pytest.raises(ValueError, match="capacity policy missing required fields"):
        registry.activate(activation(card))


def test_operational_safety_block_is_separate_from_strategy_condition() -> None:
    card = make_card("safety")
    _, engine = setup_engine(card)
    readiness = TechnicalReadiness(False, NOW, "private account state stale")
    result = engine.process(fact(1), technical_readiness=readiness)
    assert result[0].decision.code is EntryDecisionCode.OPERATIONAL_SAFETY_BLOCKED
    assert result[0].execution_request is None


def test_missing_mandatory_technical_readiness_is_unknown_not_accepted() -> None:
    card = make_card("missing-readiness")
    _, engine = setup_engine(card)
    result = engine.process(fact(1))
    assert result[0].decision.code is EntryDecisionCode.STALE_OR_UNKNOWN_REQUIRED_STATE
    assert result[0].execution_request is None


def test_unknown_capacity_is_not_zero() -> None:
    card = make_card("unknown-cap", capital=capacity_policy("1"))
    _, engine = setup_engine(card)
    result = evaluate(engine, fact(1), capacity=None)
    assert result[0].decision.code is EntryDecisionCode.STALE_OR_UNKNOWN_REQUIRED_STATE


def test_compare_operator_has_no_hidden_comparator_default() -> None:
    with pytest.raises(ValueError, match="requires explicit parameters"):
        predicate_from_mapping({"op": "COMPARE", "params": {"path": "fact.allow", "value": True}})


def test_unsupported_operator_fails_explicitly() -> None:
    with pytest.raises(UnsupportedOperatorError, match="UNSUPPORTED_OPERATOR"):
        predicate_from_mapping({"op": "MAGIC_WINNER"})


def test_strategy_fingerprint_is_stable_and_changes_with_policy() -> None:
    first = make_card("stable")
    second = make_card("stable")
    changed = make_card("stable", capital={"requested_amount": "2"})
    assert first.strategy_config_fingerprint == second.strategy_config_fingerprint
    assert first.strategy_config_fingerprint != changed.strategy_config_fingerprint


def test_sensor_context_catalog_has_stable_ids_and_rejects_collision() -> None:
    catalog = SensorContextCatalog(
        (CatalogEntry("dispatcher.coin", ContextClass.DISPATCHER_COIN_CONTEXT, "coin"),)
    )
    assert catalog.require("dispatcher.coin").context_class == ContextClass.DISPATCHER_COIN_CONTEXT
    catalog.register(CatalogEntry("dispatcher.coin", ContextClass.DISPATCHER_COIN_CONTEXT, "coin"))
    with pytest.raises(ValueError, match="already registered"):
        catalog.register(CatalogEntry("dispatcher.coin", ContextClass.MAYAK_CONTEXT, "different"))
    with pytest.raises(KeyError, match="unknown sensor/context id"):
        catalog.require("missing")


def test_universal_entry_source_has_no_strategy_selector_or_exchange_mutation() -> None:
    package = ROOT / "src/bybit_workbench/universal_entry"
    body = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    forbidden = (
        "OTHER_STRATEGY_WON",
        "select_strategy(",
        "strategy_priority",
        "place_order",
        "cancel_order",
        "amend_order",
        "runtime.trade_commands",
        "entry_v1_core",
        "Entry V2",
        "pressure_then_reversal",
        "OI tail",
        "0.25%",
        "0.5 ATR",
        "lookback = 130",
    )
    for token in forbidden:
        assert token not in body


def test_universal_engine_has_no_strategy_id_specific_branch() -> None:
    body = (ROOT / "src/bybit_workbench/universal_entry/engine.py").read_text(encoding="utf-8")
    assert "if strategy_id" not in body
    assert "if plan.strategy_id" not in body
    assert "entry_v1_core" not in body


def test_dispatcher_v2_does_not_import_activation_plan_or_signal() -> None:
    paths = [
        ROOT / "production/src/bybit_workbench/dispatcher_v2",
        ROOT / "operations/monitoring/dispatcher_v2.py",
    ]
    body_parts: list[str] = []
    for path in paths:
        if path.is_dir():
            body_parts.extend(item.read_text(encoding="utf-8") for item in path.glob("*.py"))
        elif path.exists():
            body_parts.append(path.read_text(encoding="utf-8"))
    body = "\n".join(body_parts)
    assert "StrategyActivation" not in body
    assert "EntryPlan" not in body
    assert "StrategySignal" not in body


def test_decision_context_requires_explicit_missing_stale_partial_semantics() -> None:
    with pytest.raises(ValueError, match="missing/stale/partial"):
        ContextRequirement("x", ContextMode.CONDITION)
    with pytest.raises(ValueError, match="missing/stale/partial"):
        ContextRequirement("x", ContextMode.RANKING)


def test_decision_sensor_requires_explicit_missing_stale_partial_semantics() -> None:
    with pytest.raises(ValueError, match="missing/stale/partial"):
        SensorRequirement("x", ContextMode.CONDITION)
