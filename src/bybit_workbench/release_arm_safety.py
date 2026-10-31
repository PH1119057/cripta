"""Runtime control-plane guard for release-bound REAL Entry.

No Exchange mutation, no Strategy modification, and no position/Exit mutation.
The installer never invokes this as an implicit mutation.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4


def check_release_arm_invariant(
    connection: Any,
    *,
    loaded_commit: str,
    now: datetime | None = None,
    require_entry_runtime_health: bool = False,
    entry_source_ready: bool | None = None,
    entry_source_detail: str | None = None,
    health_max_age_seconds: int = 8,
) -> bool:
    """Return True when new Entry may proceed from a release-bound perspective.

    A mismatch closes only the global *new-Entry* gate and raises a durable
    operator incident; it leaves existing positions, orders and Exit untouched.
    Caller must commit the transaction before consuming the result.
    """
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates "
        "WHERE mode='mainnet' FOR UPDATE"
    ).fetchone()
    gate_open = bool(gate and (gate['enabled'] if isinstance(gate, Mapping) else gate[0]))
    sessions = connection.execute(
        """SELECT strategy_id,strategy_version,symbol,release_commit
             FROM control.live_arm_sessions
            WHERE state='ACTIVE' ORDER BY strategy_id,symbol"""
    ).fetchall()
    permissions = connection.execute(
        """SELECT strategy_id,strategy_version
             FROM strategy_entry.execution_permissions
            WHERE enabled=true ORDER BY strategy_id,strategy_version"""
    ).fetchall()
    def field(row: Any, index: int, key: str) -> Any:
        return row[key] if isinstance(row, Mapping) else row[index]

    session_scopes = {
        (str(field(r, 0, "strategy_id")), str(field(r, 1, "strategy_version")))
        for r in sessions
    }
    permitted_scopes = {
        (str(field(r, 0, "strategy_id")), str(field(r, 1, "strategy_version")))
        for r in permissions
    }
    stale = [
        {"strategy_id": str(field(r, 0, "strategy_id")),
         "strategy_version": str(field(r, 1, "strategy_version")),
         "symbol": str(field(r, 2, "symbol")),
         "session_release_commit": str(field(r, 3, "release_commit"))}
        for r in sessions
        if not loaded_commit or str(field(r, 3, "release_commit")) != loaded_commit
    ]
    missing_scopes = sorted(permitted_scopes - session_scopes)
    runtime_health: dict[str, object] = {
        "required": require_entry_runtime_health,
        "source_ready": entry_source_ready,
        "source_detail": entry_source_detail,
        "consumer_accounts": [],
        "consumer_failures": [],
    }
    runtime_invalid = False
    runtime_scope_present = bool(gate_open or sessions or permissions)
    if require_entry_runtime_health and runtime_scope_present:
        expected_accounts = connection.execute(
            """SELECT DISTINCT b.trading_account_id
                 FROM strategy_entry.execution_permissions p
                 JOIN strategy_entry.strategy_activations a
                   ON a.strategy_id=p.strategy_id
                  AND a.strategy_version=p.strategy_version
                  AND a.strategy_config_fingerprint=p.strategy_config_fingerprint
                  AND a.enabled=true
                 JOIN strategy_entry.strategy_execution_bindings b
                   ON b.strategy_activation_id=a.activation_id
                  AND b.binding_state='ACTIVE'
                WHERE p.enabled=true
                  AND b.trading_account_id IS NOT NULL
                ORDER BY b.trading_account_id"""
        ).fetchall()
        account_ids = [
            str(field(row, 0, "trading_account_id")) for row in expected_accounts
        ]
        runtime_health["consumer_accounts"] = account_ids
        consumer_failures: list[dict[str, object]] = []
        fresh_after = instant - timedelta(seconds=health_max_age_seconds)
        for account_id in account_ids:
            row = connection.execute(
                """SELECT account_connector_id,release_commit,state,detail,observed_at
                     FROM runtime.entry_consumer_health
                    WHERE trading_account_id=%s""",
                (account_id,),
            ).fetchone()
            if row is None:
                consumer_failures.append({
                    "trading_account_id": account_id,
                    "reason": "MISSING",
                })
                continue
            connector = str(field(row, 0, "account_connector_id"))
            release = str(field(row, 1, "release_commit"))
            state = str(field(row, 2, "state"))
            detail = str(field(row, 3, "detail"))
            observed = field(row, 4, "observed_at")
            fresh = (
                isinstance(observed, datetime)
                and observed.tzinfo is not None
                and observed.astimezone(UTC) >= fresh_after
            )
            if release != loaded_commit or state != "READY" or not fresh:
                consumer_failures.append({
                    "trading_account_id": account_id,
                    "account_connector_id": connector,
                    "release_commit": release,
                    "state": state,
                    "detail": detail,
                    "observed_at": (
                        observed.isoformat()
                        if isinstance(observed, datetime) else str(observed)
                    ),
                    "fresh": fresh,
                })
        runtime_health["consumer_failures"] = consumer_failures
        runtime_invalid = (
            not account_ids
            or bool(consumer_failures)
            or entry_source_ready is not True
        )

    invalid = bool(stale) or runtime_invalid or (
        gate_open and (not loaded_commit or not sessions or bool(missing_scopes))
    )
    if invalid:
        affected = {
            "loaded_commit": loaded_commit or None,
            "stale_sessions": stale,
            "missing_scopes": missing_scopes,
            "gate_was_open": gate_open,
            "entry_runtime_health": runtime_health,
        }
        reason = (
            "REAL_ENTRY_RUNTIME_NOT_READY"
            if runtime_invalid else "RELEASE_BOUND_LIVE_ARM_NOT_READY"
        )
        connection.execute(
            """INSERT INTO control.release_arm_incidents(
                   incident_id,release_commit,reason,affected,state,
                   detected_at,last_checked_at)
               VALUES(%s,%s,%s,%s::jsonb,'OPEN',%s,%s)
               ON CONFLICT (release_commit) WHERE state='OPEN'
               DO UPDATE SET reason=EXCLUDED.reason,
                             affected=EXCLUDED.affected,
                             last_checked_at=EXCLUDED.last_checked_at""",
            ("release-arm-" + uuid4().hex, loaded_commit or "UNKNOWN",
             reason, json.dumps(affected), instant, instant),
        )
        if gate_open:
            connection.execute(
                """UPDATE control.execution_gates
                      SET enabled=0,reason=%s,updated_at_epoch_ms=%s
                    WHERE mode='mainnet' AND enabled=1""",
                (reason, int(instant.timestamp() * 1000)),
            )
            connection.execute(
                """INSERT INTO control.execution_gate_events(
                     at_epoch_ms,mode,previous_enabled,requested_enabled,
                     resulting_enabled,reason,source,origin,request_id,
                     settings_version)
                   VALUES(%s,'mainnet',true,false,false,%s,
                          'release-arm-runtime-guard','runtime',%s,NULL)""",
                (int(instant.timestamp() * 1000), reason,
                 "release-arm-" + uuid4().hex),
            )
        return False

    # Resolve only incidents this invocation is competent to prove recovered.
    # The lightweight Entry-consumer guard does not inspect observer/source
    # health and therefore must never clear REAL_ENTRY_RUNTIME_NOT_READY.
    # Resolution is also scoped to the exact loaded release; one healthy
    # release cannot erase a durable incident belonging to another release.
    recoverable_reasons = ["RELEASE_BOUND_LIVE_ARM_NOT_READY"]
    if require_entry_runtime_health:
        recoverable_reasons.append("REAL_ENTRY_RUNTIME_NOT_READY")
    connection.execute(
        """UPDATE control.release_arm_incidents
              SET state='RESOLVED',resolved_at=%s,last_checked_at=%s
            WHERE state='OPEN'
              AND release_commit=%s
              AND reason = ANY(%s)""",
        (
            instant,
            instant,
            loaded_commit or "UNKNOWN",
            recoverable_reasons,
        ),
    )
    return gate_open
