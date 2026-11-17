"""P90L90 REAL v3 Exit math on the exact immutable approved ExitPlan.

All samples are pure, synthetic proof objects. No live Exchange, DB, or arm.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    StrategyActivation,
    TradeDirection,
)
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    VERSION,
    real_v3_cards,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_exit.contracts import (
    ExitActionKind,
    ExitEvaluationStatus,
    ExitObservation,
)
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.execution_bridge import validate_exit_mutation
from bybit_workbench.universal_exit.p90l90_real_v3 import (
    P90RealExitUnverified,
    project_p90l90_real_exit,
)

AT = datetime(2026, 10, 28, 19, 0, tzinfo=UTC)
D = Decimal
SECOND48 = StrategyHardStopEmbargoPolicy(
    enabled=True, hardstop_count=2, rolling_hours=48,
    new_entry_pause_hours=48, close_existing_positions=False,
    domain="STRATEGY_VERSION",
)


def _scope(*, side: str = "LONG", main: bool = True):
    cards = real_v3_cards(
        approved_at=AT, policy_by_strategy_id={MAIN_ID: SECOND48, SUB_ID: SECOND48},
    )
    card = cards[0] if main else cards[1]
    activation = StrategyActivation(
        activation_id=f"activation-p90-{card.strategy_id}",
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True, enabled_at=AT,
    )
    _, plan = materialize_plans(card, activation)
    symbol = card.symbols[0]
    position = StrategyPosition(
        strategy_position_id="position-real-one", account_ref="BYBIT:UNIFIED",
        exchange_position_key=f"venue-bybit:ta-real:{symbol}:0", position_idx=0,
        strategy_id=card.strategy_id, strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        strategy_activation_id=activation.activation_id, signal_id="signal-p90",
        strategy_attempt_id="attempt-p90", entry_decision_id="decision-entry",
        entry_execution_request_id="request-p90",
        entry_plan_fingerprint="entry-plan-real-p90",
        exit_plan_fingerprint=plan.exit_plan_fingerprint,
        entry_command_id="entry-command-p90", symbol=symbol,
        direction=TradeDirection(side), actual_avg_fill=D("100"),
        actual_qty=D("0.1"), fill_at=AT,
        initial_protection_confirmed_at=AT + timedelta(seconds=1),
        strategy_execution_binding_id="binding-real",
        execution_leg="PRIMARY", trading_account_id="ta-real",
        identity_status="PROVEN",
    )
    return position, plan


def _observation(
    position: StrategyPosition, *,
    best: str, price: str, stop: str, tp: str,
    target: str | None = None, kind: str = "P90L90_REAL_NATIVE_TRADE",
    trailing: str = "0",
) -> ExitObservation:
    fact = {
        "trading_account_id": position.trading_account_id,
        "strategy_execution_binding_id": position.strategy_execution_binding_id,
        "exchange_position_key": position.exchange_position_key,
        "actual_fill_confirmed": True,
        "protection_exchange_confirmed": True,
        "durable_cursor_verified": True,
        "pending_exit_mutation": False,
        "native_trade_price": price,
        "durable_best_favorable_price": best,
        "exchange_stop_price": stop,
        "exchange_tp_price": tp,
        "exchange_trailing_distance": trailing,
        "exchange_mark_price": (
            "100.20" if position.direction is TradeDirection.LONG else "99.80"
        ),
        "tick_size": "0.01",
    }
    if target is not None:
        fact["opposite_raw_l53_target"] = target
        fact["closed_geometry_proven"] = True
    return ExitObservation(
        observation_id="observation-p90-native",
        strategy_position_id=position.strategy_position_id,
        symbol=position.symbol,
        event_at=AT + timedelta(minutes=5),
        observed_at=AT + timedelta(minutes=5),
        received_at=AT + timedelta(minutes=5, milliseconds=10),
        event_kind=kind,
        attributes=FrozenPolicy.from_mapping({"p90l90_real": fact}),
        source_refs=("bybit:publicTrade:exact-native-event",),
    )


@pytest.mark.parametrize(
    ("side", "best", "last", "stop", "tp", "activation"),
    [
        ("LONG", "100.20", "100.20", "90", "110", "100.50"),
        ("SHORT", "99.80", "99.80", "110", "90", "99.50"),
    ],
)
def test_real_exit_arms_autonomous_bybit_trailing_before_activation(
    side: str, best: str, last: str, stop: str, tp: str, activation: str,
) -> None:
    position, plan = _scope(side=side)
    event = _observation(position, best=best, price=last, stop=stop, tp=tp)
    result = project_p90l90_real_exit(position, plan, event)
    assert result is not None
    kind, mutation, label = result
    assert kind is ExitActionKind.SET_TRAILING
    assert mutation == {
        "distance": "0.35",
        "active_price": activation,
        "tpsl_mode": "Full",
    }
    assert label == "P90L90_REAL_BYBIT_NATIVE_TRAIL035_ARM"
    assert validate_exit_mutation(kind, mutation) == mutation


@pytest.mark.parametrize("side,best,last,stop,tp", [
    ("LONG", "100.49", "100.30", "90", "110"),
    ("SHORT", "99.51", "99.70", "110", "90"),
])
def test_already_exchange_armed_trailing_never_generates_another_stop_or_trail(
    side: str, best: str, last: str, stop: str, tp: str,
) -> None:
    position, plan = _scope(side=side)
    fact = _observation(position, best=best, price=last, stop=stop, tp=tp,
                        trailing="0.35")
    assert project_p90l90_real_exit(position, plan, fact) is None


def test_late_unarmed_native_trailing_cannot_masquerade_as_success() -> None:
    position, plan = _scope()
    fact = _observation(position, best="101", price="101", stop="90", tp="110")
    detail = fact.attributes.to_dict()
    detail["p90l90_real"]["exchange_mark_price"] = "101"
    with pytest.raises(P90RealExitUnverified, match="LATE_ARM_UNVERIFIED"):
        project_p90l90_real_exit(
            position, plan, replace(fact, attributes=FrozenPolicy.from_mapping(detail)),
        )


@pytest.mark.parametrize("side", ("LONG", "SHORT"))
def test_verified_postfill_can_arm_exchange_trailing_without_waiting_for_next_tick(
    side: str,
) -> None:
    position, plan = _scope(side=side)
    fact = _observation(
        position, best="100.2" if side == "LONG" else "99.8",
        price="100.2" if side == "LONG" else "99.8",
        stop="90" if side == "LONG" else "110",
        tp="110" if side == "LONG" else "90",
        kind="P90L90_REAL_POSTFILL",
    )
    attrs = fact.attributes.to_dict()
    attrs["p90l90_real"].update({
        "durable_cursor_verified": False,
        "fill_to_postfill_source_verified": True,
        "signed_fill_execution_ids": ["verified-bybit-fill-exec-id"],
    })
    fact = replace(
        fact, attributes=FrozenPolicy.from_mapping(attrs),
        source_refs=(*fact.source_refs, "bybit:execution:list:exact-order:fill"),
    )
    evaluation = UniversalExitEngine().evaluate(position, plan, fact)
    assert evaluation.status is ExitEvaluationStatus.DECISION_CREATED
    assert evaluation.decision is not None
    assert evaluation.decision.action_kind is ExitActionKind.SET_TRAILING
    assert evaluation.decision.requested_mutation.to_dict()["distance"] == "0.35"


@pytest.mark.parametrize("which", ("no-coverage", "missing-fill", "fake-fill"))
def test_postfill_trailing_requires_independent_source_and_real_fill_execution(
    which: str,
) -> None:
    position, plan = _scope()
    fact = _observation(
        position, best="100.2", price="100.2", stop="90", tp="110",
        kind="P90L90_REAL_POSTFILL",
    )
    attrs = fact.attributes.to_dict()
    attrs["p90l90_real"].update({
        "durable_cursor_verified": False,
        "fill_to_postfill_source_verified": which != "no-coverage",
        "signed_fill_execution_ids": (
            [] if which == "missing-fill" else ["" if which == "fake-fill" else "exec"]
        ),
    })
    fact = replace(fact, attributes=FrozenPolicy.from_mapping(attrs))
    result = UniversalExitEngine().evaluate(position, plan, fact)
    assert result.status is ExitEvaluationStatus.BLOCKED
    assert result.decision is None


@pytest.mark.parametrize(
    ("side", "best", "last", "stop", "tp", "target"),
    [
        ("LONG", "101", "100.8", "90", "110", "109"),
        ("SHORT", "99", "99.1", "110", "90", "91"),
    ],
)
def test_closed_5m_rejects_missing_exchange_trail_even_with_valid_tp(
    side: str, best: str, last: str, stop: str, tp: str, target: str,
) -> None:
    position, plan = _scope(side=side)
    fact = _observation(
        position, best=best, price=last, stop=stop, tp=tp,
        target=target, kind="P90L90_REAL_CLOSED_5M", trailing="0",
    )
    with pytest.raises(
        P90RealExitUnverified, match="CLOSED_5M_BYBIT_TRAILING_NOT_ARMED",
    ):
        project_p90l90_real_exit(position, plan, fact)
    blocked = UniversalExitEngine().evaluate(position, plan, fact)
    assert blocked.status is ExitEvaluationStatus.BLOCKED
    assert blocked.decision is None



@pytest.mark.parametrize(
    ("side", "best", "last", "stop", "tp", "target"),
    [
        ("LONG", "101", "100.8", "90", "110", "109"),
        ("SHORT", "99", "99.1", "110", "90", "91"),
    ],
)
def test_real_exit_updated_causal_l53_tp_uses_universal_exchange_action(
    side: str, best: str, last: str, stop: str, tp: str, target: str,
) -> None:
    position, plan = _scope(side=side)
    fact = _observation(
        position, best=best, price=last, stop=stop, tp=tp,
        target=target, kind="P90L90_REAL_CLOSED_5M", trailing="0.35",
    )
    result = project_p90l90_real_exit(position, plan, fact)
    assert result is not None
    kind, mutation, _ = result
    assert kind is ExitActionKind.SET_TP
    assert mutation["take_profit_price"] == target
    assert validate_exit_mutation(kind, mutation) == mutation


@pytest.mark.parametrize(
    ("side", "best", "last", "stop", "tp", "target"),
    [
        ("LONG", "101", "101", "90", "110", "100.60"),
        ("SHORT", "99", "99", "110", "90", "99.40"),
    ],
)
def test_new_marketable_raw_target_uses_existing_reduce_only_close(
    side: str, best: str, last: str, stop: str, tp: str, target: str,
) -> None:
    position, plan = _scope(side=side)
    fact = _observation(
        position, best=best, price=last, stop=stop, tp=tp,
        target=target, kind="P90L90_REAL_CLOSED_5M", trailing="0.35",
    )
    result = project_p90l90_real_exit(position, plan, fact)
    assert result is not None
    kind, mutation, label = result
    assert kind is ExitActionKind.CLOSE
    assert mutation == {"quantity": "ALL", "order_type": "MARKET"}
    assert validate_exit_mutation(kind, mutation) == mutation
    assert label == "P90L90_REAL_NEW_MARKETABLE_RAW_TP"


@pytest.mark.parametrize("field", (
    "trading_account_id", "strategy_execution_binding_id",
    "exchange_position_key", "actual_fill_confirmed",
    "protection_exchange_confirmed", "durable_cursor_verified",
))
def test_real_exit_rejects_unproven_account_or_exchange_state(field: str) -> None:
    position, plan = _scope(main=False)
    fact = _observation(
        position, best="101", price="100.9", stop="90", tp="110",
    )
    attrs = fact.attributes.to_dict()
    real = dict(attrs["p90l90_real"])
    real[field] = "wrong" if isinstance(real[field], str) else False
    attrs["p90l90_real"] = real
    corrupted = replace(fact, attributes=FrozenPolicy.from_mapping(attrs))
    with pytest.raises(P90RealExitUnverified, match="ACCOUNT_OR_EXCHANGE_UNPROVEN"):
        project_p90l90_real_exit(position, plan, corrupted)


def test_real_exit_rejects_unconfirmed_position_and_stale_native_event() -> None:
    position, plan = _scope()
    fact = _observation(position, best="101", price="100.9", stop="90", tp="110")
    with pytest.raises(P90RealExitUnverified, match="POSITION_OR_SOURCE_NOT_VERIFIED"):
        project_p90l90_real_exit(
            replace(position, initial_protection_confirmed_at=None), plan, fact,
        )
    with pytest.raises(P90RealExitUnverified, match="POSITION_OR_SOURCE_NOT_VERIFIED"):
        project_p90l90_real_exit(replace(position, fill_at=fact.event_at), plan, fact)


def test_real_projection_preserves_v3_and_independent_account_lineage() -> None:
    position, plan = _scope(main=False)
    assert position.strategy_id == SUB_ID
    assert position.strategy_version == VERSION
    fact = _observation(position, best="100.2", price="100.2", stop="90", tp="110")
    result = project_p90l90_real_exit(position, plan, fact)
    assert result is not None and result[0] is ExitActionKind.SET_TRAILING


@pytest.mark.parametrize("side,best,last,stop,tp", [
    ("LONG", "100.2", "100.2", "90", "110"),
    ("SHORT", "99.8", "99.8", "110", "90"),
])
def test_shared_universal_exit_engine_creates_bybit_native_trailing_arm(
    side: str, best: str, last: str, stop: str, tp: str,
) -> None:
    position, plan = _scope(side=side)
    fact = _observation(position, best=best, price=last, stop=stop, tp=tp)
    result = UniversalExitEngine().evaluate(position, plan, fact)
    assert result.status is ExitEvaluationStatus.DECISION_CREATED
    assert result.decision is not None
    assert result.decision.action_kind is ExitActionKind.SET_TRAILING
    assert result.decision.strategy_position_id == position.strategy_position_id
    assert result.decision.exit_plan_fingerprint == plan.exit_plan_fingerprint
    assert result.decision.source_refs == fact.source_refs
    assert result.decision.requested_mutation.to_dict() == {
        "distance": "0.35",
        "active_price": "100.50" if side == "LONG" else "99.50",
        "tpsl_mode": "Full",
    }
    assert UniversalExitEngine().evaluate(position, plan, fact).decision == result.decision


def test_shared_universal_exit_engine_never_promotes_unproven_real_fact() -> None:
    position, plan = _scope()
    fact = _observation(
        position, best="101", price="100.9", stop="90", tp="110",
    )
    attrs = fact.attributes.to_dict()
    original = dict(attrs["p90l90_real"])
    original["durable_cursor_verified"] = False
    attrs["p90l90_real"] = original
    blocked = UniversalExitEngine().evaluate(
        position, plan, replace(fact, attributes=FrozenPolicy.from_mapping(attrs)),
    )
    assert blocked.status is ExitEvaluationStatus.BLOCKED
    assert blocked.decision is None
    assert "UNPROVEN" in blocked.reason
