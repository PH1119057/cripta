"""Two-phase P90 REAL v3 OWNER ARM: stage while gate CLOSED, then finalize.

The staging transaction may create exact release-bound owner sessions and enable
only the two approved execution permissions. The global mainnet gate remains
CLOSED, therefore no new REAL Entry can execute. Finalization is a separate
short SERIALIZABLE transaction and is permitted only after the Dashboard proves
that the staged observer/runtime is RUNNING/READY on the exact release.

No Exchange mutation is performed by this module.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .account_live_arm import account_live_arm_ready
from .live_arm_readiness import (
    LiveArmContext,
    active_live_arm_session,
    evaluate_live_arm,
    strategy_symbol_scope_key,
)
from .p90l90_real_arm_control import (
    _owner_time,
    p90l90_real_v3_contexts_and_routes,
)
from .universal_entry.dashboard_control import StrategyDashboardStore
from .universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from .universal_entry.p90l90_real_contract_v3 import (
    OWNER_ARM_OPEN_GATE_REASON,
    OWNER_ARM_STAGE_GATE_REASON,
    VERSION,
)


def stage_p90l90_real_v3(
    connection: Any,
    *,
    release_commit: str,
    now: datetime,
    operator: str,
    request_id: str,
    confirmed: bool,
    observer_ready: bool,
) -> dict[str, object]:
    """Create exact owner-arm scope while mainnet gate remains CLOSED."""
    current = _owner_time(operator=operator, request_id=request_id, now=now)
    if confirmed is not True:
        raise ValueError("P90_REAL_OWNER_ARM_NOT_EXPLICITLY_CONFIRMED")
    if not observer_ready:
        raise ValueError("P90_REAL_OBSERVER_NOT_READY")
    source = "dashboard:p90l90-real-v3"

    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        gate = connection.execute(
            "SELECT enabled FROM control.execution_gates "
            "WHERE mode='mainnet' FOR UPDATE"
        ).fetchone()
        if gate is None or bool(gate[0]):
            raise ValueError("P90_REAL_MAINNET_GATE_NOT_CLOSED")

        contexts, routes = p90l90_real_v3_contexts_and_routes(
            connection, release_commit=release_commit, now=current,
        )
        if connection.execute(
            "SELECT count(*) FROM strategy_entry.execution_permissions "
            "WHERE enabled=true"
        ).fetchone() != (0,):
            raise ValueError("P90_REAL_OTHER_EXECUTION_PERMISSION_ACTIVE")
        if connection.execute(
            "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
        ).fetchone() != (0,):
            raise ValueError("P90_REAL_PREVIOUS_OR_UNKNOWN_ARM_SESSION_ACTIVE")
        if connection.execute(
            "SELECT count(*) FROM control.account_live_arm_sessions "
            "WHERE state='ACTIVE'"
        ).fetchone() != (0,):
            raise ValueError("P90_REAL_PREVIOUS_ACCOUNT_ARM_ACTIVE")

        for context in contexts:
            proof = evaluate_live_arm(
                connection,
                context=context,
                now=current,
                require_owner_approval=False,
            )
            if not proof.ready:
                raise ValueError(
                    "P90_REAL_PRE_OWNER_EVIDENCE_MISSING:"
                    + context.strategy_id + ":" + context.symbol + ":"
                    + ",".join(proof.failed_codes)
                )

        session_rows: list[tuple[LiveArmContext, str, str]] = []
        for context in contexts:
            scope = strategy_symbol_scope_key(context)
            witness = (
                request_id + "|" + scope + "|" + release_commit
                + "|" + routes[context.strategy_id][1]
            )
            token = hashlib.sha256(witness.encode()).hexdigest()[:32]
            evidence_id = "p90-real-owner-" + token
            live_session = "p90-real-live-" + token
            account_session = "p90-real-account-" + token

            connection.execute(
                """INSERT INTO control.live_arm_evidence(
                     evidence_id,check_code,scope_type,scope_key,status,
                     checked_at,valid_until,release_commit,source,evidence)
                   VALUES(
                     %s,'MAINNET_GATE_EXPLICIT_OWNER_APPROVAL',
                     'STRATEGY_SYMBOL',%s,'PASS',%s,NULL,%s,%s,%s::jsonb)""",
                (
                    evidence_id,
                    scope,
                    current,
                    release_commit,
                    source,
                    json.dumps(
                        {
                            "owner_action_request_id": request_id,
                            "operator": operator,
                            "strategy_id": context.strategy_id,
                            "strategy_version": context.strategy_version,
                            "trading_account_id": routes[context.strategy_id][1],
                            "account_connector_id": routes[context.strategy_id][2],
                            "symbol": context.symbol,
                            "independent_account_owner_arm": True,
                            "requested_amount_usdt": "10",
                            "leverage": 1,
                            "catastrophic_stop_pct": "10",
                            "arm_phase": "STAGED_GATE_CLOSED",
                        },
                        sort_keys=True,
                    ),
                ),
            )
            full = evaluate_live_arm(
                connection,
                context=context,
                now=current,
                require_owner_approval=True,
            )
            if not full.ready:
                raise ValueError(
                    "P90_REAL_OWNER_ARM_FINAL_EVIDENCE_BLOCKED:"
                    + context.strategy_id + ":" + context.symbol + ":"
                    + ",".join(full.failed_codes)
                )

            connection.execute(
                """INSERT INTO control.live_arm_sessions(
                     live_arm_session_id,strategy_id,strategy_version,
                     strategy_config_fingerprint,strategy_activation_id,
                     symbol,release_commit,state,owner_approved_at,
                     activated_at,deactivated_at,source)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s,NULL,%s)""",
                (
                    live_session,
                    context.strategy_id,
                    context.strategy_version,
                    context.strategy_config_fingerprint,
                    context.strategy_activation_id,
                    context.symbol,
                    release_commit,
                    current,
                    current,
                    source,
                ),
            )
            if active_live_arm_session(connection, context=context) != live_session:
                raise ValueError(
                    "P90_REAL_LIVE_ARM_SESSION_READBACK_FAILED:" + context.symbol
                )

            binding, account, connector = routes[context.strategy_id]
            result = connection.execute(
                """SELECT control.arm_p90l90_real_v3_account_scope(
                       %s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    account_session,
                    live_session,
                    binding,
                    account,
                    connector,
                    context.symbol,
                    release_commit,
                    current,
                    source,
                ),
            ).fetchone()
            if result != (account_session,):
                raise ValueError(
                    "P90_REAL_ACCOUNT_ARM_WRITE_FAILED:" + context.symbol
                )
            account_ready, reasons = account_live_arm_ready(
                connection,
                strategy_activation_id=context.strategy_activation_id,
                strategy_execution_binding_id=binding,
                trading_account_id=account,
                account_connector_id=connector,
                strategy_id=context.strategy_id,
                strategy_version=context.strategy_version,
                strategy_config_fingerprint=context.strategy_config_fingerprint,
                symbol=context.symbol,
                release_commit=release_commit,
                now=current,
            )
            if not account_ready:
                raise ValueError(
                    "P90_REAL_ACCOUNT_ARM_READBACK_FAILED:"
                    + context.symbol + ":" + ",".join(reasons)
                )
            session_rows.append((context, live_session, account_session))

        store = StrategyDashboardStore(connection)
        for strategy_id in (MAIN_ID, SUB_ID):
            context = next(
                item for item in contexts if item.strategy_id == strategy_id
            )
            store.set_execution_permission(
                strategy_id=context.strategy_id,
                strategy_version=context.strategy_version,
                strategy_config_fingerprint=context.strategy_config_fingerprint,
                enabled=True,
                changed_at=current,
                operator=operator,
                source=source,
                reason="owner confirmed P90 REAL v3; gate staged closed",
                observer_ready=True,
            )

        staged = connection.execute(
            """UPDATE control.execution_gates
                  SET enabled=0,reason=%s,updated_at_epoch_ms=%s
                WHERE mode='mainnet' AND enabled=0
                RETURNING enabled,reason""",
            (OWNER_ARM_STAGE_GATE_REASON, int(current.timestamp() * 1000)),
        ).fetchone()
        if staged not in (
            (False, OWNER_ARM_STAGE_GATE_REASON),
            (0, OWNER_ARM_STAGE_GATE_REASON),
        ):
            raise ValueError("P90_REAL_OWNER_ARM_STAGE_FAILED")

        connection.execute(
            """INSERT INTO control.execution_gate_events(
                   at_epoch_ms,mode,previous_enabled,requested_enabled,
                   resulting_enabled,reason,source,origin,request_id,
                   settings_version)
               VALUES(%s,'mainnet',false,false,false,%s,%s,'owner',%s,%s)""",
            (
                int(current.timestamp() * 1000),
                OWNER_ARM_STAGE_GATE_REASON,
                source,
                request_id,
                VERSION,
            ),
        )

    return {
        "status": "ARM_STAGED",
        "strategies": 2,
        "instrument_scopes": 5,
        "accounts": 2,
        "live_session_ids": [item[1] for item in session_rows],
        "account_session_ids": [item[2] for item in session_rows],
        "execution_permissions": 2,
        "gate_enabled": False,
        "exchange_mutation": False,
    }


def finalize_p90l90_real_v3(
    connection: Any,
    *,
    release_commit: str,
    now: datetime,
    operator: str,
    request_id: str,
    observer_stage_ready: bool,
) -> dict[str, object]:
    """Open mainnet gate LAST after exact staged runtime readiness is proven."""
    current = _owner_time(operator=operator, request_id=request_id, now=now)
    if not observer_stage_ready:
        raise ValueError("P90_REAL_OWNER_ARM_STAGE_NOT_READY")
    source = "dashboard:p90l90-real-v3"

    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        gate = connection.execute(
            "SELECT enabled,reason FROM control.execution_gates "
            "WHERE mode='mainnet' FOR UPDATE"
        ).fetchone()
        if gate != (False, OWNER_ARM_STAGE_GATE_REASON):
            raise ValueError("P90_REAL_OWNER_ARM_STAGE_GATE_DRIFT")

        contexts, routes = p90l90_real_v3_contexts_and_routes(
            connection, release_commit=release_commit, now=current,
        )
        expected_permissions = {
            (
                context.strategy_id,
                context.strategy_version,
                context.strategy_config_fingerprint,
            )
            for context in contexts
        }
        actual_permissions = {
            tuple(str(value) for value in row)
            for row in connection.execute(
                """SELECT strategy_id,strategy_version,strategy_config_fingerprint
                     FROM strategy_entry.execution_permissions
                    WHERE enabled=true
                    ORDER BY strategy_id,strategy_version"""
            ).fetchall()
        }
        if actual_permissions != expected_permissions:
            raise ValueError("P90_REAL_OWNER_ARM_STAGE_PERMISSION_DRIFT")

        expected_sessions = {
            (
                context.strategy_id,
                context.strategy_version,
                context.symbol,
                release_commit,
            )
            for context in contexts
        }
        actual_sessions = {
            tuple(str(value) for value in row)
            for row in connection.execute(
                """SELECT strategy_id,strategy_version,symbol,release_commit
                     FROM control.live_arm_sessions
                    WHERE state='ACTIVE'
                    ORDER BY strategy_id,symbol"""
            ).fetchall()
        }
        if actual_sessions != expected_sessions:
            raise ValueError("P90_REAL_OWNER_ARM_STAGE_SESSION_DRIFT")

        for context in contexts:
            full = evaluate_live_arm(
                connection,
                context=context,
                now=current,
                require_owner_approval=True,
            )
            if not full.ready:
                raise ValueError(
                    "P90_REAL_OWNER_ARM_FINAL_EVIDENCE_BLOCKED:"
                    + context.strategy_id + ":" + context.symbol + ":"
                    + ",".join(full.failed_codes)
                )
            binding, account, connector = routes[context.strategy_id]
            account_ready, reasons = account_live_arm_ready(
                connection,
                strategy_activation_id=context.strategy_activation_id,
                strategy_execution_binding_id=binding,
                trading_account_id=account,
                account_connector_id=connector,
                strategy_id=context.strategy_id,
                strategy_version=context.strategy_version,
                strategy_config_fingerprint=context.strategy_config_fingerprint,
                symbol=context.symbol,
                release_commit=release_commit,
                now=current,
            )
            if not account_ready:
                raise ValueError(
                    "P90_REAL_ACCOUNT_ARM_READBACK_FAILED:"
                    + context.symbol + ":" + ",".join(reasons)
                )

        fresh_after = current - timedelta(seconds=8)
        for account in sorted({route[1] for route in routes.values()}):
            health = connection.execute(
                """SELECT release_commit,state,detail,observed_at
                     FROM runtime.entry_consumer_health
                    WHERE trading_account_id=%s""",
                (account,),
            ).fetchone()
            if health is None:
                raise ValueError(
                    "P90_REAL_ENTRY_CONSUMER_HEALTH_MISSING:" + account
                )
            observed = health[3]
            if (
                str(health[0]) != release_commit
                or str(health[1]) != "READY"
                or not isinstance(observed, datetime)
                or observed.tzinfo is None
                or observed.astimezone(UTC) < fresh_after
            ):
                raise ValueError(
                    "P90_REAL_ENTRY_CONSUMER_NOT_READY:"
                    + account + ":" + str(health[2])
                )

        if connection.execute(
            """SELECT count(*) FROM control.release_arm_incidents
                WHERE release_commit=%s AND state='OPEN'""",
            (release_commit,),
        ).fetchone() != (0,):
            raise ValueError("P90_REAL_OPEN_RUNTIME_INCIDENT")

        changed = connection.execute(
            """UPDATE control.execution_gates
                  SET enabled=1,reason=%s,updated_at_epoch_ms=%s
                WHERE mode='mainnet' AND enabled=0 AND reason=%s
                RETURNING enabled""",
            (
                OWNER_ARM_OPEN_GATE_REASON,
                int(current.timestamp() * 1000),
                OWNER_ARM_STAGE_GATE_REASON,
            ),
        ).fetchone()
        if changed not in ((1,), (True,)):
            raise ValueError("P90_REAL_MAINNET_GATE_ENABLE_FAILED")

        connection.execute(
            """INSERT INTO control.execution_gate_events(
                   at_epoch_ms,mode,previous_enabled,requested_enabled,
                   resulting_enabled,reason,source,origin,request_id,
                   settings_version)
               VALUES(%s,'mainnet',false,true,true,%s,%s,'owner',%s,%s)""",
            (
                int(current.timestamp() * 1000),
                OWNER_ARM_OPEN_GATE_REASON,
                source,
                request_id,
                VERSION,
            ),
        )

    return {
        "status": "ARMED",
        "strategies": 2,
        "instrument_scopes": 5,
        "accounts": 2,
        "execution_permissions": 2,
        "gate_enabled": True,
        "exchange_mutation": False,
    }
