from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Protocol

from .contracts import (
    ContextLink,
    ContextMode,
    EntryEvaluation,
    EntryPlan,
    ExitPlan,
    FrozenPolicy,
    ObjectiveContext,
    SensorLink,
    StrategyActivation,
    StrategyCard,
)
from .fingerprint import canonical_json, fingerprint
from .shadow_runtime import ShadowParityObservation, ShadowRunIdentity


class CursorLike(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Sequence[object] | None: ...


class PostgresConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...

    def transaction(self) -> AbstractContextManager[object]: ...


def _json(value: object) -> str:
    return canonical_json(value)


def _quality(value: object | None) -> str | None:
    if value is None:
        return None
    rendered = getattr(value, "value", value)
    return str(rendered)


def _entry_plan_json(plan: EntryPlan) -> str:
    return canonical_json(
        {
            "strategy_id": plan.strategy_id,
            "strategy_version": plan.strategy_version,
            "strategy_config_fingerprint": plan.strategy_config_fingerprint,
            "entry_plan_version": plan.entry_plan_version,
            "entry_plan_fingerprint": plan.entry_plan_fingerprint,
            "symbols": plan.symbols,
            "directions": plan.directions,
            "predicate": plan.predicate,
            "watch_policy": plan.watch_policy,
            "touch_policy": plan.touch_policy,
            "sensor_policy": plan.sensor_policy,
            "context_policy": plan.context_policy,
            "capital_policy": plan.capital_policy,
            "lifecycle_policy": plan.lifecycle_policy,
            "post_signal_outcome_policy": plan.post_signal_outcome_policy,
        }
    )


def _exit_plan_json(plan: ExitPlan) -> str:
    return canonical_json(
        {
            "strategy_id": plan.strategy_id,
            "strategy_version": plan.strategy_version,
            "strategy_config_fingerprint": plan.strategy_config_fingerprint,
            "exit_plan_version": plan.exit_plan_version,
            "exit_plan_fingerprint": plan.exit_plan_fingerprint,
            "exit_policy": plan.exit_policy,
            "protection_policy": plan.protection_policy,
        }
    )


class StrategyEntryStore:
    """PostgreSQL persistence for immutable Strategy/Entry lifecycle facts.

    The store has no exchange transport and no strategy selection. It persists only
    exact IDs already created by Strategy/Entry and offers a narrow mutable operation
    for StrategyActivation state.
    """

    def __init__(self, connection: PostgresConnectionLike) -> None:
        self._connection = connection

    def insert_strategy_card(self, card: StrategyCard) -> None:
        self._connection.execute(
            """INSERT INTO strategy_entry.strategy_cards(
                   strategy_id,strategy_version,strategy_config_fingerprint,
                   name,description,card_json,approved_at,approved_source
               ) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
            (
                card.strategy_id,
                card.strategy_version,
                card.strategy_config_fingerprint,
                card.name,
                card.description,
                _json(card),
                card.approved_at,
                card.approved_source,
            ),
        )

    def insert_activation(self, activation: StrategyActivation, *, reason: str = "") -> None:
        disabled_at = activation.disabled_at
        if not activation.enabled and disabled_at is None:
            raise ValueError("disabled activation requires disabled_at")
        self._connection.execute(
            """INSERT INTO strategy_entry.strategy_activations(
                   activation_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,enabled,enabled_at,disabled_at,
                   scope,operator,source,change_reason
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)""",
            (
                activation.activation_id,
                activation.strategy_id,
                activation.strategy_version,
                activation.strategy_config_fingerprint,
                activation.enabled,
                activation.enabled_at,
                disabled_at,
                activation.scope.payload_json,
                activation.operator,
                activation.source,
                reason,
            ),
        )

    def set_activation_enabled(
        self,
        activation_id: str,
        *,
        enabled: bool,
        changed_at: datetime,
        operator: str,
        source: str,
        reason: str,
    ) -> None:
        cursor = self._connection.execute(
            """UPDATE strategy_entry.strategy_activations
               SET enabled=%s,
                   enabled_at=CASE WHEN %s THEN %s ELSE enabled_at END,
                   disabled_at=CASE WHEN %s THEN NULL ELSE %s END,
                   operator=%s,source=%s,change_reason=%s
               WHERE activation_id=%s""",
            (
                enabled,
                enabled,
                changed_at,
                enabled,
                changed_at,
                operator,
                source,
                reason,
                activation_id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"unknown StrategyActivation: {activation_id}")

    def insert_entry_plan(self, plan: EntryPlan) -> None:
        self._connection.execute(
            """INSERT INTO strategy_entry.entry_plans(
                   entry_plan_fingerprint,strategy_id,strategy_version,
                   strategy_config_fingerprint,entry_plan_version,plan_json
               ) VALUES(%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                plan.entry_plan_fingerprint,
                plan.strategy_id,
                plan.strategy_version,
                plan.strategy_config_fingerprint,
                plan.entry_plan_version,
                _entry_plan_json(plan),
            ),
        )

    def insert_exit_plan(self, plan: ExitPlan) -> None:
        self._connection.execute(
            """INSERT INTO strategy_entry.exit_plans(
                   exit_plan_fingerprint,strategy_id,strategy_version,
                   strategy_config_fingerprint,exit_plan_version,plan_json
               ) VALUES(%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                plan.exit_plan_fingerprint,
                plan.strategy_id,
                plan.strategy_version,
                plan.strategy_config_fingerprint,
                plan.exit_plan_version,
                _exit_plan_json(plan),
            ),
        )

    def record_evaluation(
        self,
        evaluation: EntryEvaluation,
        *,
        provenance: FrozenPolicy | None = None,
    ) -> None:
        evidence = provenance or FrozenPolicy.from_mapping({"source": "universal_entry"})
        with self._connection.transaction():
            self._insert_signal(evaluation)
            self._insert_attempt(evaluation)
            self._insert_decision(evaluation)
            for context_link in evaluation.context_links:
                self._insert_context_evidence(evaluation, context_link, evidence)
            for sensor_link in evaluation.sensor_links:
                self._insert_sensor_evidence(evaluation, sensor_link, evidence)
            if evaluation.execution_request is not None:
                request = evaluation.execution_request
                self._connection.execute(
                    """INSERT INTO strategy_entry.execution_requests(
                           execution_request_id,strategy_attempt_id,entry_decision_id,
                           signal_id,strategy_id,strategy_version,
                           strategy_config_fingerprint,entry_plan_fingerprint,
                           symbol,direction,requested_at,payload
                       ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                    (
                        request.execution_request_id,
                        request.strategy_attempt_id,
                        request.entry_decision_id,
                        request.signal_id,
                        request.strategy_id,
                        request.strategy_version,
                        request.strategy_config_fingerprint,
                        request.entry_plan_fingerprint,
                        request.symbol,
                        request.direction.value,
                        request.requested_at,
                        request.payload.payload_json,
                    ),
                )
            for notice in evaluation.notifications:
                self._connection.execute(
                    """INSERT INTO strategy_entry.notifications(
                           notification_id,strategy_attempt_id,signal_id,kind,
                           strategy_id,strategy_version,symbol,direction,occurred_at,
                           reason,requested_amount,available_amount,payload
                       ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                    (
                        notice.notification_id,
                        notice.strategy_attempt_id,
                        notice.signal_id,
                        notice.kind.value,
                        notice.strategy_id,
                        notice.strategy_version,
                        notice.symbol,
                        notice.direction.value,
                        notice.occurred_at,
                        notice.reason,
                        notice.requested_amount,
                        notice.available_amount,
                        _json(notice),
                    ),
                )

    def record_observed_context(
        self,
        *,
        signal_id: str,
        requirement_id: str,
        context: ObjectiveContext,
        linked_at: datetime,
        age_seconds: float,
        status: str,
        provenance: FrozenPolicy,
    ) -> str:
        link_id = self._context_link_id(
            signal_id=signal_id,
            strategy_attempt_id=None,
            link_type="OBSERVED_CONTEXT",
            requirement_id=requirement_id,
            context_id=context.context_id,
        )
        self._connection.execute(
            """INSERT INTO strategy_entry.context_links(
                   context_link_id,signal_id,strategy_attempt_id,link_type,
                   requirement_id,context_type,context_id,plan_mode,
                   context_observed_at,linked_at,age_seconds,quality,status,
                   source_refs,provenance
               ) VALUES(%s,%s,NULL,'OBSERVED_CONTEXT',%s,%s,%s,NULL,%s,%s,%s,%s,%s,
                        %s::jsonb,%s::jsonb)""",
            (
                link_id,
                signal_id,
                requirement_id,
                context.context_type,
                context.context_id,
                context.observed_at,
                linked_at,
                age_seconds,
                context.quality.value,
                status,
                _json(context.source_refs),
                provenance.payload_json,
            ),
        )
        return link_id

    def _insert_signal(self, evaluation: EntryEvaluation) -> None:
        signal = evaluation.signal
        self._connection.execute(
            """INSERT INTO strategy_entry.strategy_signals(
                   signal_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,entry_plan_fingerprint,
                   strategy_activation_id,symbol,direction,detected_at,
                   fact_id,source_refs,payload
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)""",
            (
                signal.signal_id,
                signal.strategy_id,
                signal.strategy_version,
                signal.strategy_config_fingerprint,
                signal.entry_plan_fingerprint,
                signal.strategy_activation_id,
                signal.symbol,
                signal.direction.value,
                signal.detected_at,
                signal.fact_id,
                _json(signal.source_refs),
                _json(signal),
            ),
        )

    def _insert_attempt(self, evaluation: EntryEvaluation) -> None:
        attempt = evaluation.attempt
        self._connection.execute(
            """INSERT INTO strategy_entry.strategy_attempts(
                   strategy_attempt_id,signal_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,entry_plan_fingerprint,
                   strategy_activation_id,created_at,payload
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                attempt.strategy_attempt_id,
                attempt.signal_id,
                attempt.strategy_id,
                attempt.strategy_version,
                attempt.strategy_config_fingerprint,
                attempt.entry_plan_fingerprint,
                attempt.strategy_activation_id,
                attempt.created_at,
                _json(attempt),
            ),
        )

    def _insert_decision(self, evaluation: EntryEvaluation) -> None:
        decision = evaluation.decision
        self._connection.execute(
            """INSERT INTO strategy_entry.entry_decisions(
                   entry_decision_id,strategy_attempt_id,signal_id,decision_code,
                   reason,decided_at,capacity_snapshot_id,payload
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                decision.entry_decision_id,
                decision.strategy_attempt_id,
                decision.signal_id,
                decision.code.value,
                decision.reason,
                decision.decided_at,
                decision.capacity_snapshot_id,
                _json(decision),
            ),
        )

    def _insert_context_evidence(
        self,
        evaluation: EntryEvaluation,
        link: ContextLink,
        provenance: FrozenPolicy,
    ) -> None:
        observed_id = self._context_link_id(
            signal_id=evaluation.signal.signal_id,
            strategy_attempt_id=None,
            link_type="OBSERVED_CONTEXT",
            requirement_id=link.requirement_id,
            context_id=link.context_id,
        )
        self._connection.execute(
            """INSERT INTO strategy_entry.context_links(
                   context_link_id,signal_id,strategy_attempt_id,link_type,
                   requirement_id,context_type,context_id,plan_mode,
                   context_observed_at,linked_at,age_seconds,quality,status,
                   source_refs,provenance
               ) VALUES(%s,%s,NULL,'OBSERVED_CONTEXT',%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s::jsonb,%s::jsonb)""",
            (
                observed_id,
                evaluation.signal.signal_id,
                link.requirement_id,
                link.context_type,
                link.context_id,
                None if link.mode is ContextMode.OFF else link.mode.value,
                link.context_observed_at,
                evaluation.decision.decided_at,
                link.age_seconds,
                _quality(link.quality),
                link.status,
                _json(link.source_refs),
                provenance.payload_json,
            ),
        )
        if not link.consumed or link.context_id is None or link.context_observed_at is None:
            return
        consumed_id = self._context_link_id(
            signal_id=evaluation.signal.signal_id,
            strategy_attempt_id=evaluation.attempt.strategy_attempt_id,
            link_type="CONSUMED_CONTEXT",
            requirement_id=link.requirement_id,
            context_id=link.context_id,
        )
        self._connection.execute(
            """INSERT INTO strategy_entry.context_links(
                   context_link_id,signal_id,strategy_attempt_id,link_type,
                   requirement_id,context_type,context_id,plan_mode,
                   context_observed_at,linked_at,age_seconds,quality,status,
                   source_refs,provenance
               ) VALUES(%s,%s,%s,'CONSUMED_CONTEXT',%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s::jsonb,%s::jsonb)""",
            (
                consumed_id,
                evaluation.signal.signal_id,
                evaluation.attempt.strategy_attempt_id,
                link.requirement_id,
                link.context_type,
                link.context_id,
                link.mode.value,
                link.context_observed_at,
                evaluation.decision.decided_at,
                link.age_seconds,
                _quality(link.quality),
                link.status,
                _json(link.source_refs),
                provenance.payload_json,
            ),
        )

    def _insert_sensor_evidence(
        self,
        evaluation: EntryEvaluation,
        link: SensorLink,
        provenance: FrozenPolicy,
    ) -> None:
        observed_id = self._sensor_link_id(
            signal_id=evaluation.signal.signal_id,
            strategy_attempt_id=None,
            link_type="OBSERVED_SENSOR",
            sensor_id=link.sensor_id,
        )
        self._connection.execute(
            """INSERT INTO strategy_entry.sensor_links(
                   sensor_link_id,signal_id,strategy_attempt_id,link_type,sensor_id,
                   sensor_observed_at,linked_at,age_seconds,quality,status,
                   source_refs,provenance
               ) VALUES(%s,%s,NULL,'OBSERVED_SENSOR',%s,%s,%s,%s,%s,%s,
                        %s::jsonb,%s::jsonb)""",
            (
                observed_id,
                evaluation.signal.signal_id,
                link.sensor_id,
                link.observation_observed_at,
                evaluation.decision.decided_at,
                link.age_seconds,
                _quality(link.quality),
                link.status,
                _json(link.source_refs),
                provenance.payload_json,
            ),
        )
        if not link.consumed or link.observation_observed_at is None:
            return
        consumed_id = self._sensor_link_id(
            signal_id=evaluation.signal.signal_id,
            strategy_attempt_id=evaluation.attempt.strategy_attempt_id,
            link_type="CONSUMED_SENSOR",
            sensor_id=link.sensor_id,
        )
        self._connection.execute(
            """INSERT INTO strategy_entry.sensor_links(
                   sensor_link_id,signal_id,strategy_attempt_id,link_type,sensor_id,
                   sensor_observed_at,linked_at,age_seconds,quality,status,
                   source_refs,provenance
               ) VALUES(%s,%s,%s,'CONSUMED_SENSOR',%s,%s,%s,%s,%s,%s,
                        %s::jsonb,%s::jsonb)""",
            (
                consumed_id,
                evaluation.signal.signal_id,
                evaluation.attempt.strategy_attempt_id,
                link.sensor_id,
                link.observation_observed_at,
                evaluation.decision.decided_at,
                link.age_seconds,
                _quality(link.quality),
                link.status,
                _json(link.source_refs),
                provenance.payload_json,
            ),
        )

    @staticmethod
    def _context_link_id(
        *,
        signal_id: str,
        strategy_attempt_id: str | None,
        link_type: str,
        requirement_id: str,
        context_id: str | None,
    ) -> str:
        return (
            "ctx-"
            + fingerprint(
                {
                    "signal_id": signal_id,
                    "strategy_attempt_id": strategy_attempt_id,
                    "link_type": link_type,
                    "requirement_id": requirement_id,
                    "context_id": context_id,
                }
            )[:32]
        )

    @staticmethod
    def _sensor_link_id(
        *,
        signal_id: str,
        strategy_attempt_id: str | None,
        link_type: str,
        sensor_id: str,
    ) -> str:
        return (
            "sensor-"
            + fingerprint(
                {
                    "signal_id": signal_id,
                    "strategy_attempt_id": strategy_attempt_id,
                    "link_type": link_type,
                    "sensor_id": sensor_id,
                }
            )[:32]
        )


class ShadowParityStore:
    """PostgreSQL audit store for U5 online parity only; it has no trading tables."""

    def __init__(self, connection: PostgresConnectionLike) -> None:
        self._connection = connection

    def ensure_policy_identity(self, card: StrategyCard, plan: EntryPlan) -> None:
        self._connection.execute(
            """INSERT INTO strategy_entry.strategy_cards(
                   strategy_id,strategy_version,strategy_config_fingerprint,
                   name,description,card_json,approved_at,approved_source
               ) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
               ON CONFLICT DO NOTHING""",
            (
                card.strategy_id,
                card.strategy_version,
                card.strategy_config_fingerprint,
                card.name,
                card.description,
                _json(card),
                card.approved_at,
                card.approved_source,
            ),
        )
        self._connection.execute(
            """INSERT INTO strategy_entry.entry_plans(
                   entry_plan_fingerprint,strategy_id,strategy_version,
                   strategy_config_fingerprint,entry_plan_version,plan_json
               ) VALUES(%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT DO NOTHING""",
            (
                plan.entry_plan_fingerprint,
                plan.strategy_id,
                plan.strategy_version,
                plan.strategy_config_fingerprint,
                plan.entry_plan_version,
                _entry_plan_json(plan),
            ),
        )

    def finalize_unfinished_run_for_restart(
        self,
        *,
        strategy_config_fingerprint: str,
        entry_plan_fingerprint: str,
        fact_source_id: str,
        occurred_at: datetime,
    ) -> str | None:
        row = self._connection.execute(
            """SELECT parity_run_id,strategy_id,strategy_version,
                      strategy_config_fingerprint,entry_plan_fingerprint,
                      calibration_sha256,calibration_size,baseline_source_commit,
                      universal_source_commit,fact_source_id,service_instance_id,started_at
               FROM strategy_entry.shadow_parity_runs
               WHERE status IN ('WARMUP','PARITY_COMPARABLE')
                 AND strategy_config_fingerprint=%s
                 AND entry_plan_fingerprint=%s
                 AND fact_source_id=%s
               ORDER BY started_at DESC
               LIMIT 1""",
            (strategy_config_fingerprint, entry_plan_fingerprint, fact_source_id),
        ).fetchone()
        if row is None:
            return None
        identity = ShadowRunIdentity(
            parity_run_id=str(row[0]),
            strategy_id=str(row[1]),
            strategy_version=str(row[2]),
            strategy_config_fingerprint=str(row[3]),
            entry_plan_fingerprint=str(row[4]),
            calibration_sha256=str(row[5]),
            calibration_size=int(str(row[6])),
            baseline_source_commit=str(row[7]),
            universal_source_commit=str(row[8]),
            fact_source_id=str(row[9]),
            service_instance_id=str(row[10]),
            started_at=row[11]
            if isinstance(row[11], datetime)
            else datetime.fromisoformat(str(row[11])),
        )
        self.transition_run(
            identity,
            status="NOT_COMPARABLE",
            occurred_at=occurred_at,
            summary={
                "trading_effect": "NONE",
                "state": "NOT_COMPARABLE",
                "reason": (
                    "service restart detected unfinished parity run; "
                    "continuity gap is not replay-safe"
                ),
            },
        )
        return identity.parity_run_id

    def start_run(self, identity: ShadowRunIdentity, *, summary: dict[str, object]) -> None:
        with self._connection.transaction():
            self._connection.execute(
                """INSERT INTO strategy_entry.shadow_parity_runs(
                   parity_run_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,entry_plan_fingerprint,
                   calibration_sha256,calibration_size,baseline_source_commit,
                   universal_source_commit,fact_source_id,service_instance_id,
                   status,started_at,finished_at,summary
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'WARMUP',%s,NULL,%s::jsonb)""",
                (
                    identity.parity_run_id,
                    identity.strategy_id,
                    identity.strategy_version,
                    identity.strategy_config_fingerprint,
                    identity.entry_plan_fingerprint,
                    identity.calibration_sha256,
                    identity.calibration_size,
                    identity.baseline_source_commit,
                    identity.universal_source_commit,
                    identity.fact_source_id,
                    identity.service_instance_id,
                    identity.started_at,
                    canonical_json(summary),
                ),
            )
            self._insert_status_event(identity, "WARMUP", identity.started_at, summary)

    def update_summary(self, identity: ShadowRunIdentity, summary: dict[str, object]) -> None:
        self._connection.execute(
            """UPDATE strategy_entry.shadow_parity_runs
               SET summary=%s::jsonb
               WHERE parity_run_id=%s""",
            (canonical_json(summary), identity.parity_run_id),
        )

    def transition_run(
        self,
        identity: ShadowRunIdentity,
        *,
        status: str,
        occurred_at: datetime,
        summary: dict[str, object],
    ) -> None:
        finished = occurred_at if status in {"PASS", "FAIL", "NOT_COMPARABLE"} else None
        with self._connection.transaction():
            self._connection.execute(
                """UPDATE strategy_entry.shadow_parity_runs
                   SET status=%s,finished_at=%s,summary=%s::jsonb
                   WHERE parity_run_id=%s""",
                (status, finished, canonical_json(summary), identity.parity_run_id),
            )
            self._insert_status_event(identity, status, occurred_at, summary)

    def record_observation(self, observation: ShadowParityObservation) -> None:
        event_id = (
            "spe-"
            + fingerprint(
                {
                    "run": observation.parity_run_id,
                    "causal_key": observation.causal_key,
                    "category": observation.category,
                }
            )[:32]
        )
        self._connection.execute(
            """INSERT INTO strategy_entry.shadow_parity_events(
                   parity_event_id,parity_run_id,causal_key,event_at,category,
                   observed_at,source_refs,strategy_config_fingerprint,
                   entry_plan_fingerprint,legacy_payload,universal_payload,
                   equivalent,difference
               ) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb)
               ON CONFLICT(parity_run_id,causal_key) DO NOTHING""",
            (
                event_id,
                observation.parity_run_id,
                observation.causal_key,
                observation.observed_at,
                observation.category,
                observation.observed_at,
                canonical_json(observation.source_refs),
                observation.strategy_config_fingerprint,
                observation.entry_plan_fingerprint,
                observation.legacy_payload.payload_json,
                observation.universal_payload.payload_json,
                observation.equivalent,
                observation.difference.payload_json,
            ),
        )

    def _insert_status_event(
        self,
        identity: ShadowRunIdentity,
        status: str,
        occurred_at: datetime,
        summary: dict[str, object],
    ) -> None:
        causal_key = f"STATUS:{status}:{occurred_at.astimezone(UTC).isoformat()}"
        event_id = (
            "spe-" + fingerprint({"run": identity.parity_run_id, "causal_key": causal_key})[:32]
        )
        payload = canonical_json({"status": status, "summary": summary})
        self._connection.execute(
            """INSERT INTO strategy_entry.shadow_parity_events(
                   parity_event_id,parity_run_id,causal_key,event_at,category,
                   observed_at,source_refs,strategy_config_fingerprint,
                   entry_plan_fingerprint,legacy_payload,universal_payload,
                   equivalent,difference
               ) VALUES(
                     %s,%s,%s,%s,'STATUS_TRANSITION',%s,'[]'::jsonb,%s,%s,
                     %s::jsonb,%s::jsonb,true,'{}'::jsonb
               )""",
            (
                event_id,
                identity.parity_run_id,
                causal_key,
                occurred_at,
                occurred_at,
                identity.strategy_config_fingerprint,
                identity.entry_plan_fingerprint,
                payload,
                payload,
            ),
        )
