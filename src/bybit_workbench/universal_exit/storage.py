from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import ExitPlan
from bybit_workbench.universal_entry.fingerprint import canonical_json

from .contracts import ExitEvaluation


class CursorLike(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Mapping[str, object] | None: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...

    def transaction(self) -> AbstractContextManager[object]: ...


class PostgresExitShadowStore:
    """Durable P5 shadow evidence. No ExecutionRequest or Exchange writes."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def record(
        self,
        evaluation: ExitEvaluation,
        *,
        position: StrategyPosition,
        plan: ExitPlan,
    ) -> None:
        observation = evaluation.observation
        if observation.strategy_position_id != position.strategy_position_id:
            raise ValueError("ExitEvaluation/StrategyPosition mismatch")
        if plan.exit_plan_fingerprint != position.exit_plan_fingerprint:
            raise ValueError("ExitEvaluation/ExitPlan mismatch")

        with self._connection.transaction():
            self._connection.execute(
                """INSERT INTO strategy_exit.exit_observations(
                       observation_id,strategy_position_id,exit_plan_fingerprint,
                       symbol,event_kind,event_at,observed_at,received_at,
                       attributes,source_refs
                   ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                   ON CONFLICT(observation_id) DO NOTHING""",
                (
                    observation.observation_id,
                    position.strategy_position_id,
                    plan.exit_plan_fingerprint,
                    observation.symbol,
                    observation.event_kind,
                    observation.event_at,
                    observation.observed_at,
                    observation.received_at,
                    observation.attributes.payload_json,
                    canonical_json(observation.source_refs),
                ),
            )
            self._assert_observation_identity(evaluation, position, plan)

            if evaluation.decision is not None:
                decision = evaluation.decision
                assert evaluation.selected_priority is not None
                assert evaluation.repeat_policy is not None
                self._connection.execute(
                    """INSERT INTO strategy_exit.exit_decisions(
                           exit_decision_id,strategy_position_id,strategy_id,
                           strategy_version,strategy_config_fingerprint,
                           exit_plan_fingerprint,observation_id,rule_id,
                           rule_priority,repeat_policy,action_kind,
                           requested_mutation,source_refs,decided_at
                       ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                       ON CONFLICT(exit_decision_id) DO NOTHING""",
                    (
                        decision.exit_decision_id,
                        decision.strategy_position_id,
                        decision.strategy_id,
                        decision.strategy_version,
                        decision.strategy_config_fingerprint,
                        decision.exit_plan_fingerprint,
                        observation.observation_id,
                        decision.rule_id,
                        evaluation.selected_priority,
                        evaluation.repeat_policy.value,
                        decision.action_kind.value,
                        decision.requested_mutation.payload_json,
                        canonical_json(decision.source_refs),
                        decision.decided_at,
                    ),
                )
                self._assert_decision_identity(evaluation)

            self._connection.execute(
                """INSERT INTO strategy_exit.shadow_evaluations(
                       evaluation_id,observation_id,strategy_position_id,
                       exit_plan_fingerprint,status,reason,matched_rule_ids,
                       exit_decision_id
                   ) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT(evaluation_id) DO NOTHING""",
                (
                    evaluation.evaluation_id,
                    observation.observation_id,
                    position.strategy_position_id,
                    plan.exit_plan_fingerprint,
                    evaluation.status.value,
                    evaluation.reason,
                    canonical_json(evaluation.matched_rule_ids),
                    None if evaluation.decision is None else evaluation.decision.exit_decision_id,
                ),
            )
            self._assert_evaluation_identity(evaluation, position, plan)

    def _assert_observation_identity(
        self,
        evaluation: ExitEvaluation,
        position: StrategyPosition,
        plan: ExitPlan,
    ) -> None:
        row = self._connection.execute(
            """SELECT strategy_position_id,exit_plan_fingerprint,symbol,event_kind,
                      event_at,observed_at,received_at,attributes,source_refs
                 FROM strategy_exit.exit_observations
                WHERE observation_id=%s""",
            (evaluation.observation.observation_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Exit observation persistence disappeared")
        expected = (
            position.strategy_position_id,
            plan.exit_plan_fingerprint,
            evaluation.observation.symbol,
            evaluation.observation.event_kind,
            evaluation.observation.event_at,
            evaluation.observation.observed_at,
            evaluation.observation.received_at,
            evaluation.observation.attributes.to_dict(),
            list(evaluation.observation.source_refs),
        )
        actual = (
            str(row["strategy_position_id"]),
            str(row["exit_plan_fingerprint"]),
            str(row["symbol"]),
            str(row["event_kind"]),
            row["event_at"],
            row["observed_at"],
            row["received_at"],
            row["attributes"],
            row["source_refs"],
        )
        if actual != expected:
            raise RuntimeError("exit observation identity collision")

    def _assert_decision_identity(self, evaluation: ExitEvaluation) -> None:
        decision = evaluation.decision
        assert decision is not None
        row = self._connection.execute(
            """SELECT strategy_position_id,exit_plan_fingerprint,observation_id,
                      rule_id,rule_priority,repeat_policy,action_kind,
                      requested_mutation,source_refs,decided_at
                 FROM strategy_exit.exit_decisions
                WHERE exit_decision_id=%s""",
            (decision.exit_decision_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("ExitDecision persistence disappeared")
        expected = (
            decision.strategy_position_id,
            decision.exit_plan_fingerprint,
            evaluation.observation.observation_id,
            decision.rule_id,
            evaluation.selected_priority,
            evaluation.repeat_policy.value if evaluation.repeat_policy is not None else None,
            decision.action_kind.value,
            decision.requested_mutation.to_dict(),
            list(decision.source_refs),
            decision.decided_at,
        )
        try:
            rule_priority = int(str(row["rule_priority"]))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("stored ExitDecision rule_priority is invalid") from exc
        actual: tuple[object, ...] = (
            str(row["strategy_position_id"]),
            str(row["exit_plan_fingerprint"]),
            str(row["observation_id"]),
            str(row["rule_id"]),
            rule_priority,
            str(row["repeat_policy"]),
            str(row["action_kind"]),
            row["requested_mutation"],
            row["source_refs"],
            row["decided_at"],
        )
        if actual != expected:
            raise RuntimeError("ExitDecision identity collision")

    def _assert_evaluation_identity(
        self,
        evaluation: ExitEvaluation,
        position: StrategyPosition,
        plan: ExitPlan,
    ) -> None:
        row = self._connection.execute(
            """SELECT observation_id,strategy_position_id,exit_plan_fingerprint,
                      status,reason,matched_rule_ids,exit_decision_id
                 FROM strategy_exit.shadow_evaluations
                WHERE evaluation_id=%s""",
            (evaluation.evaluation_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Exit shadow evaluation persistence disappeared")
        expected_decision = (
            None if evaluation.decision is None else evaluation.decision.exit_decision_id
        )
        matched_rule_ids = row["matched_rule_ids"]
        if not isinstance(matched_rule_ids, list):
            raise RuntimeError("stored ExitEvaluation matched_rule_ids is invalid")
        actual: tuple[object, ...] = (
            str(row["observation_id"]),
            str(row["strategy_position_id"]),
            str(row["exit_plan_fingerprint"]),
            str(row["status"]),
            str(row["reason"]),
            tuple(str(value) for value in matched_rule_ids),
            None if row["exit_decision_id"] is None else str(row["exit_decision_id"]),
        )
        expected = (
            evaluation.observation.observation_id,
            position.strategy_position_id,
            plan.exit_plan_fingerprint,
            evaluation.status.value,
            evaluation.reason,
            evaluation.matched_rule_ids,
            expected_decision,
        )
        if actual != expected:
            raise RuntimeError("Exit shadow evaluation identity collision")
