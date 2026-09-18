from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from bybit_workbench.universal_entry.fingerprint import canonical_json, fingerprint


class CursorLike(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Mapping[str, object] | None: ...
    def fetchall(self) -> Sequence[Mapping[str, object]]: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...

    def transaction(self) -> AbstractContextManager[object]: ...


class FaultSeverity(StrEnum):
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class LifecycleSupervisorPolicy:
    exchange_state_max_age_seconds: int | None = None
    exit_owner_max_age_seconds: int | None = None

    def __post_init__(self) -> None:
        if (
            self.exchange_state_max_age_seconds is not None
            and self.exchange_state_max_age_seconds <= 0
        ):
            raise ValueError("exchange_state_max_age_seconds must be positive or None")
        if self.exit_owner_max_age_seconds is not None and self.exit_owner_max_age_seconds <= 0:
            raise ValueError("exit_owner_max_age_seconds must be positive or None")


@dataclass(frozen=True, slots=True)
class LifecycleScanResult:
    projected_events: int
    open_faults: int
    resolved_faults: int
    active_fault_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FaultCondition:
    code: str
    severity: FaultSeverity
    scope_key: str
    strategy_position_id: str | None
    exact_ids: Mapping[str, object]
    payload: Mapping[str, object]


def _as_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError(f"{label} must be datetime")
    if value.tzinfo is None:
        raise RuntimeError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


_MANAGED_FAULT_CODES = frozenset(
    {
        "PLAN_PAIR_INCOMPLETE",
        "ENTRY_PLAN_NOT_CONSUMED",
        "ENTRY_REQUEST_NOT_DISPATCHED",
        "ENTRY_EXECUTION_AMBIGUOUS",
        "POSITION_LINEAGE_INCOMPLETE",
        "POSITION_WITHOUT_EXIT_PLAN",
        "POSITION_WITHOUT_EXIT_OWNER",
        "EXIT_REQUEST_NOT_DISPATCHED",
        "EXIT_EXECUTION_AMBIGUOUS",
        "EXCHANGE_POSITION_OWNERSHIP_CONFLICT",
        "EXCHANGE_STATE_DIVERGED",
        "CAPITAL_RESERVATION_STUCK",
    }
)


class LifecycleSupervisor:
    """Read/control-plane lifecycle auditor with no trading mutation rights."""

    def __init__(
        self,
        connection: ConnectionLike,
        *,
        policy: LifecycleSupervisorPolicy | None = None,
    ) -> None:
        self._connection = connection
        self._policy = policy or LifecycleSupervisorPolicy()

    def scan(self, *, now: datetime) -> LifecycleScanResult:
        current = now.astimezone(UTC)
        with self._connection.transaction():
            events = self._project_events(current)
            active = self._detect_faults(current)
            opened = 0
            for fault in active:
                opened += self._upsert_fault(fault, current)
            resolved = self._resolve_cleared_faults(active, current)
        return LifecycleScanResult(
            projected_events=events,
            open_faults=opened,
            resolved_faults=resolved,
            active_fault_codes=tuple(sorted({item.code for item in active})),
        )

    def _insert_event(
        self,
        event_type: str,
        occurred_at: datetime,
        exact_ids: Mapping[str, object],
        *,
        payload: Mapping[str, object] | None = None,
        strategy_id: str | None = None,
        strategy_version: str | None = None,
        strategy_config_fingerprint: str | None = None,
        strategy_activation_id: str | None = None,
        signal_id: str | None = None,
        strategy_attempt_id: str | None = None,
        entry_decision_id: str | None = None,
        entry_execution_request_id: str | None = None,
        strategy_position_id: str | None = None,
        exit_plan_fingerprint: str | None = None,
        exit_decision_id: str | None = None,
        exit_execution_request_id: str | None = None,
    ) -> int:
        event_id = (
            "lifecycle-"
            + fingerprint({"event_type": event_type, "exact_ids": dict(exact_ids)})[:32]
        )
        inserted = self._connection.execute(
            """INSERT INTO runtime.trade_lifecycle_events(
                   lifecycle_event_id,event_type,occurred_at,
                   strategy_id,strategy_version,strategy_config_fingerprint,
                   strategy_activation_id,signal_id,strategy_attempt_id,
                   entry_decision_id,entry_execution_request_id,
                   strategy_position_id,exit_plan_fingerprint,exit_decision_id,
                   exit_execution_request_id,exact_ids,payload,provenance
               ) VALUES(
                   %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                   %s::jsonb,%s::jsonb,%s::jsonb
               )
               ON CONFLICT(lifecycle_event_id) DO NOTHING""",
            (
                event_id,
                event_type,
                occurred_at.astimezone(UTC),
                strategy_id,
                strategy_version,
                strategy_config_fingerprint,
                strategy_activation_id,
                signal_id,
                strategy_attempt_id,
                entry_decision_id,
                entry_execution_request_id,
                strategy_position_id,
                exit_plan_fingerprint,
                exit_decision_id,
                exit_execution_request_id,
                canonical_json(dict(exact_ids)),
                canonical_json(dict(payload or {})),
                canonical_json({"source": "lifecycle_supervisor_projection_v1"}),
            ),
        )
        return 1 if inserted.rowcount == 1 else 0

    def _project_events(self, now: datetime) -> int:
        total = 0

        activations = self._connection.execute(
            """SELECT activation_id,strategy_id,strategy_version,
                      strategy_config_fingerprint,enabled_at
                 FROM strategy_entry.strategy_activations"""
        ).fetchall()
        for row in activations:
            exact = {
                "strategy_activation_id": row["activation_id"],
                "strategy_id": row["strategy_id"],
                "strategy_version": row["strategy_version"],
                "strategy_config_fingerprint": row["strategy_config_fingerprint"],
            }
            total += self._insert_event(
                "STRATEGY_ACTIVATED",
                _as_datetime(row["enabled_at"], "StrategyActivation.enabled_at"),
                exact,
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                strategy_activation_id=str(row["activation_id"]),
            )

        pairs = self._connection.execute(
            """SELECT a.activation_id,a.strategy_id,a.strategy_version,
                      a.strategy_config_fingerprint,a.enabled_at,
                      e.entry_plan_fingerprint,e.created_at AS entry_created_at,
                      x.exit_plan_fingerprint,x.created_at AS exit_created_at
                 FROM strategy_entry.strategy_activations a
                 JOIN strategy_entry.entry_plans e
                   ON e.strategy_id=a.strategy_id
                  AND e.strategy_version=a.strategy_version
                  AND e.strategy_config_fingerprint=a.strategy_config_fingerprint
                 JOIN strategy_entry.exit_plans x
                   ON x.strategy_id=a.strategy_id
                  AND x.strategy_version=a.strategy_version
                  AND x.strategy_config_fingerprint=a.strategy_config_fingerprint"""
        ).fetchall()
        for row in pairs:
            occurred = max(
                _as_datetime(row["enabled_at"], "StrategyActivation.enabled_at"),
                _as_datetime(row["entry_created_at"], "EntryPlan.created_at"),
                _as_datetime(row["exit_created_at"], "ExitPlan.created_at"),
            )
            exact = {
                "strategy_activation_id": row["activation_id"],
                "entry_plan_fingerprint": row["entry_plan_fingerprint"],
                "exit_plan_fingerprint": row["exit_plan_fingerprint"],
            }
            for event_type in ("PLANS_MATERIALIZED", "PLANS_PUBLISHED"):
                total += self._insert_event(
                    event_type,
                    occurred,
                    exact,
                    strategy_id=str(row["strategy_id"]),
                    strategy_version=str(row["strategy_version"]),
                    strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                    strategy_activation_id=str(row["activation_id"]),
                    exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
                )

        consumptions = self._connection.execute(
            """SELECT plan_consumption_id,entry_plan_fingerprint,
                      strategy_activation_id,consumer_instance_id,loaded_at,status
                 FROM runtime.plan_consumptions
                WHERE plan_kind='ENTRY' AND consumer_kind='ENTRY_ENGINE'
                  AND status='LOADED'"""
        ).fetchall()
        for row in consumptions:
            total += self._insert_event(
                "ENTRY_PLAN_CONSUMED",
                _as_datetime(row["loaded_at"], "PlanConsumption.loaded_at"),
                {
                    "plan_consumption_id": row["plan_consumption_id"],
                    "entry_plan_fingerprint": row["entry_plan_fingerprint"],
                    "consumer_instance_id": row["consumer_instance_id"],
                },
                strategy_activation_id=str(row["strategy_activation_id"]),
            )

        signals = self._connection.execute(
            """SELECT signal_id,strategy_id,strategy_version,
                      strategy_config_fingerprint,strategy_activation_id,
                      detected_at,entry_plan_fingerprint
                 FROM strategy_entry.strategy_signals"""
        ).fetchall()
        for row in signals:
            total += self._insert_event(
                "STRATEGY_SIGNAL_CREATED",
                _as_datetime(row["detected_at"], "StrategySignal.detected_at"),
                {
                    "signal_id": row["signal_id"],
                    "entry_plan_fingerprint": row["entry_plan_fingerprint"],
                },
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                strategy_activation_id=str(row["strategy_activation_id"]),
                signal_id=str(row["signal_id"]),
            )

        attempts = self._connection.execute(
            """SELECT strategy_attempt_id,signal_id,strategy_id,strategy_version,
                      strategy_config_fingerprint,strategy_activation_id,created_at
                 FROM strategy_entry.strategy_attempts"""
        ).fetchall()
        for row in attempts:
            total += self._insert_event(
                "ENTRY_ATTEMPT_CREATED",
                _as_datetime(row["created_at"], "lifecycle source created_at"),
                {"strategy_attempt_id": row["strategy_attempt_id"]},
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                strategy_activation_id=str(row["strategy_activation_id"]),
                signal_id=str(row["signal_id"]),
                strategy_attempt_id=str(row["strategy_attempt_id"]),
            )

        reservations = self._connection.execute(
            """SELECT reservation_id,strategy_attempt_id,signal_id,strategy_id,
                      strategy_version,strategy_config_fingerprint,created_at
                 FROM runtime.capital_reservations"""
        ).fetchall()
        for row in reservations:
            total += self._insert_event(
                "CAPITAL_RESERVED",
                _as_datetime(row["created_at"], "lifecycle source created_at"),
                {"reservation_id": row["reservation_id"]},
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                signal_id=str(row["signal_id"]),
                strategy_attempt_id=str(row["strategy_attempt_id"]),
            )

        decisions = self._connection.execute(
            """SELECT d.entry_decision_id,d.strategy_attempt_id,d.signal_id,
                      d.decided_at,a.strategy_id,a.strategy_version,
                      a.strategy_config_fingerprint
                 FROM strategy_entry.entry_decisions d
                 JOIN strategy_entry.strategy_attempts a
                   ON a.strategy_attempt_id=d.strategy_attempt_id"""
        ).fetchall()
        for row in decisions:
            total += self._insert_event(
                "ENTRY_DECIDED",
                _as_datetime(row["decided_at"], "decision.decided_at"),
                {"entry_decision_id": row["entry_decision_id"]},
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                signal_id=str(row["signal_id"]),
                strategy_attempt_id=str(row["strategy_attempt_id"]),
                entry_decision_id=str(row["entry_decision_id"]),
            )

        requests = self._connection.execute(
            """SELECT execution_request_id,strategy_attempt_id,entry_decision_id,
                      signal_id,strategy_id,strategy_version,
                      strategy_config_fingerprint,exit_plan_fingerprint,requested_at
                 FROM strategy_entry.execution_requests"""
        ).fetchall()
        for row in requests:
            total += self._insert_event(
                "ENTRY_REQUEST_CREATED",
                _as_datetime(row["requested_at"], "ExecutionRequest.requested_at"),
                {"entry_execution_request_id": row["execution_request_id"]},
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                signal_id=str(row["signal_id"]),
                strategy_attempt_id=str(row["strategy_attempt_id"]),
                entry_decision_id=str(row["entry_decision_id"]),
                entry_execution_request_id=str(row["execution_request_id"]),
                exit_plan_fingerprint=(
                    None
                    if row["exit_plan_fingerprint"] is None
                    else str(row["exit_plan_fingerprint"])
                ),
            )

        dispatches = self._connection.execute(
            """SELECT d.execution_request_id,d.command_id,d.created_at,
                      d.strategy_attempt_id,d.entry_decision_id,d.signal_id,
                      d.strategy_id,d.strategy_version,
                      d.strategy_config_fingerprint,d.exit_plan_fingerprint
                 FROM strategy_entry.execution_dispatches d
                WHERE d.state='DISPATCHED'"""
        ).fetchall()
        for row in dispatches:
            total += self._insert_event(
                "ENTRY_REQUEST_DISPATCHED",
                _as_datetime(row["created_at"], "lifecycle source created_at"),
                {
                    "entry_execution_request_id": row["execution_request_id"],
                    "command_id": row["command_id"],
                },
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                signal_id=str(row["signal_id"]),
                strategy_attempt_id=str(row["strategy_attempt_id"]),
                entry_decision_id=str(row["entry_decision_id"]),
                entry_execution_request_id=str(row["execution_request_id"]),
                exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
            )

        acknowledgements = self._connection.execute(
            """SELECT reservation_id,strategy_attempt_id,signal_id,strategy_id,
                      strategy_version,strategy_config_fingerprint,
                      exchange_commitment_ref,exchange_commitment_at
                 FROM runtime.capital_reservations
                WHERE exchange_commitment_ref IS NOT NULL
                  AND exchange_commitment_at IS NOT NULL"""
        ).fetchall()
        for row in acknowledgements:
            total += self._insert_event(
                "ENTRY_ORDER_ACKNOWLEDGED",
                _as_datetime(
                    row["exchange_commitment_at"],
                    "CapitalReservation.exchange_commitment_at",
                ),
                {
                    "reservation_id": row["reservation_id"],
                    "exchange_order_id": row["exchange_commitment_ref"],
                },
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                signal_id=str(row["signal_id"]),
                strategy_attempt_id=str(row["strategy_attempt_id"]),
            )

        positions = self._connection.execute(
            """SELECT position_id,strategy_id,strategy_version,
                      strategy_config_fingerprint,strategy_activation_id,
                      signal_id,strategy_attempt_id,entry_decision_id,
                      entry_execution_request_id,entry_plan_fingerprint,
                      exit_plan_fingerprint,entry_command_id,fill_at,closed_at,state
                 FROM runtime.position_ownership
                WHERE bot_instance_id='universal-entry'"""
        ).fetchall()
        for row in positions:
            exact = {
                "strategy_position_id": row["position_id"],
                "entry_command_id": row["entry_command_id"],
                "entry_plan_fingerprint": row["entry_plan_fingerprint"],
                "exit_plan_fingerprint": row["exit_plan_fingerprint"],
            }
            fill_at = _as_datetime(row["fill_at"], "StrategyPosition.fill_at")
            strategy_id = str(row["strategy_id"])
            strategy_version = str(row["strategy_version"])
            strategy_fingerprint = str(row["strategy_config_fingerprint"])
            activation_id = str(row["strategy_activation_id"])
            signal_id = str(row["signal_id"])
            attempt_id = str(row["strategy_attempt_id"])
            entry_decision_id = str(row["entry_decision_id"])
            entry_request_id = str(row["entry_execution_request_id"])
            position_id = str(row["position_id"])
            exit_plan_fingerprint = str(row["exit_plan_fingerprint"])
            position_events: list[tuple[str, datetime]] = [
                ("ENTRY_FILLED", fill_at),
                ("STRATEGY_POSITION_CREATED", fill_at),
                ("EXIT_PLAN_BOUND", fill_at),
            ]
            if str(row["state"]) == "CLOSED" and row["closed_at"] is not None:
                position_events.append(
                    (
                        "POSITION_CLOSED",
                        _as_datetime(row["closed_at"], "StrategyPosition.closed_at"),
                    )
                )
            for event_type, occurred_at in position_events:
                total += self._insert_event(
                    event_type,
                    occurred_at,
                    exact,
                    strategy_id=strategy_id,
                    strategy_version=strategy_version,
                    strategy_config_fingerprint=strategy_fingerprint,
                    strategy_activation_id=activation_id,
                    signal_id=signal_id,
                    strategy_attempt_id=attempt_id,
                    entry_decision_id=entry_decision_id,
                    entry_execution_request_id=entry_request_id,
                    strategy_position_id=position_id,
                    exit_plan_fingerprint=exit_plan_fingerprint,
                )

        claims = self._connection.execute(
            """SELECT claim_id,strategy_position_id,exit_plan_fingerprint,
                      strategy_activation_id,consumer_instance_id,claimed_at
                 FROM runtime.position_exit_claims
                WHERE status='CLAIMED'"""
        ).fetchall()
        for row in claims:
            total += self._insert_event(
                "EXIT_POSITION_CLAIMED",
                _as_datetime(row["claimed_at"], "ExitClaim.claimed_at"),
                {
                    "claim_id": row["claim_id"],
                    "consumer_instance_id": row["consumer_instance_id"],
                },
                strategy_activation_id=str(row["strategy_activation_id"]),
                strategy_position_id=str(row["strategy_position_id"]),
                exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
            )

        exit_decisions = self._connection.execute(
            """SELECT exit_decision_id,strategy_position_id,strategy_id,
                      strategy_version,strategy_config_fingerprint,
                      exit_plan_fingerprint,decided_at
                 FROM strategy_exit.exit_decisions"""
        ).fetchall()
        for row in exit_decisions:
            total += self._insert_event(
                "EXIT_DECISION_CREATED",
                _as_datetime(row["decided_at"], "decision.decided_at"),
                {"exit_decision_id": row["exit_decision_id"]},
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                strategy_position_id=str(row["strategy_position_id"]),
                exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
                exit_decision_id=str(row["exit_decision_id"]),
            )

        exit_requests = self._connection.execute(
            """SELECT execution_request_id,exit_decision_id,strategy_position_id,
                      strategy_id,strategy_version,strategy_config_fingerprint,
                      exit_plan_fingerprint,requested_at
                 FROM strategy_exit.execution_requests"""
        ).fetchall()
        for row in exit_requests:
            total += self._insert_event(
                "EXIT_REQUEST_CREATED",
                _as_datetime(row["requested_at"], "ExecutionRequest.requested_at"),
                {"exit_execution_request_id": row["execution_request_id"]},
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                strategy_position_id=str(row["strategy_position_id"]),
                exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
                exit_decision_id=str(row["exit_decision_id"]),
                exit_execution_request_id=str(row["execution_request_id"]),
            )

        exit_dispatches = self._connection.execute(
            """SELECT d.execution_request_id,d.command_id,d.created_at,
                      r.exit_decision_id,r.strategy_position_id,r.strategy_id,
                      r.strategy_version,r.strategy_config_fingerprint,
                      r.exit_plan_fingerprint
                 FROM strategy_exit.execution_dispatches d
                 JOIN strategy_exit.execution_requests r
                   ON r.execution_request_id=d.execution_request_id
                WHERE d.state='DISPATCHED'"""
        ).fetchall()
        for row in exit_dispatches:
            total += self._insert_event(
                "EXIT_REQUEST_DISPATCHED",
                _as_datetime(row["created_at"], "lifecycle source created_at"),
                {
                    "exit_execution_request_id": row["execution_request_id"],
                    "command_id": row["command_id"],
                },
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                strategy_position_id=str(row["strategy_position_id"]),
                exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
                exit_decision_id=str(row["exit_decision_id"]),
                exit_execution_request_id=str(row["execution_request_id"]),
            )

        mutations = self._connection.execute(
            """SELECT tc.command_id,tc.finished_at_epoch_ms,tc.state,
                      tc.payload_json::jsonb AS payload,
                      d.execution_request_id,r.exit_decision_id,
                      r.strategy_position_id,r.strategy_id,r.strategy_version,
                      r.strategy_config_fingerprint,r.exit_plan_fingerprint
                 FROM runtime.trade_commands tc
                 JOIN strategy_exit.execution_dispatches d
                   ON d.command_id=tc.command_id
                 JOIN strategy_exit.execution_requests r
                   ON r.execution_request_id=d.execution_request_id
                WHERE tc.command_type='strategy_exit'
                  AND tc.state='completed'
                  AND tc.finished_at_epoch_ms IS NOT NULL"""
        ).fetchall()
        for row in mutations:
            occurred = datetime.fromtimestamp(
                int(str(row["finished_at_epoch_ms"])) / 1000,
                UTC,
            )
            exact = {
                "command_id": row["command_id"],
                "exit_execution_request_id": row["execution_request_id"],
            }
            for event_type in (
                "EXIT_MUTATION_ACKNOWLEDGED",
                "EXIT_MUTATION_CONFIRMED",
            ):
                total += self._insert_event(
                    event_type,
                    occurred,
                    exact,
                    strategy_id=str(row["strategy_id"]),
                    strategy_version=str(row["strategy_version"]),
                    strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                    strategy_position_id=str(row["strategy_position_id"]),
                    exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
                    exit_decision_id=str(row["exit_decision_id"]),
                    exit_execution_request_id=str(row["execution_request_id"]),
                )

        economics = self._connection.execute(
            """SELECT a.position_id,a.closed_at,a.attribution_id,
                      a.economics_completeness,p.strategy_id,p.strategy_version,
                      p.strategy_config_fingerprint,p.strategy_activation_id,
                      p.signal_id,p.strategy_attempt_id,p.entry_decision_id,
                      p.entry_execution_request_id,p.exit_plan_fingerprint
                 FROM runtime.position_exit_attribution a
                 JOIN runtime.position_ownership p ON p.position_id=a.position_id
                WHERE p.bot_instance_id='universal-entry'"""
        ).fetchall()
        for row in economics:
            total += self._insert_event(
                "ECONOMICS_FINALIZED",
                _as_datetime(row["closed_at"], "position_exit_attribution.closed_at"),
                {
                    "strategy_position_id": row["position_id"],
                    "attribution_id": row["attribution_id"],
                    "economics_completeness": row["economics_completeness"],
                },
                payload={"economics_completeness": row["economics_completeness"]},
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
                strategy_activation_id=str(row["strategy_activation_id"]),
                signal_id=str(row["signal_id"]),
                strategy_attempt_id=str(row["strategy_attempt_id"]),
                entry_decision_id=str(row["entry_decision_id"]),
                entry_execution_request_id=str(row["entry_execution_request_id"]),
                strategy_position_id=str(row["position_id"]),
                exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
            )
        return total

    def _detect_faults(self, now: datetime) -> tuple[FaultCondition, ...]:
        faults: list[FaultCondition] = []

        active = self._connection.execute(
            """SELECT a.activation_id,a.strategy_id,a.strategy_version,
                      a.strategy_config_fingerprint,
                      count(DISTINCT e.entry_plan_fingerprint) AS entry_plan_count,
                      min(e.entry_plan_fingerprint) AS entry_plan_fingerprint,
                      count(DISTINCT x.exit_plan_fingerprint) AS exit_plan_count,
                      min(x.exit_plan_fingerprint) AS exit_plan_fingerprint
                 FROM strategy_entry.strategy_activations a
                 LEFT JOIN strategy_entry.entry_plans e
                   ON e.strategy_id=a.strategy_id
                  AND e.strategy_version=a.strategy_version
                  AND e.strategy_config_fingerprint=a.strategy_config_fingerprint
                 LEFT JOIN strategy_entry.exit_plans x
                   ON x.strategy_id=a.strategy_id
                  AND x.strategy_version=a.strategy_version
                  AND x.strategy_config_fingerprint=a.strategy_config_fingerprint
                WHERE a.enabled=true
                GROUP BY a.activation_id,a.strategy_id,a.strategy_version,
                         a.strategy_config_fingerprint"""
        ).fetchall()
        for row in active:
            activation_id = str(row["activation_id"])
            entry_count = int(str(row["entry_plan_count"]))
            exit_count = int(str(row["exit_plan_count"]))
            entry_fp = row["entry_plan_fingerprint"]
            if entry_count != 1 or exit_count != 1:
                faults.append(
                    FaultCondition(
                        "PLAN_PAIR_INCOMPLETE",
                        FaultSeverity.CRITICAL,
                        activation_id,
                        None,
                        {"strategy_activation_id": activation_id},
                        {
                            "entry_plan_count": entry_count,
                            "exit_plan_count": exit_count,
                        },
                    )
                )
            elif (
                self._connection.execute(
                    """SELECT 1 FROM runtime.plan_consumptions
                        WHERE plan_kind='ENTRY'
                          AND entry_plan_fingerprint=%s
                          AND strategy_activation_id=%s
                          AND consumer_kind='ENTRY_ENGINE'
                          AND status='LOADED'
                        LIMIT 1""",
                    (entry_fp, activation_id),
                ).fetchone()
                is None
            ):
                faults.append(
                    FaultCondition(
                        "ENTRY_PLAN_NOT_CONSUMED",
                        FaultSeverity.ERROR,
                        activation_id,
                        None,
                        {
                            "strategy_activation_id": activation_id,
                            "entry_plan_fingerprint": entry_fp,
                        },
                        {"reason": ("active EntryPlan has no LOADED Entry Engine acknowledgement")},
                    )
                )

        entry_requests = self._connection.execute(
            """SELECT r.execution_request_id,r.strategy_attempt_id,
                      c.pre_dispatch_expires_at,d.state,d.reason
                 FROM strategy_entry.execution_requests r
                 LEFT JOIN runtime.capital_reservations c
                   ON c.reservation_id=r.capital_reservation_id
                 LEFT JOIN strategy_entry.execution_dispatches d
                   ON d.execution_request_id=r.execution_request_id"""
        ).fetchall()
        for row in entry_requests:
            blocked = str(row["state"] or "") == "BLOCKED"
            expired = (
                row["state"] is None
                and row["pre_dispatch_expires_at"] is not None
                and _as_datetime(
                    row["pre_dispatch_expires_at"],
                    "CapitalReservation.pre_dispatch_expires_at",
                )
                <= now
            )
            if blocked or expired:
                request_id = str(row["execution_request_id"])
                faults.append(
                    FaultCondition(
                        "ENTRY_REQUEST_NOT_DISPATCHED",
                        FaultSeverity.ERROR,
                        request_id,
                        None,
                        {
                            "entry_execution_request_id": request_id,
                            "strategy_attempt_id": row["strategy_attempt_id"],
                        },
                        {"reason": row["reason"] if blocked else "request expired before dispatch"},
                    )
                )

        ambiguous_entry = self._connection.execute(
            """SELECT reservation_id,strategy_attempt_id,strategy_position_id
                 FROM runtime.capital_reservations
                WHERE state='RECONCILIATION_REQUIRED'"""
        ).fetchall()
        for row in ambiguous_entry:
            faults.append(
                FaultCondition(
                    "ENTRY_EXECUTION_AMBIGUOUS",
                    FaultSeverity.CRITICAL,
                    str(row["reservation_id"]),
                    (
                        None
                        if row["strategy_position_id"] is None
                        else str(row["strategy_position_id"])
                    ),
                    {
                        "reservation_id": row["reservation_id"],
                        "strategy_attempt_id": row["strategy_attempt_id"],
                    },
                    {"state": "RECONCILIATION_REQUIRED"},
                )
            )

        stuck = self._connection.execute(
            """SELECT reservation_id,strategy_attempt_id
                 FROM runtime.capital_reservations
                WHERE state='RESERVED' AND pre_dispatch_expires_at <= %s""",
            (now,),
        ).fetchall()
        for row in stuck:
            faults.append(
                FaultCondition(
                    "CAPITAL_RESERVATION_STUCK",
                    FaultSeverity.ERROR,
                    str(row["reservation_id"]),
                    None,
                    {
                        "reservation_id": row["reservation_id"],
                        "strategy_attempt_id": row["strategy_attempt_id"],
                    },
                    {"reason": "RESERVED after exact pre-dispatch expiry"},
                )
            )

        positions = self._connection.execute(
            """SELECT p.position_id,p.exit_plan_fingerprint,p.strategy_activation_id,
                      p.strategy_id,p.strategy_version,
                      p.strategy_config_fingerprint,p.state,
                      x.exit_plan_fingerprint AS exact_exit_plan,
                      c.claim_id,c.exit_plan_fingerprint AS claimed_exit_plan,
                      c.status AS claim_status,c.last_seen_at AS claim_last_seen_at
                 FROM runtime.position_ownership p
                 LEFT JOIN strategy_entry.exit_plans x
                   ON x.exit_plan_fingerprint=p.exit_plan_fingerprint
                  AND x.strategy_id=p.strategy_id
                  AND x.strategy_version=p.strategy_version
                  AND x.strategy_config_fingerprint=p.strategy_config_fingerprint
                 LEFT JOIN runtime.position_exit_claims c
                   ON c.strategy_position_id=p.position_id
                WHERE p.bot_instance_id='universal-entry'
                  AND p.state IN ('OPEN','RECONCILIATION_REQUIRED')"""
        ).fetchall()
        for row in positions:
            position_id = str(row["position_id"])
            required = (
                row["exit_plan_fingerprint"],
                row["strategy_activation_id"],
                row["strategy_config_fingerprint"],
            )
            if any(value is None or str(value) == "" for value in required):
                faults.append(
                    FaultCondition(
                        "POSITION_LINEAGE_INCOMPLETE",
                        FaultSeverity.CRITICAL,
                        position_id,
                        position_id,
                        {"strategy_position_id": position_id},
                        {"reason": "required Universal position lineage is incomplete"},
                    )
                )
                continue
            if row["exact_exit_plan"] is None:
                faults.append(
                    FaultCondition(
                        "POSITION_WITHOUT_EXIT_PLAN",
                        FaultSeverity.CRITICAL,
                        position_id,
                        position_id,
                        {
                            "strategy_position_id": position_id,
                            "exit_plan_fingerprint": row["exit_plan_fingerprint"],
                        },
                        {"reason": "exact bound ExitPlan is missing"},
                    )
                )
                continue
            claim_reason: str | None = None
            if row["claim_id"] is None:
                claim_reason = "open StrategyPosition lacks exact Exit Engine claim"
            elif str(row["claim_status"] or "") != "CLAIMED":
                claim_reason = "Exit Engine claim is not active: " + str(
                    row["claim_status"] or "UNKNOWN"
                )
            elif str(row["claimed_exit_plan"] or "") != str(row["exit_plan_fingerprint"]):
                claim_reason = "Exit Engine claim has wrong ExitPlan lineage"
            elif self._policy.exit_owner_max_age_seconds is not None:
                last_seen = _as_datetime(
                    row["claim_last_seen_at"],
                    "ExitEngineClaim.last_seen_at",
                )
                age = (now - last_seen).total_seconds()
                if age < 0:
                    claim_reason = "Exit Engine claim heartbeat is from the future"
                elif age > self._policy.exit_owner_max_age_seconds:
                    claim_reason = "Exit Engine claim heartbeat is stale"
            if claim_reason is not None:
                faults.append(
                    FaultCondition(
                        "POSITION_WITHOUT_EXIT_OWNER",
                        FaultSeverity.CRITICAL,
                        position_id,
                        position_id,
                        {
                            "strategy_position_id": position_id,
                            "exit_plan_fingerprint": row["exit_plan_fingerprint"],
                        },
                        {"reason": claim_reason},
                    )
                )

        exit_requests = self._connection.execute(
            """SELECT r.execution_request_id,r.strategy_position_id,r.expires_at,
                      d.state,d.reason
                 FROM strategy_exit.execution_requests r
                 LEFT JOIN strategy_exit.execution_dispatches d
                   ON d.execution_request_id=r.execution_request_id"""
        ).fetchall()
        for row in exit_requests:
            blocked = str(row["state"] or "") == "BLOCKED"
            expired = (
                row["state"] is None
                and _as_datetime(
                    row["expires_at"],
                    "ExitExecutionRequest.expires_at",
                )
                <= now
            )
            if blocked or expired:
                request_id = str(row["execution_request_id"])
                position_id = str(row["strategy_position_id"])
                faults.append(
                    FaultCondition(
                        "EXIT_REQUEST_NOT_DISPATCHED",
                        FaultSeverity.ERROR,
                        request_id,
                        position_id,
                        {
                            "exit_execution_request_id": request_id,
                            "strategy_position_id": position_id,
                        },
                        {"reason": row["reason"] if blocked else "request expired before dispatch"},
                    )
                )

        exit_ambiguous = self._connection.execute(
            """SELECT DISTINCT p.position_id
                 FROM runtime.position_ownership p
                 JOIN strategy_exit.execution_requests r
                   ON r.strategy_position_id=p.position_id
                 JOIN strategy_exit.execution_dispatches d
                   ON d.execution_request_id=r.execution_request_id
                WHERE p.state='RECONCILIATION_REQUIRED'
                  AND d.state='DISPATCHED'"""
        ).fetchall()
        for row in exit_ambiguous:
            position_id = str(row["position_id"])
            faults.append(
                FaultCondition(
                    "EXIT_EXECUTION_AMBIGUOUS",
                    FaultSeverity.CRITICAL,
                    position_id,
                    position_id,
                    {"strategy_position_id": position_id},
                    {"state": "RECONCILIATION_REQUIRED"},
                )
            )

        conflicts = self._connection.execute(
            """SELECT dispatch_id,execution_request_id,reason
                 FROM strategy_entry.execution_dispatches
                WHERE state='BLOCKED'
                  AND reason LIKE 'EXCHANGE_POSITION_OWNERSHIP_CONFLICT%'"""
        ).fetchall()
        for row in conflicts:
            faults.append(
                FaultCondition(
                    "EXCHANGE_POSITION_OWNERSHIP_CONFLICT",
                    FaultSeverity.CRITICAL,
                    str(row["dispatch_id"]),
                    None,
                    {
                        "dispatch_id": row["dispatch_id"],
                        "entry_execution_request_id": row["execution_request_id"],
                    },
                    {"reason": row["reason"]},
                )
            )

        faults.extend(self._exchange_divergence_faults(now))
        return tuple(faults)

    def _fresh_exchange_reconciliation(self, now: datetime) -> datetime | None:
        max_age = self._policy.exchange_state_max_age_seconds
        if max_age is None:
            return None
        run = self._connection.execute(
            """SELECT finished_at_epoch_ms,ok
                 FROM runtime.reconciliation_runs
                ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if run is None or int(str(run["ok"])) != 1:
            return None
        finished_at = datetime.fromtimestamp(
            int(str(run["finished_at_epoch_ms"])) / 1000,
            UTC,
        )
        age_seconds = (now - finished_at).total_seconds()
        if age_seconds < 0 or age_seconds > max_age:
            return None
        return finished_at

    def _exchange_divergence_faults(self, now: datetime) -> tuple[FaultCondition, ...]:
        finished_at = self._fresh_exchange_reconciliation(now)
        if finished_at is None:
            return ()
        rows = self._connection.execute(
            """SELECT p.position_id,p.symbol,p.position_idx,p.side,
                      h.side AS hot_side,h.size AS hot_size
                 FROM runtime.position_ownership p
                 LEFT JOIN runtime.hot_positions h
                   ON h.symbol=p.symbol AND h.position_idx=p.position_idx
                WHERE p.bot_instance_id='universal-entry' AND p.state='OPEN'"""
        ).fetchall()
        faults: list[FaultCondition] = []
        for row in rows:
            mismatch = (
                row["hot_side"] is None
                or str(row["hot_side"]) != str(row["side"])
                or str(row["hot_size"] or "0") in {"", "0", "0.0"}
            )
            if mismatch:
                position_id = str(row["position_id"])
                faults.append(
                    FaultCondition(
                        "EXCHANGE_STATE_DIVERGED",
                        FaultSeverity.CRITICAL,
                        position_id,
                        position_id,
                        {
                            "strategy_position_id": position_id,
                            "symbol": row["symbol"],
                            "position_idx": row["position_idx"],
                        },
                        {
                            "reconciliation_finished_at": finished_at.isoformat(),
                            "hot_side": row["hot_side"],
                            "hot_size": row["hot_size"],
                        },
                    )
                )
        return tuple(faults)

    def _fault_id(self, condition: FaultCondition) -> str:
        return (
            "lifecycle-fault-"
            + fingerprint({"fault_code": condition.code, "scope_key": condition.scope_key})[:32]
        )

    def _upsert_fault(self, condition: FaultCondition, now: datetime) -> int:
        fault_id = self._fault_id(condition)
        existing = self._connection.execute(
            "SELECT state FROM runtime.lifecycle_faults WHERE fault_id=%s",
            (fault_id,),
        ).fetchone()
        if existing is None:
            self._connection.execute(
                """INSERT INTO runtime.lifecycle_faults(
                       fault_id,fault_code,severity,state,strategy_position_id,
                       detected_at,resolved_at,exact_ids,payload
                   ) VALUES(%s,%s,%s,'OPEN',%s,%s,NULL,%s::jsonb,%s::jsonb)""",
                (
                    fault_id,
                    condition.code,
                    condition.severity.value,
                    condition.strategy_position_id,
                    now,
                    canonical_json(dict(condition.exact_ids)),
                    canonical_json(dict(condition.payload)),
                ),
            )
            self._insert_event(
                "LIFECYCLE_FAULT",
                now,
                {"fault_id": fault_id, **dict(condition.exact_ids)},
                payload={
                    "fault_code": condition.code,
                    "severity": condition.severity.value,
                    **dict(condition.payload),
                },
                strategy_position_id=condition.strategy_position_id,
            )
            return 1
        if str(existing["state"]) == "RESOLVED":
            self._connection.execute(
                """UPDATE runtime.lifecycle_faults
                      SET state='OPEN',resolved_at=NULL,payload=%s::jsonb
                    WHERE fault_id=%s""",
                (canonical_json(dict(condition.payload)), fault_id),
            )
            return 1
        return 0

    def _resolve_cleared_faults(
        self,
        active: Sequence[FaultCondition],
        now: datetime,
    ) -> int:
        active_ids = {self._fault_id(item) for item in active}
        rows = self._connection.execute(
            """SELECT fault_id,fault_code
                 FROM runtime.lifecycle_faults
                WHERE state='OPEN'"""
        ).fetchall()
        resolved = 0
        for row in rows:
            fault_code = str(row["fault_code"])
            if fault_code not in _MANAGED_FAULT_CODES:
                continue
            if (
                fault_code == "EXCHANGE_STATE_DIVERGED"
                and self._fresh_exchange_reconciliation(now) is None
            ):
                continue
            fault_id = str(row["fault_id"])
            if fault_id in active_ids:
                continue
            updated = self._connection.execute(
                """UPDATE runtime.lifecycle_faults
                      SET state='RESOLVED',resolved_at=%s
                    WHERE fault_id=%s AND state='OPEN'""",
                (now, fault_id),
            )
            resolved += updated.rowcount
        return resolved
