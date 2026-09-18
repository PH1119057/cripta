from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import (
    ExitPlan,
    FrozenPolicy,
    TradeDirection,
)
from bybit_workbench.universal_exit.contracts import (
    ExitActionKind,
    ExitEvaluationStatus,
    ExitObservation,
    ExitRepeatPolicy,
)
from bybit_workbench.universal_exit.engine import UniversalExitEngine

NOW = datetime(2026, 9, 18, 18, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def policy(value: dict[str, object] | None = None) -> FrozenPolicy:
    return FrozenPolicy.from_mapping(value or {})


def position(*, exit_fp: str = "exit-fp") -> StrategyPosition:
    return StrategyPosition(
        strategy_position_id="position-1",
        account_ref="BYBIT:UNIFIED",
        exchange_position_key="BYBIT:UNIFIED:LINEAR:USDT:UNIUSDT:0",
        position_idx=0,
        strategy_id="strategy-a",
        strategy_version="1.0",
        strategy_config_fingerprint="strategy-fp",
        strategy_activation_id="activation-1",
        signal_id="signal-1",
        strategy_attempt_id="attempt-1",
        entry_decision_id="entry-decision-1",
        entry_execution_request_id="entry-request-1",
        entry_plan_fingerprint="entry-fp",
        exit_plan_fingerprint=exit_fp,
        entry_command_id="entry-command-1",
        symbol="UNIUSDT",
        direction=TradeDirection.LONG,
        actual_avg_fill=Decimal("10"),
        actual_qty=Decimal("2"),
        fill_at=NOW,
    )


def exit_plan(
    rules: list[dict[str, object]] | None,
    *,
    exit_fp: str = "exit-fp",
    conflict: dict[str, object] | None = None,
) -> ExitPlan:
    payload: dict[str, object] = {"exit_plan_version": "1"}
    if rules is not None:
        payload["rules"] = rules
    if conflict is not None:
        payload["conflict_policy"] = conflict
    return ExitPlan(
        strategy_id="strategy-a",
        strategy_version="1.0",
        strategy_config_fingerprint="strategy-fp",
        strategy_activation_id="activation-1",
        exit_plan_version="1",
        exit_plan_fingerprint=exit_fp,
        exit_policy=policy(payload),
        protection_policy=policy({}),
    )


def observation(
    *,
    attributes: dict[str, object] | None = None,
    event_kind: str = "PRICE",
    position_id: str = "position-1",
    symbol: str = "UNIUSDT",
) -> ExitObservation:
    return ExitObservation(
        observation_id="exit-observation-1",
        strategy_position_id=position_id,
        symbol=symbol,
        event_at=NOW,
        observed_at=NOW,
        received_at=NOW,
        event_kind=event_kind,
        attributes=policy(attributes),
        source_refs=("ticker:exact:1",),
    )


def conflict(
    *,
    priority_order: str = "HIGHER_WINS",
) -> dict[str, object]:
    return {
        "mode": "PRIORITY",
        "priority_order": priority_order,
        "tie_break": "FAIL_CLOSED",
    }


def rule(
    action_kind: str,
    *,
    rule_id: str = "rule-1",
    priority: int = 10,
    repeat_policy: str = "EACH_MATCH",
    predicate: dict[str, object] | None = None,
    required_fact_paths: list[str] | None = None,
    mutation: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "priority": priority,
        "repeat_policy": repeat_policy,
        "required_fact_paths": (
            ["fact.pnl_pct"] if required_fact_paths is None else required_fact_paths
        ),
        "predicate": predicate
        or {
            "op": "COMPARE",
            "params": {
                "path": "fact.pnl_pct",
                "comparator": "GTE",
                "value": "1.0",
            },
        },
        "action": {
            "kind": action_kind,
            "mutation": mutation or {"source": rule_id},
        },
    }


def test_compatibility_exit_plan_without_rules_has_no_executable_policy() -> None:
    result = UniversalExitEngine().evaluate(
        position(),
        exit_plan(None),
        observation(attributes={"pnl_pct": "2"}),
    )
    assert result.status is ExitEvaluationStatus.NO_EXECUTABLE_EXIT_RULES
    assert result.decision is None


@pytest.mark.parametrize(
    ("action_kind", "mutation"),
    [
        ("SET_STOP", {"stop_price": "9.9"}),
        ("SET_TP", {"take_profit_price": "10.5"}),
        ("SET_PROTECTION", {"stop_price": "9.9", "take_profit_price": "10.5"}),
        ("SET_TRAILING", {"distance_pct": "0.2"}),
        ("REDUCE", {"quantity": "0.5"}),
        ("CLOSE", {"quantity": "ALL"}),
    ],
)
def test_all_p5_typed_actions_are_preserved_from_exit_plan(
    action_kind: str,
    mutation: dict[str, object],
) -> None:
    plan = exit_plan(
        [rule(action_kind, mutation=mutation)],
        conflict=conflict(),
    )
    result = UniversalExitEngine().evaluate(
        position(),
        plan,
        observation(attributes={"pnl_pct": "1.2"}),
    )
    assert result.status is ExitEvaluationStatus.DECISION_CREATED
    assert result.decision is not None
    assert result.decision.action_kind is ExitActionKind(action_kind)
    assert result.decision.requested_mutation.to_dict() == mutation
    assert result.selected_priority == 10
    assert result.repeat_policy is ExitRepeatPolicy.EACH_MATCH
    assert result.decision.source_refs == ("ticker:exact:1",)


def test_unsupported_action_is_fail_closed() -> None:
    result = UniversalExitEngine().evaluate(
        position(),
        exit_plan([rule("HEDGE")], conflict=conflict()),
        observation(attributes={"pnl_pct": "1.2"}),
    )
    assert result.status is ExitEvaluationStatus.BLOCKED
    assert result.decision is None
    assert "unsupported action kind HEDGE" in result.reason


def test_exact_position_plan_lineage_is_mandatory() -> None:
    result = UniversalExitEngine().evaluate(
        position(exit_fp="exact-position-exit"),
        exit_plan(
            [rule("CLOSE")],
            exit_fp="different-exit",
            conflict=conflict(),
        ),
        observation(attributes={"pnl_pct": "1.2"}),
    )
    assert result.status is ExitEvaluationStatus.BLOCKED
    assert result.reason == "EXIT_PLAN_POSITION_LINEAGE_MISMATCH"


def test_observation_must_belong_to_exact_position_and_symbol() -> None:
    engine = UniversalExitEngine()
    plan = exit_plan([rule("CLOSE")], conflict=conflict())
    wrong_position = engine.evaluate(
        position(),
        plan,
        observation(position_id="other-position", attributes={"pnl_pct": "1.2"}),
    )
    assert wrong_position.status is ExitEvaluationStatus.BLOCKED
    assert wrong_position.reason == "OBSERVATION_POSITION_MISMATCH"

    wrong_symbol = engine.evaluate(
        position(),
        plan,
        observation(symbol="BTCUSDT", attributes={"pnl_pct": "1.2"}),
    )
    assert wrong_symbol.status is ExitEvaluationStatus.BLOCKED
    assert wrong_symbol.reason == "OBSERVATION_SYMBOL_MISMATCH"


def test_required_fact_missing_is_not_treated_as_false_or_zero() -> None:
    result = UniversalExitEngine().evaluate(
        position(),
        exit_plan([rule("CLOSE")], conflict=conflict()),
        observation(attributes={}),
    )
    assert result.status is ExitEvaluationStatus.BLOCKED
    assert result.reason == "MISSING_REQUIRED_EXIT_FACT:fact.pnl_pct"


def test_stateful_operator_requires_explicit_future_exit_state_contract() -> None:
    stateful = rule(
        "CLOSE",
        predicate={"op": "TOUCH"},
        required_fact_paths=[],
    )
    result = UniversalExitEngine().evaluate(
        position(),
        exit_plan([stateful], conflict=conflict()),
        observation(event_kind="TOUCH"),
    )
    assert result.status is ExitEvaluationStatus.BLOCKED
    assert result.reason == "STATEFUL_EXIT_OPERATOR_REQUIRES_EXPLICIT_STATE_CONTRACT:TOUCH"


def test_context_path_requires_explicit_future_context_contract() -> None:
    contextual = rule(
        "CLOSE",
        predicate={
            "op": "COMPARE",
            "params": {
                "path": "context.dispatcher.market.score",
                "comparator": "GTE",
                "value": 1,
            },
        },
        required_fact_paths=[],
    )
    result = UniversalExitEngine().evaluate(
        position(),
        exit_plan([contextual], conflict=conflict()),
        observation(attributes={}),
    )
    assert result.status is ExitEvaluationStatus.BLOCKED
    assert result.reason.startswith("STATELESS_EXIT_PATH_REQUIRES_EXPLICIT_CONTEXT_CONTRACT:")


def test_priority_order_is_explicitly_owned_by_exit_plan() -> None:
    rules = [
        rule("SET_STOP", rule_id="low-number", priority=1),
        rule("CLOSE", rule_id="high-number", priority=9),
    ]
    higher = UniversalExitEngine().evaluate(
        position(),
        exit_plan(rules, conflict=conflict(priority_order="HIGHER_WINS")),
        observation(attributes={"pnl_pct": "2"}),
    )
    assert higher.decision is not None
    assert higher.decision.rule_id == "high-number"

    lower = UniversalExitEngine().evaluate(
        position(),
        exit_plan(rules, conflict=conflict(priority_order="LOWER_WINS")),
        observation(attributes={"pnl_pct": "2"}),
    )
    assert lower.decision is not None
    assert lower.decision.rule_id == "low-number"


def test_equal_priority_conflict_fails_closed() -> None:
    rules = [
        rule("SET_STOP", rule_id="a", priority=5),
        rule("CLOSE", rule_id="b", priority=5),
    ]
    result = UniversalExitEngine().evaluate(
        position(),
        exit_plan(rules, conflict=conflict()),
        observation(attributes={"pnl_pct": "2"}),
    )
    assert result.status is ExitEvaluationStatus.BLOCKED
    assert result.reason == "EXIT_RULE_PRIORITY_TIE_FAIL_CLOSED"
    assert result.matched_rule_ids == ("a", "b")


def test_missing_or_unsupported_conflict_policy_fails_closed() -> None:
    engine = UniversalExitEngine()
    missing = engine.evaluate(
        position(),
        exit_plan([rule("CLOSE")], conflict=None),
        observation(attributes={"pnl_pct": "2"}),
    )
    assert missing.status is ExitEvaluationStatus.BLOCKED
    assert missing.reason == "EXIT_CONFLICT_POLICY_REQUIRED"

    unsupported = engine.evaluate(
        position(),
        exit_plan(
            [rule("CLOSE")],
            conflict={
                "mode": "PRIORITY",
                "priority_order": "MAGIC",
                "tie_break": "FAIL_CLOSED",
            },
        ),
        observation(attributes={"pnl_pct": "2"}),
    )
    assert unsupported.status is ExitEvaluationStatus.BLOCKED
    assert unsupported.reason == "EXIT_CONFLICT_POLICY_UNSUPPORTED"


def test_once_per_position_rule_does_not_recreate_decision() -> None:
    plan = exit_plan(
        [
            rule(
                "CLOSE",
                rule_id="once",
                repeat_policy="ONCE_PER_POSITION",
            )
        ],
        conflict=conflict(),
    )
    engine = UniversalExitEngine()
    first = engine.evaluate(
        position(),
        plan,
        observation(attributes={"pnl_pct": "2"}),
    )
    assert first.status is ExitEvaluationStatus.DECISION_CREATED
    second = engine.evaluate(
        position(),
        plan,
        observation(attributes={"pnl_pct": "2"}),
        prior_once_rule_ids=frozenset({"once"}),
    )
    assert second.status is ExitEvaluationStatus.NO_MATCH
    assert second.decision is None


def test_decision_and_evaluation_ids_are_deterministic() -> None:
    engine = UniversalExitEngine()
    plan = exit_plan([rule("CLOSE")], conflict=conflict())
    event = observation(attributes={"pnl_pct": "2"})
    first = engine.evaluate(position(), plan, event)
    second = engine.evaluate(position(), plan, event)
    assert first.evaluation_id == second.evaluation_id
    assert first.decision is not None
    assert second.decision is not None
    assert first.decision.exit_decision_id == second.decision.exit_decision_id


def test_p5_source_has_no_h3_h9_percent_default_or_exchange_bridge() -> None:
    engine_source = (ROOT / "src/bybit_workbench/universal_exit/engine.py").read_text(
        encoding="utf-8"
    )
    storage_source = (ROOT / "src/bybit_workbench/universal_exit/storage.py").read_text(
        encoding="utf-8"
    )
    combined = engine_source + storage_source
    for forbidden in (
        "H3",
        "H9",
        "hard_stop",
        "take_profit_pct",
        "trailing_distance",
        "INSERT INTO runtime.trade_commands",
        "UPDATE runtime.trade_commands",
        "ExitExecutionRequest(",
        "/v5/order",
        "/v5/position",
    ):
        assert forbidden not in combined
