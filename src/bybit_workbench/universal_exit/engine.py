from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import (
    ExitPlan,
    FrozenPolicy,
    MarketFactEnvelope,
)
from bybit_workbench.universal_entry.dsl import (
    PredicateNode,
    UnsupportedOperatorError,
    evaluate_stateless_predicate,
    predicate_from_mapping,
)
from bybit_workbench.universal_entry.fingerprint import fingerprint

from .contracts import (
    ExitActionKind,
    ExitDecision,
    ExitEvaluation,
    ExitEvaluationStatus,
    ExitObservation,
    ExitRepeatPolicy,
)


class ExitConflictMode(StrEnum):
    PRIORITY = "PRIORITY"


class ExitPriorityOrder(StrEnum):
    HIGHER_WINS = "HIGHER_WINS"
    LOWER_WINS = "LOWER_WINS"


class ExitTieBreak(StrEnum):
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass(frozen=True, slots=True)
class ExitRule:
    rule_id: str
    priority: int
    repeat_policy: ExitRepeatPolicy
    required_fact_paths: tuple[str, ...]
    predicate: PredicateNode
    action_kind: ExitActionKind
    requested_mutation: FrozenPolicy


class ExitPlanContractError(ValueError):
    pass


def _evaluation_id(
    position: StrategyPosition,
    plan: ExitPlan,
    observation: ExitObservation,
) -> str:
    return (
        "exit-eval-"
        + fingerprint(
            {
                "strategy_position_id": position.strategy_position_id,
                "exit_plan_fingerprint": plan.exit_plan_fingerprint,
                "observation_id": observation.observation_id,
            }
        )[:32]
    )


def _blocked(
    position: StrategyPosition,
    plan: ExitPlan,
    observation: ExitObservation,
    reason: str,
    *,
    matched_rule_ids: tuple[str, ...] = (),
) -> ExitEvaluation:
    return ExitEvaluation(
        evaluation_id=_evaluation_id(position, plan, observation),
        observation=observation,
        status=ExitEvaluationStatus.BLOCKED,
        reason=reason,
        matched_rule_ids=matched_rule_ids,
    )


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ExitPlanContractError(f"{label} must be an object")
    return value


def _parse_rule(raw: object) -> ExitRule:
    rule = _require_mapping(raw, "exit rule")
    rule_id = str(rule.get("rule_id") or "").strip()
    if not rule_id:
        raise ExitPlanContractError("exit rule requires rule_id")
    if "priority" not in rule:
        raise ExitPlanContractError(f"exit rule {rule_id} requires explicit priority")
    try:
        priority = int(str(rule["priority"]))
    except (TypeError, ValueError) as exc:
        raise ExitPlanContractError(f"exit rule {rule_id} priority must be integer") from exc

    try:
        repeat_policy = ExitRepeatPolicy(str(rule.get("repeat_policy") or ""))
    except ValueError as exc:
        raise ExitPlanContractError(
            f"exit rule {rule_id} requires supported explicit repeat_policy"
        ) from exc

    required_raw = rule.get("required_fact_paths")
    if not isinstance(required_raw, Sequence) or isinstance(required_raw, (str, bytes)):
        raise ExitPlanContractError(
            f"exit rule {rule_id} requires explicit required_fact_paths list"
        )
    required_paths = tuple(str(path).strip() for path in required_raw)
    if any(not path for path in required_paths):
        raise ExitPlanContractError(f"exit rule {rule_id} has empty required fact path")

    predicate_raw = _require_mapping(rule.get("predicate"), f"exit rule {rule_id}.predicate")
    try:
        predicate = predicate_from_mapping(predicate_raw)
    except (ValueError, UnsupportedOperatorError) as exc:
        raise ExitPlanContractError(f"exit rule {rule_id} invalid predicate: {exc}") from exc

    action = _require_mapping(rule.get("action"), f"exit rule {rule_id}.action")
    try:
        action_kind = ExitActionKind(str(action.get("kind") or ""))
    except ValueError as exc:
        raise ExitPlanContractError(
            f"exit rule {rule_id} has unsupported action kind {action.get('kind')}"
        ) from exc
    mutation = _require_mapping(
        action.get("mutation"),
        f"exit rule {rule_id}.action.mutation",
    )
    return ExitRule(
        rule_id=rule_id,
        priority=priority,
        repeat_policy=repeat_policy,
        required_fact_paths=required_paths,
        predicate=predicate,
        action_kind=action_kind,
        requested_mutation=FrozenPolicy.from_mapping(mutation),
    )


def _fact_path_present(path: str, observation: ExitObservation) -> bool:
    if path in {"event_kind", "symbol", "direction"}:
        return True
    if not path.startswith("fact."):
        raise ExitPlanContractError(
            f"P5 exit required fact path needs explicit future contract: {path}"
        )
    parts = path.removeprefix("fact.").split(".")
    value: object = observation.attributes.to_dict()
    for part in parts:
        if not part or not isinstance(value, Mapping) or part not in value:
            return False
        value = value[part]
    return value is not None


class UniversalExitEngine:
    """Pure shadow evaluator of exact StrategyPosition + exact ExitPlan.

    P5 creates decisions only. It cannot create ExitExecutionRequest, write
    runtime.trade_commands or mutate Exchange.
    """

    def evaluate(
        self,
        position: StrategyPosition,
        plan: ExitPlan,
        observation: ExitObservation,
        *,
        prior_once_rule_ids: frozenset[str] = frozenset(),
    ) -> ExitEvaluation:
        evaluation_id = _evaluation_id(position, plan, observation)
        if observation.strategy_position_id != position.strategy_position_id:
            return _blocked(position, plan, observation, "OBSERVATION_POSITION_MISMATCH")
        if observation.symbol != position.symbol:
            return _blocked(position, plan, observation, "OBSERVATION_SYMBOL_MISMATCH")
        if (
            plan.strategy_id,
            plan.strategy_version,
            plan.strategy_config_fingerprint,
            plan.exit_plan_fingerprint,
        ) != (
            position.strategy_id,
            position.strategy_version,
            position.strategy_config_fingerprint,
            position.exit_plan_fingerprint,
        ):
            return _blocked(position, plan, observation, "EXIT_PLAN_POSITION_LINEAGE_MISMATCH")

        policy = plan.exit_policy.to_dict()
        rules_raw = policy.get("rules")
        if rules_raw is None or rules_raw == []:
            return ExitEvaluation(
                evaluation_id=evaluation_id,
                observation=observation,
                status=ExitEvaluationStatus.NO_EXECUTABLE_EXIT_RULES,
                reason="ExitPlan contains no explicit executable rules",
            )
        if not isinstance(rules_raw, Sequence) or isinstance(rules_raw, (str, bytes)):
            return _blocked(position, plan, observation, "EXIT_RULES_MUST_BE_LIST")

        conflict_raw = policy.get("conflict_policy")
        if not isinstance(conflict_raw, Mapping):
            return _blocked(position, plan, observation, "EXIT_CONFLICT_POLICY_REQUIRED")
        try:
            conflict_mode = ExitConflictMode(str(conflict_raw.get("mode") or ""))
            priority_order = ExitPriorityOrder(str(conflict_raw.get("priority_order") or ""))
            tie_break = ExitTieBreak(str(conflict_raw.get("tie_break") or ""))
        except ValueError:
            return _blocked(position, plan, observation, "EXIT_CONFLICT_POLICY_UNSUPPORTED")
        if (
            conflict_mode is not ExitConflictMode.PRIORITY
            or tie_break is not ExitTieBreak.FAIL_CLOSED
        ):
            return _blocked(position, plan, observation, "EXIT_CONFLICT_POLICY_UNSUPPORTED")

        try:
            rules = tuple(_parse_rule(raw) for raw in rules_raw)
        except ExitPlanContractError as exc:
            return _blocked(position, plan, observation, f"EXIT_PLAN_CONTRACT:{exc}")
        rule_ids = tuple(rule.rule_id for rule in rules)
        if len(set(rule_ids)) != len(rule_ids):
            return _blocked(position, plan, observation, "DUPLICATE_EXIT_RULE_ID")

        fact = MarketFactEnvelope(
            fact_id=observation.observation_id,
            event_kind=observation.event_kind,
            symbol=observation.symbol,
            observed_at=observation.observed_at.astimezone(UTC),
            event_at=observation.event_at.astimezone(UTC),
            received_at=observation.received_at.astimezone(UTC),
            source_refs=observation.source_refs,
            attributes=observation.attributes,
            direction=position.direction,
        )

        matches: list[ExitRule] = []
        for rule in rules:
            if (
                rule.repeat_policy is ExitRepeatPolicy.ONCE_PER_POSITION
                and rule.rule_id in prior_once_rule_ids
            ):
                continue
            try:
                missing = tuple(
                    path
                    for path in rule.required_fact_paths
                    if not _fact_path_present(path, observation)
                )
            except ExitPlanContractError as exc:
                return _blocked(
                    position,
                    plan,
                    observation,
                    f"EXIT_PLAN_CONTRACT:{exc}",
                )
            if missing:
                return _blocked(
                    position,
                    plan,
                    observation,
                    f"MISSING_REQUIRED_EXIT_FACT:{','.join(missing)}",
                )
            try:
                matched = evaluate_stateless_predicate(rule.predicate, fact=fact)
            except UnsupportedOperatorError as exc:
                return _blocked(position, plan, observation, str(exc))
            if matched:
                matches.append(rule)

        if not matches:
            return ExitEvaluation(
                evaluation_id=evaluation_id,
                observation=observation,
                status=ExitEvaluationStatus.NO_MATCH,
                reason="no explicit ExitPlan rule matched",
            )

        priority_values = tuple(rule.priority for rule in matches)
        selected_priority = (
            max(priority_values)
            if priority_order is ExitPriorityOrder.HIGHER_WINS
            else min(priority_values)
        )
        winners = tuple(rule for rule in matches if rule.priority == selected_priority)
        matched_ids = tuple(rule.rule_id for rule in matches)
        if len(winners) != 1:
            return _blocked(
                position,
                plan,
                observation,
                "EXIT_RULE_PRIORITY_TIE_FAIL_CLOSED",
                matched_rule_ids=matched_ids,
            )
        selected = winners[0]
        decision_id = (
            "exit-decision-"
            + fingerprint(
                {
                    "strategy_position_id": position.strategy_position_id,
                    "exit_plan_fingerprint": plan.exit_plan_fingerprint,
                    "rule_id": selected.rule_id,
                    "observation_id": observation.observation_id,
                }
            )[:32]
        )
        decision = ExitDecision(
            exit_decision_id=decision_id,
            strategy_position_id=position.strategy_position_id,
            strategy_id=position.strategy_id,
            strategy_version=position.strategy_version,
            strategy_config_fingerprint=position.strategy_config_fingerprint,
            exit_plan_fingerprint=position.exit_plan_fingerprint,
            rule_id=selected.rule_id,
            action_kind=selected.action_kind,
            requested_mutation=selected.requested_mutation,
            source_refs=observation.source_refs,
            decided_at=observation.observed_at.astimezone(UTC),
        )
        return ExitEvaluation(
            evaluation_id=evaluation_id,
            observation=observation,
            status=ExitEvaluationStatus.DECISION_CREATED,
            reason=f"selected explicit ExitPlan rule {selected.rule_id}",
            matched_rule_ids=matched_ids,
            decision=decision,
            selected_priority=selected.priority,
            repeat_policy=selected.repeat_policy,
        )
