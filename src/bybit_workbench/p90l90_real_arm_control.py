"""Explicit two-account OWNER ARM rail for P90L90 REAL v3.

This module does not activate Strategy cards, bindings, or Exchange positions.
Both immutable v3 StrategyActivations must already be enabled and both exact
dedicated MAIN/SUB bindings must already be ACTIVE.  The caller must be the
authenticated Dashboard owner action and must explicitly confirm arm.

One transaction:
  pre-owner §4.7 evidence for all 5 Strategy/symbol scopes
  -> owner approval evidence
  -> generic release-bound live_arm_sessions
  -> narrow SECURITY DEFINER account_live_arm_sessions
  -> exact account-arm readback
  -> 2 execution permissions
  -> global mainnet gate LAST

Disarm reverses permission first/gate closed and never closes open positions.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from .account_live_arm import account_live_arm_ready
from .live_arm_readiness import (
    LiveArmContext,
    active_live_arm_session,
    evaluate_live_arm,
    strategy_symbol_scope_key,
)
from .universal_entry.dashboard_control import StrategyDashboardStore
from .universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from .universal_entry.p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION
from .universal_entry.readiness import assess_strategy_runtime_readiness
from .universal_entry.runtime_loader import load_active_strategy_bundles

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_KIND = {MAIN_ID: "MAIN", SUB_ID: "SUB"}


def _owner_time(*, operator: str, request_id: str, now: datetime) -> datetime:
    if not operator.strip() or operator.upper() == "UNKNOWN":
        raise ValueError("P90_REAL_OWNER_IDENTITY_REQUIRED")
    if len(request_id.strip()) < 8:
        raise ValueError("P90_REAL_OWNER_REQUEST_ID_REQUIRED")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("P90_REAL_OWNER_ACTION_TIME_REQUIRES_UTC")
    return now.astimezone(UTC)


def p90l90_real_v3_contexts_and_routes(
    connection: Any,
    *,
    release_commit: str,
    now: datetime,
) -> tuple[
    tuple[LiveArmContext, ...],
    dict[str, tuple[str, str, str]],
]:
    if _SHA.fullmatch(release_commit) is None:
        raise ValueError("P90_REAL_RELEASE_COMMIT_INVALID")
    bundles = tuple(
        bundle
        for bundle in load_active_strategy_bundles(connection)
        if bundle.card.strategy_id in {MAIN_ID, SUB_ID}
        and bundle.card.strategy_version == VERSION
    )
    if len(bundles) != 2 or {b.card.strategy_id for b in bundles} != {
        MAIN_ID, SUB_ID,
    }:
        raise ValueError("P90_REAL_EXACT_TWO_ACTIVE_STRATEGIES_REQUIRED")

    contexts: list[LiveArmContext] = []
    routes: dict[str, tuple[str, str, str]] = {}
    for bundle in bundles:
        readiness = assess_strategy_runtime_readiness(
            bundle.card, observer_ready=True,
        )
        if not (
            readiness.policy_ready
            and readiness.execution_ready
            and readiness.active_ready
        ):
            raise ValueError(
                "P90_REAL_EXACT_V3_RUNTIME_POLICY_NOT_EXECUTABLE:"
                + ",".join(item.code for item in readiness.reasons)
            )
        expected_symbols = ALLOWED_SYMBOLS[bundle.card.strategy_id]
        if frozenset(bundle.card.symbols) != expected_symbols:
            raise ValueError("P90_REAL_STRATEGY_SYMBOL_SCOPE_DRIFT")

        rows = connection.execute(
            """SELECT b.strategy_execution_binding_id,b.trading_account_id,
                      c.account_connector_id,a.account_kind
                 FROM strategy_entry.strategy_execution_bindings b
                 JOIN identity.trading_accounts a
                   ON a.trading_account_id=b.trading_account_id
                 JOIN identity.account_connectors c
                   ON c.trading_account_id=b.trading_account_id
                WHERE b.strategy_activation_id=%s
                  AND b.execution_leg='PRIMARY'
                  AND b.execution_role='PRIMARY'
                  AND b.allocation_mode='DEDICATED_STRATEGY'
                  AND b.binding_state='ACTIVE'
                  AND b.effective_from<=%s
                  AND (b.effective_to IS NULL OR b.effective_to>%s)
                  AND a.status='ACTIVE' AND a.environment='MAINNET'
                  AND a.venue_id='venue-bybit' AND a.account_type='UNIFIED'
                  AND c.status='ACTIVE' AND c.retired_at IS NULL
                  AND c.connector_kind='CEX_PRIVATE_RUNTIME'
                  AND c.adapter_id='BYBIT_V5_PRIVATE'
                  AND nullif(btrim(c.credential_ref),'') IS NOT NULL""",
            (bundle.activation.activation_id, now, now),
        ).fetchall()
        if len(rows) != 1 or str(rows[0][3]) != _KIND[bundle.card.strategy_id]:
            raise ValueError(
                "P90_REAL_EXACT_ACCOUNT_CONNECTOR_BINDING_REQUIRED:"
                + bundle.card.strategy_id
            )
        binding_id, account_id, connector_id, _kind = (
            str(rows[0][0]), str(rows[0][1]), str(rows[0][2]), str(rows[0][3])
        )
        routes[bundle.card.strategy_id] = (
            binding_id, account_id, connector_id,
        )
        for symbol in sorted(expected_symbols):
            contexts.append(LiveArmContext(
                strategy_id=bundle.card.strategy_id,
                strategy_version=bundle.card.strategy_version,
                strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
                strategy_activation_id=bundle.activation.activation_id,
                symbol=symbol,
                release_commit=release_commit,
            ))
    if len(contexts) != 5:
        raise ValueError("P90_REAL_EXACT_FIVE_LIVE_ARM_SCOPES_REQUIRED")
    return tuple(contexts), routes


def prearm_p90l90_real_v3(
    connection: Any,
    *,
    release_commit: str,
    now: datetime,
    operator: str,
    request_id: str,
    enabled: bool,
) -> dict[str, object]:
    """Atomically start/stop both v3 Strategy watchers with trading rights NONE."""
    current = _owner_time(operator=operator, request_id=request_id, now=now)
    if _SHA.fullmatch(release_commit) is None:
        raise ValueError("P90_REAL_RELEASE_COMMIT_INVALID")
    source = "dashboard:p90l90-real-v3-prearm"
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        if enabled:
            # Static card/readiness is checked against the exact two INACTIVE
            # cards before the narrow SQL writer changes activation state.
            rows = connection.execute(
                """SELECT c.card_json,c.approved_at,c.approved_source
                     FROM strategy_entry.strategy_cards c
                    WHERE c.strategy_version=%s
                      AND c.strategy_id IN (%s,%s)
                    ORDER BY c.strategy_id""",
                (VERSION, MAIN_ID, SUB_ID),
            ).fetchall()
            if len(rows) != 2:
                raise ValueError("P90_REAL_PREARM_EXACT_TWO_CARDS_MISSING")
            from .universal_entry.runtime_loader import strategy_card_from_storage

            for raw, approved_at, approved_source in rows:
                card = strategy_card_from_storage(
                    raw, approved_at=approved_at, approved_source=approved_source,
                )
                readiness = assess_strategy_runtime_readiness(
                    card, observer_ready=True,
                )
                if not readiness.policy_ready or not readiness.execution_ready:
                    raise ValueError(
                        "P90_REAL_PREARM_POLICY_NOT_EXECUTABLE:"
                        + ",".join(item.code for item in readiness.reasons)
                    )
            result = connection.execute(
                "SELECT strategy_entry.prearm_p90l90_real_v3(%s,%s,%s)",
                (current, operator, source),
            ).fetchone()
            if result != (2,):
                raise ValueError("P90_REAL_PREARM_WRITE_FAILED")
            state = "PREARMED_SOURCE_ONLY"
        else:
            result = connection.execute(
                "SELECT strategy_entry.unprearm_p90l90_real_v3(%s,%s,%s)",
                (current, operator, source),
            ).fetchone()
            if result != (2,):
                raise ValueError("P90_REAL_UNPREARM_WRITE_FAILED")
            state = "DISABLED"
    return {
        "status": state,
        "strategies": 2,
        "accounts": 2,
        "execution_permissions": 0,
        "gate_enabled": False,
        "exchange_mutation": False,
    }


def arm_p90l90_real_v3(
    connection: Any,
    *,
    release_commit: str,
    now: datetime,
    operator: str,
    request_id: str,
    confirmed: bool,
    observer_ready: bool,
) -> dict[str, object]:
    """One explicit owner action arms exactly two accounts / five symbols."""
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
            """SELECT count(*) FROM strategy_entry.execution_permissions
                WHERE enabled=true"""
        ).fetchone() != (0,):
            raise ValueError("P90_REAL_OTHER_EXECUTION_PERMISSION_ACTIVE")
        if connection.execute(
            """SELECT count(*) FROM control.live_arm_sessions
                WHERE state='ACTIVE'"""
        ).fetchone() != (0,):
            raise ValueError("P90_REAL_PREVIOUS_OR_UNKNOWN_ARM_SESSION_ACTIVE")
        if connection.execute(
            """SELECT count(*) FROM control.account_live_arm_sessions
                WHERE state='ACTIVE'"""
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
                    evidence_id, scope, current, release_commit, source,
                    json.dumps({
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
                    }, sort_keys=True),
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
                    live_session, context.strategy_id, context.strategy_version,
                    context.strategy_config_fingerprint,
                    context.strategy_activation_id, context.symbol,
                    release_commit, current, current, source,
                ),
            )
            if active_live_arm_session(connection, context=context) != live_session:
                raise ValueError(
                    "P90_REAL_LIVE_ARM_SESSION_READBACK_FAILED:"
                    + context.symbol
                )
            binding, account, connector = routes[context.strategy_id]
            result = connection.execute(
                """SELECT control.arm_p90l90_real_v3_account_scope(
                       %s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    account_session, live_session, binding, account, connector,
                    context.symbol, release_commit, current, source,
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
                reason="owner confirmed independent MAIN/SUB P90 REAL v3",
                observer_ready=True,
            )

        changed = connection.execute(
            """UPDATE control.execution_gates
                  SET enabled=1,
                      reason='P90L90 REAL v3 explicitly armed by owner',
                      updated_at_epoch_ms=%s
                WHERE mode='mainnet' AND enabled=0
                RETURNING enabled""",
            (int(current.timestamp() * 1000),),
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
                "P90L90 REAL v3 explicitly armed by owner",
                source, request_id, VERSION,
            ),
        )
    return {
        "status": "ARMED",
        "strategies": 2,
        "instrument_scopes": 5,
        "accounts": 2,
        "live_session_ids": [item[1] for item in session_rows],
        "account_session_ids": [item[2] for item in session_rows],
        "requested_amount_usdt_per_strategy": "10",
        "aggregate_committed_usdt_max": "20",
        "leverage": 1,
        "gate_enabled": True,
    }


def disarm_p90l90_real_v3(
    connection: Any,
    *,
    now: datetime,
    operator: str,
    request_id: str,
) -> dict[str, object]:
    """Owner kill: no evidence freshness is required and no position is closed."""
    current = _owner_time(operator=operator, request_id=request_id, now=now)
    source = "dashboard:p90l90-real-v3"
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        gate = connection.execute(
            "SELECT enabled FROM control.execution_gates "
            "WHERE mode='mainnet' FOR UPDATE"
        ).fetchone()
        previous = bool(gate and gate[0])
        connection.execute(
            """UPDATE control.execution_gates
                  SET enabled=0,
                      reason='P90L90 REAL v3 stopped by owner',
                      updated_at_epoch_ms=%s
                WHERE mode='mainnet'""",
            (int(current.timestamp() * 1000),),
        )
        connection.execute(
            """UPDATE strategy_entry.execution_permissions
                  SET enabled=false,disabled_at=%s,operator=%s,source=%s,
                      change_reason='P90L90 REAL v3 stopped by owner'
                WHERE enabled=true AND strategy_version=%s
                  AND strategy_id IN (%s,%s)""",
            (current, operator, source, VERSION, MAIN_ID, SUB_ID),
        )
        connection.execute(
            """UPDATE control.live_arm_sessions
                  SET state='CLOSED',deactivated_at=%s,
                      updated_at=clock_timestamp()
                WHERE state='ACTIVE' AND strategy_version=%s
                  AND strategy_id IN (%s,%s)""",
            (current, VERSION, MAIN_ID, SUB_ID),
        )
        result = connection.execute(
            "SELECT control.disarm_p90l90_real_v3_account_scopes(%s)",
            (current,),
        ).fetchone()
        if (
            not isinstance(result, (tuple, list))
            or len(result) != 1
            or type(result[0]) is not int
            or result[0] < 0
        ):
            raise ValueError("P90_REAL_ACCOUNT_DISARM_FAILED")
        connection.execute(
            """INSERT INTO control.execution_gate_events(
                   at_epoch_ms,mode,previous_enabled,requested_enabled,
                   resulting_enabled,reason,source,origin,request_id,
                   settings_version)
               VALUES(%s,'mainnet',%s,false,false,%s,%s,'owner',%s,%s)""",
            (
                int(current.timestamp() * 1000), previous,
                "P90L90 REAL v3 stopped by owner",
                source, request_id, VERSION,
            ),
        )
    return {
        "status": "DISARMED",
        "strategies": 2,
        "accounts": 2,
        "gate_enabled": False,
        "positions_closed": False,
    }
