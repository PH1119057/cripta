from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import ExitPlan, FrozenPolicy, TradeDirection
from bybit_workbench.universal_exit.contracts import (
    ExitActionKind,
    ExitDecision,
)
from bybit_workbench.universal_exit.execution_bridge import (
    ExitExecutionBridgeBlockCode,
    ExitExecutionBridgeBlocked,
    prepare_exit_execution_request,
    prepare_runtime_exit_command,
    validate_exit_mutation,
)

NOW = datetime(2026, 9, 18, 20, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def policy(value: dict[str, object]) -> FrozenPolicy:
    return FrozenPolicy.from_mapping(value)


def position() -> StrategyPosition:
    return StrategyPosition(
        strategy_position_id="sp-p6",
        account_ref="BYBIT:UNIFIED",
        exchange_position_key="BYBIT:UNIFIED:LINEAR:USDT:UNIUSDT:0",
        position_idx=0,
        strategy_id="strategy-p6",
        strategy_version="1",
        strategy_config_fingerprint="strategy-p6-fp",
        strategy_activation_id="activation-p6",
        signal_id="signal-p6",
        strategy_attempt_id="attempt-p6",
        entry_decision_id="entry-decision-p6",
        entry_execution_request_id="entry-request-p6",
        entry_plan_fingerprint="entry-plan-p6",
        exit_plan_fingerprint="exit-plan-p6",
        entry_command_id="entry-command-p6",
        symbol="UNIUSDT",
        direction=TradeDirection.LONG,
        actual_avg_fill=Decimal("10"),
        actual_qty=Decimal("2"),
        fill_at=NOW - timedelta(minutes=1),
    )


def plan(*, max_age: object = 30) -> ExitPlan:
    return ExitPlan(
        strategy_id="strategy-p6",
        strategy_version="1",
        strategy_config_fingerprint="strategy-p6-fp",
        strategy_activation_id="activation-p6",
        exit_plan_version="1",
        exit_plan_fingerprint="exit-plan-p6",
        exit_policy=policy(
            {
                "execution_policy": {
                    "max_request_age_seconds": max_age,
                }
            }
        ),
        protection_policy=policy({}),
    )


def decision(
    action: ExitActionKind,
    mutation: dict[str, object],
    *,
    decided_at: datetime = NOW,
) -> ExitDecision:
    return ExitDecision(
        exit_decision_id=f"decision-{action.value}",
        strategy_position_id="sp-p6",
        strategy_id="strategy-p6",
        strategy_version="1",
        strategy_config_fingerprint="strategy-p6-fp",
        exit_plan_fingerprint="exit-plan-p6",
        rule_id=f"rule-{action.value}",
        action_kind=action,
        requested_mutation=policy(mutation),
        source_refs=("exit-fact:p6",),
        decided_at=decided_at,
    )


@pytest.mark.parametrize(
    ("action", "mutation"),
    [
        (
            ExitActionKind.SET_STOP,
            {
                "stop_price": "9.90",
                "trigger_by": "LastPrice",
                "tpsl_mode": "Full",
                "order_type": "Market",
            },
        ),
        (
            ExitActionKind.SET_TP,
            {
                "take_profit_price": "10.50",
                "trigger_by": "MarkPrice",
                "tpsl_mode": "Full",
                "order_type": "Market",
            },
        ),
        (
            ExitActionKind.SET_PROTECTION,
            {
                "stop_price": "9.90",
                "take_profit_price": "10.50",
                "sl_trigger_by": "LastPrice",
                "tp_trigger_by": "MarkPrice",
                "tpsl_mode": "Full",
                "sl_order_type": "Market",
                "tp_order_type": "Market",
            },
        ),
        (
            ExitActionKind.SET_TRAILING,
            {
                "distance": "0.05",
                "active_price": "10.20",
                "tpsl_mode": "Full",
            },
        ),
        (
            ExitActionKind.REDUCE,
            {"quantity": "0.5", "order_type": "Market"},
        ),
        (
            ExitActionKind.CLOSE,
            {"quantity": "ALL", "order_type": "Market"},
        ),
    ],
)
def test_p6_all_actions_preserve_exact_strategy_mutation(
    action: ExitActionKind,
    mutation: dict[str, object],
) -> None:
    pos = position()
    dec = decision(action, mutation)
    request = prepare_exit_execution_request(dec, pos, plan(), now=NOW)
    assert request.requested_at == NOW
    assert request.expires_at == NOW + timedelta(seconds=30)
    prepared = prepare_runtime_exit_command(request, pos, now=NOW)
    assert prepared.execution_request == request
    assert prepared.payload["action_kind"] == action.value
    assert prepared.payload["requested_mutation"] == validate_exit_mutation(action, mutation)
    assert prepared.payload["strategy_position_id"] == pos.strategy_position_id
    assert prepared.payload["exchange_position_key"] == pos.exchange_position_key


def test_exit_request_id_and_runtime_command_id_are_deterministic() -> None:
    pos = position()
    dec = decision(
        ExitActionKind.CLOSE,
        {"quantity": "ALL", "order_type": "Market"},
    )
    one = prepare_exit_execution_request(dec, pos, plan(), now=NOW)
    two = prepare_exit_execution_request(dec, pos, plan(), now=NOW + timedelta(seconds=1))
    assert one == two
    cmd_one = prepare_runtime_exit_command(one, pos, now=NOW)
    cmd_two = prepare_runtime_exit_command(two, pos, now=NOW + timedelta(seconds=1))
    assert cmd_one.command_id == cmd_two.command_id
    assert cmd_one.payload == cmd_two.payload


def test_expired_decision_still_materializes_request_but_cannot_dispatch() -> None:
    pos = position()
    dec = decision(
        ExitActionKind.CLOSE,
        {"quantity": "ALL", "order_type": "Market"},
        decided_at=NOW - timedelta(seconds=40),
    )
    request = prepare_exit_execution_request(dec, pos, plan(max_age=30), now=NOW)
    assert request.expires_at == NOW - timedelta(seconds=10)
    with pytest.raises(ExitExecutionBridgeBlocked) as blocked:
        prepare_runtime_exit_command(request, pos, now=NOW)
    assert blocked.value.code is ExitExecutionBridgeBlockCode.REQUEST_EXPIRED


def test_missing_strategy_owned_exit_lifetime_fails_closed() -> None:
    with pytest.raises(ExitExecutionBridgeBlocked) as blocked:
        prepare_exit_execution_request(
            decision(
                ExitActionKind.CLOSE,
                {"quantity": "ALL", "order_type": "Market"},
            ),
            position(),
            plan(max_age=None),
            now=NOW,
        )
    assert blocked.value.code is ExitExecutionBridgeBlockCode.POLICY_INCOMPLETE


def test_unknown_mutation_field_is_not_silently_ignored() -> None:
    pos = position()
    dec = decision(
        ExitActionKind.SET_STOP,
        {
            "stop_price": "9.9",
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
            "order_type": "Market",
            "hidden_default": "forbidden",
        },
    )
    request = prepare_exit_execution_request(dec, pos, plan(), now=NOW)
    with pytest.raises(ExitExecutionBridgeBlocked) as blocked:
        prepare_runtime_exit_command(request, pos, now=NOW)
    assert blocked.value.code is ExitExecutionBridgeBlockCode.POLICY_UNSUPPORTED
    assert "hidden_default" in blocked.value.reason


def test_close_and_reduce_are_not_interchangeable() -> None:
    with pytest.raises(ExitExecutionBridgeBlocked):
        validate_exit_mutation(
            ExitActionKind.CLOSE,
            {"quantity": "0.5", "order_type": "Market"},
        )
    reduced = validate_exit_mutation(
        ExitActionKind.REDUCE,
        {"quantity": "0.5", "order_type": "Market"},
    )
    assert reduced["quantity"] == "0.5"


def test_cross_lineage_is_fail_closed_before_request_creation() -> None:
    pos = position()
    bad = ExitDecision(
        exit_decision_id="bad",
        strategy_position_id=pos.strategy_position_id,
        strategy_id=pos.strategy_id,
        strategy_version=pos.strategy_version,
        strategy_config_fingerprint=pos.strategy_config_fingerprint,
        exit_plan_fingerprint="wrong-exit-plan",
        rule_id="rule",
        action_kind=ExitActionKind.CLOSE,
        requested_mutation=policy({"quantity": "ALL", "order_type": "Market"}),
        source_refs=("fact",),
        decided_at=NOW,
    )
    with pytest.raises(ExitExecutionBridgeBlocked) as blocked:
        prepare_exit_execution_request(bad, pos, plan(), now=NOW)
    assert blocked.value.code is ExitExecutionBridgeBlockCode.IDENTITY_MISMATCH


def test_p6_bridge_has_no_h3_h9_or_percent_policy_defaults() -> None:
    source = (ROOT / "src/bybit_workbench/universal_exit/execution_bridge.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "H3",
        "H9",
        "break_even",
        "take_profit_pct",
        "stop_loss_pct",
        'or "0.2"',
        "protection_plan(",
    ):
        assert forbidden not in source
