from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry import (
    ActivePlanRegistry,
    FrozenPolicy,
    StrategyActivation,
    StrategyCard,
    TouchPolicy,
    TradeDirection,
)
from bybit_workbench.universal_exit import ExitActionKind, ExitDecision, ExitExecutionRequest

NOW = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)


def policy(value: dict[str, object] | None = None) -> FrozenPolicy:
    return FrozenPolicy.from_mapping(value or {})


def card() -> StrategyCard:
    return StrategyCard.build(
        strategy_id="strategy-a",
        strategy_version="1",
        name="A",
        description="registry lifecycle",
        scope=policy({"kind": "symbols"}),
        symbols=("UNIUSDT",),
        direction_policy=(TradeDirection.LONG,),
        entry_policy=policy(
            {
                "entry_plan_version": "entry-1",
                "predicate": {"op": "TOUCH"},
                "watch_policy": {"enabled": False},
            }
        ),
        exit_policy=policy({"exit_plan_version": "exit-1"}),
        capital_policy=policy({}),
        protection_policy=policy({}),
        lifecycle_policy=policy({"post_signal_outcome_policy": {"enabled": False}}),
        touch_policy=TouchPolicy(accepted_touch_numbers=(1,), accept_touch_from=1),
        approved_at=NOW,
    )


def test_registry_keeps_exact_exit_plan_after_strategy_deactivation() -> None:
    strategy = card()
    activation = StrategyActivation(
        activation_id="activation-a",
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.strategy_version,
        strategy_config_fingerprint=strategy.strategy_config_fingerprint,
        enabled=True,
        enabled_at=NOW,
    )
    registry = ActivePlanRegistry()
    registry.register_card(strategy)
    entry = registry.activate(activation)
    exit_plan = registry.active_exit_plans()[0]

    registry.set_enabled("activation-a", False, changed_at=NOW)

    assert registry.active_entry_plans() == ()
    assert registry.active_exit_plans() == ()
    assert registry.entry_plan_by_fingerprint(entry.entry_plan_fingerprint) == entry
    restored_exit = registry.exit_plan_by_fingerprint(exit_plan.exit_plan_fingerprint)
    assert restored_exit.exit_plan_fingerprint == exit_plan.exit_plan_fingerprint
    exact_entry, exact_exit = registry.exact_plan_pair("activation-a")
    assert exact_entry.entry_plan_fingerprint == entry.entry_plan_fingerprint
    assert exact_exit.exit_plan_fingerprint == exit_plan.exit_plan_fingerprint


def position() -> StrategyPosition:
    return StrategyPosition(
        strategy_position_id="SP-1",
        account_ref="BYBIT:UNIFIED:ACCOUNT",
        exchange_position_key="BYBIT:UNIFIED:LINEAR:USDT:UNIUSDT:0",
        position_idx=0,
        strategy_id="strategy-a",
        strategy_version="1",
        strategy_config_fingerprint="strategy-fp",
        strategy_activation_id="activation-a",
        signal_id="signal-1",
        strategy_attempt_id="attempt-1",
        entry_decision_id="decision-1",
        entry_execution_request_id="entry-request-1",
        entry_plan_fingerprint="entry-fp",
        exit_plan_fingerprint="exit-fp",
        entry_command_id="entry-command-1",
        symbol="UNIUSDT",
        direction=TradeDirection.LONG,
        actual_avg_fill=Decimal("8.50"),
        actual_qty=Decimal("2"),
        fill_at=NOW,
    )


def test_exit_request_preserves_exact_position_and_plan_lineage() -> None:
    pos = position()
    decision = ExitDecision(
        exit_decision_id="exit-decision-1",
        strategy_position_id=pos.strategy_position_id,
        strategy_id=pos.strategy_id,
        strategy_version=pos.strategy_version,
        strategy_config_fingerprint=pos.strategy_config_fingerprint,
        exit_plan_fingerprint=pos.exit_plan_fingerprint,
        rule_id="rule-stop-1",
        action_kind=ExitActionKind.SET_STOP,
        requested_mutation=policy({"target_stop_price": "8.42"}),
        source_refs=("fact:1",),
        decided_at=NOW,
    )
    request = ExitExecutionRequest.from_decision(
        execution_request_id="exit-request-1",
        decision=decision,
        position=pos,
        requested_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    assert request.strategy_position_id == "SP-1"
    assert request.exit_plan_fingerprint == "exit-fp"
    assert request.exchange_position_key == pos.exchange_position_key
    assert request.requested_mutation.to_dict() == {"target_stop_price": "8.42"}


def test_exit_request_rejects_cross_position_lineage() -> None:
    pos = position()
    bad = ExitDecision(
        exit_decision_id="exit-decision-bad",
        strategy_position_id=pos.strategy_position_id,
        strategy_id=pos.strategy_id,
        strategy_version=pos.strategy_version,
        strategy_config_fingerprint=pos.strategy_config_fingerprint,
        exit_plan_fingerprint="other-exit",
        rule_id="r",
        action_kind=ExitActionKind.CLOSE,
        requested_mutation=policy({"close_qty": "2"}),
        source_refs=(),
        decided_at=NOW,
    )
    with pytest.raises(ValueError, match="lineage mismatch"):
        ExitExecutionRequest.from_decision(
            execution_request_id="exit-request-bad",
            decision=bad,
            position=pos,
            requested_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
        )


def test_strategy_position_rejects_incomplete_identity() -> None:
    pos = position()
    values = {name: getattr(pos, name) for name in pos.__dataclass_fields__}
    values["exit_plan_fingerprint"] = ""
    with pytest.raises(ValueError, match="exit_plan_fingerprint"):
        StrategyPosition(**values)


def test_entry_execution_request_has_explicit_typed_runtime_name() -> None:
    from bybit_workbench.universal_entry.contracts import (
        EntryExecutionRequest,
        ExecutionRequest,
    )

    assert EntryExecutionRequest.__name__ == "EntryExecutionRequest"
    assert ExecutionRequest is EntryExecutionRequest
