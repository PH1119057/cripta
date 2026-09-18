from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Protocol

from bybit_workbench.universal_entry.fingerprint import canonical_json, fingerprint


class CursorLike(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Mapping[str, object] | None: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...

    def transaction(self) -> AbstractContextManager[object]: ...


def record_plan_consumption(
    connection: ConnectionLike,
    *,
    plan_kind: str,
    plan_fingerprint: str,
    strategy_activation_id: str,
    consumer_instance_id: str,
    seen_at: datetime,
    status: str = "LOADED",
    payload: Mapping[str, object] | None = None,
) -> str:
    if plan_kind not in {"ENTRY", "EXIT"}:
        raise ValueError("plan_kind must be ENTRY or EXIT")
    consumer_kind = "ENTRY_ENGINE" if plan_kind == "ENTRY" else "EXIT_ENGINE"
    if status not in {"LOADED", "STALE", "ERROR"}:
        raise ValueError("unsupported plan consumption status")
    when = seen_at.astimezone(UTC)
    identity = {
        "plan_kind": plan_kind,
        "plan_fingerprint": plan_fingerprint,
        "strategy_activation_id": strategy_activation_id,
        "consumer_kind": consumer_kind,
        "consumer_instance_id": consumer_instance_id,
    }
    consumption_id = "plan-consumption-" + fingerprint(identity)[:32]
    entry_fp = plan_fingerprint if plan_kind == "ENTRY" else None
    exit_fp = plan_fingerprint if plan_kind == "EXIT" else None
    with connection.transaction():
        connection.execute(
            """INSERT INTO runtime.plan_consumptions(
                   plan_consumption_id,plan_kind,entry_plan_fingerprint,
                   exit_plan_fingerprint,strategy_activation_id,consumer_kind,
                   consumer_instance_id,loaded_at,last_seen_at,status,payload
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT(plan_consumption_id) DO UPDATE
                   SET last_seen_at=GREATEST(
                           runtime.plan_consumptions.last_seen_at,
                           excluded.last_seen_at
                       ),
                       status=excluded.status,
                       payload=excluded.payload""",
            (
                consumption_id,
                plan_kind,
                entry_fp,
                exit_fp,
                strategy_activation_id,
                consumer_kind,
                consumer_instance_id,
                when,
                when,
                status,
                canonical_json(dict(payload or {})),
            ),
        )
    return consumption_id


def claim_exit_position(
    connection: ConnectionLike,
    *,
    strategy_position_id: str,
    consumer_instance_id: str,
    claimed_at: datetime,
    payload: Mapping[str, object] | None = None,
) -> str:
    when = claimed_at.astimezone(UTC)
    with connection.transaction():
        row = connection.execute(
            """SELECT p.position_id,p.exit_plan_fingerprint,p.strategy_activation_id,
                      p.state,p.bot_instance_id,ep.exit_plan_fingerprint AS exact_exit_plan
                 FROM runtime.position_ownership p
                 LEFT JOIN strategy_entry.exit_plans ep
                   ON ep.exit_plan_fingerprint=p.exit_plan_fingerprint
                  AND ep.strategy_id=p.strategy_id
                  AND ep.strategy_version=p.strategy_version
                  AND ep.strategy_config_fingerprint=p.strategy_config_fingerprint
                WHERE p.position_id=%s
                FOR UPDATE OF p""",
            (strategy_position_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"StrategyPosition not found: {strategy_position_id}")
        if str(row["state"]) != "OPEN":
            raise RuntimeError("Exit Engine can claim only OPEN StrategyPosition")
        if str(row["bot_instance_id"]) != "universal-entry":
            raise RuntimeError("Exit Engine claim requires Universal StrategyPosition")
        exit_fp = str(row["exit_plan_fingerprint"] or "")
        activation_id = str(row["strategy_activation_id"] or "")
        if not exit_fp or not activation_id or row["exact_exit_plan"] is None:
            raise RuntimeError("StrategyPosition has no exact ExitPlan binding")

        claim_id = (
            "exit-claim-"
            + fingerprint(
                {
                    "strategy_position_id": strategy_position_id,
                    "exit_plan_fingerprint": exit_fp,
                    "strategy_activation_id": activation_id,
                    "consumer_instance_id": consumer_instance_id,
                }
            )[:32]
        )
        evidence = {
            "source": "universal_exit_engine",
            **dict(payload or {}),
        }
        connection.execute(
            """INSERT INTO runtime.position_exit_claims(
                   claim_id,strategy_position_id,exit_plan_fingerprint,
                   strategy_activation_id,consumer_instance_id,claimed_at,
                   last_seen_at,status,payload
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,'CLAIMED',%s::jsonb)
               ON CONFLICT(strategy_position_id) DO NOTHING""",
            (
                claim_id,
                strategy_position_id,
                exit_fp,
                activation_id,
                consumer_instance_id,
                when,
                when,
                canonical_json(evidence),
            ),
        )
        existing = connection.execute(
            """SELECT claim_id,exit_plan_fingerprint,strategy_activation_id,
                      consumer_instance_id
                 FROM runtime.position_exit_claims
                WHERE strategy_position_id=%s
                FOR UPDATE""",
            (strategy_position_id,),
        ).fetchone()
        if existing is None:
            raise RuntimeError("Exit claim persistence disappeared")
        expected_claim = (
            claim_id,
            exit_fp,
            activation_id,
            consumer_instance_id,
        )
        actual_claim = (
            str(existing["claim_id"]),
            str(existing["exit_plan_fingerprint"]),
            str(existing["strategy_activation_id"]),
            str(existing["consumer_instance_id"]),
        )
        if actual_claim != expected_claim:
            raise RuntimeError("StrategyPosition already claimed by another Exit Engine")
        connection.execute(
            """UPDATE runtime.position_exit_claims
                  SET last_seen_at=GREATEST(last_seen_at,%s),
                      status='CLAIMED',
                      payload=%s::jsonb
                WHERE claim_id=%s""",
            (when, canonical_json(evidence), claim_id),
        )
        record_plan_consumption(
            connection,
            plan_kind="EXIT",
            plan_fingerprint=exit_fp,
            strategy_activation_id=activation_id,
            consumer_instance_id=consumer_instance_id,
            seen_at=when,
            status="LOADED",
            payload={"strategy_position_id": strategy_position_id},
        )
    return claim_id
