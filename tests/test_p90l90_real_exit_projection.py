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
    FrozenPolicy, StrategyActivation, TradeDirection,
)
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    VERSION, real_v3_cards,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_exit.contracts import (
    ExitActionKind, ExitObservation,
)
from bybit_workbench.universal_exit.execution_bridge import validate_exit_mutation
from bybit_workbench.universal_exit.p90l90_real_v3 import (
    P90RealExitUnverified, project_p90l90_real_exit,
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
    ("side", "best", "last", "stop", "tp", "expected_stop"),
    [
        ("LONG", "101", "100.9", "90", "110", "100.65"),
        ("SHORT", "99", "99.1", "110", "90", "99.35"),
    ],
)
def test_real_exit_approved_trail035_tightens_stop_with_actual_fill(
    side: str, best: str, last: str, stop: str, tp: str, expected_stop: str,
) -> None:
    position, plan = _scope(side=side)
    event = _observation(position, best=best, price=last, stop=stop, tp=tp)
    result = project_p90l90_real_exit(position, plan, event)
    assert result is not None
    kind, mutation, label = result
    assert kind is ExitActionKind.SET_STOP
    assert mutation["stop_price"] == expected_stop
    assert label == "P90L90_REAL_TRAIL035_TIGHTEN"
    assert validate_exit_mutation(kind, mutation) == mutation


@pytest.mark.parametrize(
    ("side", "best", "last", "stop", "tp"),
    [
        ("LONG", "100.49", "100.3", "90", "110"),
        ("SHORT", "99.51", "99.7", "110", "90"),
    ],
)
def test_real_exit_no_trailing_before_exact_half_percent(
    side: str, best: str, last: str, stop: str, tp: str,
) -> None:
    position, plan = _scope(side=side)
    fact = _observation(position, best=best, price=last, stop=stop, tp=tp)
    assert project_p90l90_real_exit(position, plan, fact) is None


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
        target=target, kind="P90L90_REAL_CLOSED_5M",
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
        target=target, kind="P90L90_REAL_CLOSED_5M",
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
    fact = _observation(position, best="101", price="100.9", stop="90", tp="110")
    result = project_p90l90_real_exit(position, plan, fact)
    assert result is not None and result[0] is ExitActionKind.SET_STOP
