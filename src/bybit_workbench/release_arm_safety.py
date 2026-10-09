"""Runtime control-plane guard for release-bound REAL Entry.

No Exchange mutation, no Strategy modification, and no position/Exit mutation.
The installer never invokes this as an implicit mutation.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def check_release_arm_invariant(connection: Any, *, loaded_commit: str,
                                now: datetime | None = None) -> bool:
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
    invalid = bool(stale) or (
        gate_open and (not loaded_commit or not sessions or bool(missing_scopes))
    )
    if invalid:
        affected = {
            "loaded_commit": loaded_commit or None,
            "stale_sessions": stale,
            "missing_scopes": missing_scopes,
            "gate_was_open": gate_open,
        }
        reason = "RELEASE_BOUND_LIVE_ARM_NOT_READY"
        connection.execute(
            """INSERT INTO control.release_arm_incidents(
                   incident_id,release_commit,reason,affected,state,
                   detected_at,last_checked_at)
               VALUES(%s,%s,%s,%s::jsonb,'OPEN',%s,%s)
               ON CONFLICT (release_commit) WHERE state='OPEN'
               DO UPDATE SET affected=EXCLUDED.affected,
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

    # A clean, matching, authorized state is independent recovery evidence.
    # A deliberately disarmed state with no ACTIVE session is also safe.
    connection.execute(
        """UPDATE control.release_arm_incidents
              SET state='RESOLVED',resolved_at=%s,last_checked_at=%s
            WHERE state='OPEN'""",
        (instant, instant),
    )
    return gate_open
