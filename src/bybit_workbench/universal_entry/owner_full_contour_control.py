"""Control-plane state for the owner full-contour REAL diagnostic.

Dashboard may call request_run() only after its existing authenticated owner
boundary.  This module creates a durable trigger and temporary diagnostic
execution rights; it never creates StrategySignal/EntryDecision/ExecutionRequest.

The account-scoped Universal Entry consumer later consumes REQUESTED and owns
normal Strategy/Entry lifecycle creation.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .fingerprint import fingerprint
from .owner_full_contour_diagnostic import (
    RUNTIME_SOURCE,
    VERSION,
    strategy_id_for_symbol,
    strategy_scope,
)

P90_VERSION = "2.0-p90l90-real-main-sub-20261028"
P90_MAIN_ID = "p90l90_legacy_main_ltc_inj"
P90_SUB_ID = "p90l90_legacy_sub_dot_arb_apt"
ACTIVE_STATES = (
    "REQUESTED",
    "MATERIALIZED",
    "DISPATCHED",
    "POSITION_OPEN",
    "RECONCILIATION_REQUIRED",
)


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row[key]
    return row[index]


def _count_is_one(row: Any) -> bool:
    return row is not None and int(_row_value(row, "count", 0)) == 1


def _parent_scope(symbol: str) -> tuple[str, str, str]:
    if symbol.upper() in {"LTCUSDT", "INJUSDT"}:
        return (
            P90_MAIN_ID,
            "activation-p90l90-real-main-20261029-v3",
            "binding-p90l90-real-main-20261029-v3",
        )
    return (
        P90_SUB_ID,
        "activation-p90l90-real-sub-20261029-v3",
        "binding-p90l90-real-sub-20261029-v3",
    )


def _run_id(request_id: str) -> str:
    return "owner-fc-" + fingerprint({"request_id": request_id})[:32]


def live_session_id(run_id: str) -> str:
    return "owner-fc-live-" + fingerprint({"run_id": run_id})[:32]


def account_session_id(run_id: str) -> str:
    return "owner-fc-account-" + fingerprint({"run_id": run_id})[:32]


def require_parent_p90_owner_scope(
    connection: Any,
    *,
    symbol: str,
    account_id: str,
    connector_id: str,
    release_commit: str,
) -> None:
    parent, parent_activation, parent_binding = _parent_scope(symbol)
    binding = connection.execute(
        """SELECT count(*)
             FROM strategy_entry.strategy_execution_bindings b
             JOIN strategy_entry.strategy_activations a
               ON a.activation_id=b.strategy_activation_id
            WHERE b.strategy_execution_binding_id=%s
              AND b.strategy_activation_id=%s
              AND b.trading_account_id=%s
              AND b.execution_leg='PRIMARY'
              AND b.execution_role='PRIMARY'
              AND b.allocation_mode='DEDICATED_STRATEGY'
              AND b.binding_state='ACTIVE'
              AND a.strategy_id=%s AND a.strategy_version=%s
              AND a.enabled=true AND a.disabled_at IS NULL""",
        (parent_binding, parent_activation, account_id, parent, P90_VERSION),
    ).fetchone()
    if not _count_is_one(binding):
        raise ValueError("OWNER_FULL_CONTOUR_PARENT_P90_BINDING_NOT_ACTIVE")
    permission = connection.execute(
        """SELECT count(*)
             FROM strategy_entry.execution_permissions
            WHERE strategy_id=%s AND strategy_version=%s AND enabled=true""",
        (parent, P90_VERSION),
    ).fetchone()
    if not _count_is_one(permission):
        raise ValueError("OWNER_FULL_CONTOUR_PARENT_P90_PERMISSION_NOT_ACTIVE")
    generic = connection.execute(
        """SELECT count(*)
             FROM control.live_arm_sessions
            WHERE strategy_id=%s AND strategy_version=%s
              AND symbol=%s AND release_commit=%s AND state='ACTIVE'""",
        (parent, P90_VERSION, symbol, release_commit),
    ).fetchone()
    if not _count_is_one(generic):
        raise ValueError("OWNER_FULL_CONTOUR_PARENT_P90_ARM_NOT_ACTIVE")
    account = connection.execute(
        """SELECT count(*)
             FROM control.account_live_arm_sessions
            WHERE strategy_id=%s AND strategy_version=%s
              AND strategy_activation_id=%s
              AND strategy_execution_binding_id=%s
              AND symbol=%s AND release_commit=%s
              AND trading_account_id=%s AND account_connector_id=%s
              AND state='ACTIVE' AND deactivated_at IS NULL""",
        (
            parent,
            P90_VERSION,
            parent_activation,
            parent_binding,
            symbol,
            release_commit,
            account_id,
            connector_id,
        ),
    ).fetchone()
    if not _count_is_one(account):
        raise ValueError("OWNER_FULL_CONTOUR_PARENT_P90_ACCOUNT_ARM_NOT_ACTIVE")


def _require_symbol_idle(
    connection: Any,
    *,
    symbol: str,
    account_id: str,
) -> None:
    position = connection.execute(
        """SELECT count(*) FROM runtime.hot_positions
            WHERE trading_account_id=%s AND symbol=%s
              AND identity_status='PROVEN'
              AND nullif(size,'')::numeric<>0""",
        (account_id, symbol),
    ).fetchone()
    if position != (0,):
        raise ValueError("OWNER_FULL_CONTOUR_SYMBOL_POSITION_OCCUPIED")
    ownership = connection.execute(
        """SELECT count(*) FROM runtime.position_ownership
            WHERE trading_account_id=%s AND symbol=%s
              AND identity_status='PROVEN'
              AND state IN ('OPEN','RECONCILIATION_REQUIRED')""",
        (account_id, symbol),
    ).fetchone()
    if ownership != (0,):
        raise ValueError("OWNER_FULL_CONTOUR_SYMBOL_OWNERSHIP_OCCUPIED")
    orders = connection.execute(
        """SELECT count(*) FROM runtime.hot_orders
            WHERE trading_account_id=%s AND symbol=%s
              AND identity_status='PROVEN'
              AND order_status IN ('New','PartiallyFilled','Untriggered')""",
        (account_id, symbol),
    ).fetchone()
    if orders != (0,):
        raise ValueError("OWNER_FULL_CONTOUR_SYMBOL_ORDER_OCCUPIED")


def request_run(
    connection: Any,
    *,
    request_id: str,
    symbol: str,
    direction: str,
    operator: str,
    release_commit: str,
    now: datetime,
) -> dict[str, str]:
    current = now.astimezone(UTC)
    selected_symbol = symbol.upper().strip()
    selected_direction = direction.upper().strip()
    if (
        not request_id.strip()
        or selected_direction not in {"LONG", "SHORT"}
        or len(release_commit) != 40
        or any(ch not in "0123456789abcdef" for ch in release_commit)
    ):
        raise ValueError("OWNER_FULL_CONTOUR_REQUEST_INVALID")
    strategy_id = strategy_id_for_symbol(selected_symbol)
    symbols, activation_id, binding_id, account_id, connector_id = strategy_scope(strategy_id)
    if selected_symbol not in symbols:
        raise ValueError("OWNER_FULL_CONTOUR_SYMBOL_SCOPE_INVALID")
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    if gate != (True,):
        raise ValueError("OWNER_FULL_CONTOUR_MAINNET_GATE_NOT_OPEN")

    require_parent_p90_owner_scope(
        connection,
        symbol=selected_symbol,
        account_id=account_id,
        connector_id=connector_id,
        release_commit=release_commit,
    )
    _require_symbol_idle(connection, symbol=selected_symbol, account_id=account_id)

    if connection.execute(
        """SELECT count(*) FROM control.owner_full_contour_diagnostic_runs
            WHERE state=ANY(%s)""",
        (list(ACTIVE_STATES),),
    ).fetchone() != (0,):
        raise ValueError("OWNER_FULL_CONTOUR_ANOTHER_RUN_ACTIVE")

    strategy = connection.execute(
        """SELECT a.strategy_config_fingerprint,b.binding_state
             FROM strategy_entry.strategy_activations a
             JOIN strategy_entry.strategy_execution_bindings b
               ON b.strategy_activation_id=a.activation_id
            WHERE a.activation_id=%s AND a.strategy_id=%s
              AND a.strategy_version=%s AND a.enabled=true
              AND b.strategy_execution_binding_id=%s
              AND b.trading_account_id=%s
              AND b.binding_state='ACTIVE'""",
        (activation_id, strategy_id, VERSION, binding_id, account_id),
    ).fetchone()
    if strategy is None:
        raise ValueError("OWNER_FULL_CONTOUR_CAPABILITY_NOT_INSTALLED")
    strategy_fingerprint = str(_row_value(strategy, "strategy_config_fingerprint", 0))

    run_id = _run_id(request_id)
    generic_session = live_session_id(run_id)
    scoped_session = account_session_id(run_id)
    source = f"dashboard:owner-full-contour-diagnostic:{run_id}"

    connection.execute(
        """INSERT INTO control.owner_full_contour_diagnostic_runs(
             run_id,requested_at,requested_by,source,symbol,direction,
             strategy_id,strategy_version,strategy_config_fingerprint,
             strategy_activation_id,strategy_execution_binding_id,
             trading_account_id,account_connector_id,release_commit,state)
           VALUES(%s,%s,%s,'dashboard:owner-full-contour-diagnostic',
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'REQUESTED')""",
        (
            run_id,
            current,
            operator,
            selected_symbol,
            selected_direction,
            strategy_id,
            VERSION,
            strategy_fingerprint,
            activation_id,
            binding_id,
            account_id,
            connector_id,
            release_commit,
        ),
    )

    permission = connection.execute(
        """SELECT execution_permission_id,enabled
             FROM strategy_entry.execution_permissions
            WHERE strategy_id=%s AND strategy_version=%s
              AND strategy_config_fingerprint=%s
            FOR UPDATE""",
        (strategy_id, VERSION, strategy_fingerprint),
    ).fetchone()
    if permission is None:
        permission_id = "execperm-" + fingerprint(
            {
                "strategy_id": strategy_id,
                "strategy_version": VERSION,
                "strategy_config_fingerprint": strategy_fingerprint,
            }
        )[:32]
        connection.execute(
            """INSERT INTO strategy_entry.execution_permissions(
                 execution_permission_id,strategy_id,strategy_version,
                 strategy_config_fingerprint,enabled,enabled_at,disabled_at,
                 operator,source,change_reason)
               VALUES(%s,%s,%s,%s,true,%s,NULL,%s,%s,%s)""",
            (
                permission_id,
                strategy_id,
                VERSION,
                strategy_fingerprint,
                current,
                operator,
                source,
                f"owner full-contour diagnostic run {run_id}",
            ),
        )
    else:
        if bool(_row_value(permission, "enabled", 1)):
            raise ValueError("OWNER_FULL_CONTOUR_PERMISSION_ALREADY_ACTIVE")
        connection.execute(
            """UPDATE strategy_entry.execution_permissions
                  SET enabled=true,enabled_at=%s,disabled_at=NULL,
                      operator=%s,source=%s,change_reason=%s
                WHERE execution_permission_id=%s""",
            (
                current,
                operator,
                source,
                f"owner full-contour diagnostic run {run_id}",
                str(_row_value(permission, "execution_permission_id", 0)),
            ),
        )

    connection.execute(
        """INSERT INTO control.live_arm_sessions(
             live_arm_session_id,strategy_id,strategy_version,
             strategy_config_fingerprint,strategy_activation_id,
             symbol,release_commit,state,owner_approved_at,
             activated_at,deactivated_at,source)
           VALUES(%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s,NULL,%s)""",
        (
            generic_session,
            strategy_id,
            VERSION,
            strategy_fingerprint,
            activation_id,
            selected_symbol,
            release_commit,
            current,
            current,
            source,
        ),
    )
    result = connection.execute(
        """SELECT control.arm_owner_full_contour_account_scope(
             %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            scoped_session,
            generic_session,
            binding_id,
            account_id,
            connector_id,
            strategy_id,
            strategy_fingerprint,
            activation_id,
            selected_symbol,
            release_commit,
            current,
            source,
        ),
    ).fetchone()
    if result != (scoped_session,):
        raise ValueError("OWNER_FULL_CONTOUR_ACCOUNT_ARM_WRITE_FAILED")
    return {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "trading_account_id": account_id,
        "symbol": selected_symbol,
        "direction": selected_direction,
        "state": "REQUESTED",
    }


def retire_entry_rights(
    connection: Any,
    *,
    run_id: str,
    at: datetime,
    reason: str,
) -> None:
    current = at.astimezone(UTC)
    row = connection.execute(
        """SELECT strategy_id,strategy_version,strategy_config_fingerprint
             FROM control.owner_full_contour_diagnostic_runs
            WHERE run_id=%s FOR UPDATE""",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError("OWNER_FULL_CONTOUR_RUN_NOT_FOUND")
    strategy_id = str(_row_value(row, "strategy_id", 0))
    strategy_version = str(_row_value(row, "strategy_version", 1))
    strategy_fingerprint = str(_row_value(row, "strategy_config_fingerprint", 2))
    connection.execute(
        """UPDATE strategy_entry.execution_permissions
              SET enabled=false,disabled_at=%s,
                  operator='owner-diagnostic-runtime',
                  source=%s,change_reason=%s
            WHERE strategy_id=%s AND strategy_version=%s
              AND strategy_config_fingerprint=%s AND enabled=true""",
        (
            current,
            f"{RUNTIME_SOURCE}:{run_id}",
            reason,
            strategy_id,
            strategy_version,
            strategy_fingerprint,
        ),
    )
    connection.execute(
        """UPDATE control.live_arm_sessions
              SET state='CLOSED',deactivated_at=%s
            WHERE live_arm_session_id=%s AND state='ACTIVE'""",
        (current, live_session_id(run_id)),
    )
    active_account = connection.execute(
        """SELECT count(*) AS active_count
             FROM control.account_live_arm_sessions
            WHERE account_live_arm_session_id=%s AND state='ACTIVE'""",
        (account_session_id(run_id),),
    ).fetchone()
    if (
        active_account is not None
        and int(_row_value(active_account, "active_count", 0)) == 1
    ):
        result = connection.execute(
            """SELECT control.disarm_owner_full_contour_account_scope(%s,%s,%s)""",
            (
                account_session_id(run_id),
                current,
                f"{RUNTIME_SOURCE}:{run_id}",
            ),
        ).fetchone()
        if result != (account_session_id(run_id),):
            raise RuntimeError("OWNER_FULL_CONTOUR_ACCOUNT_DISARM_FAILED")


def set_run_state(
    connection: Any,
    *,
    run_id: str,
    state: str,
    now: datetime,
    error: str | None = None,
    lineage: dict[str, str | None] | None = None,
) -> None:
    allowed = {
        "MATERIALIZED", "DISPATCHED", "POSITION_OPEN",
        "CLOSED", "FAILED", "RECONCILIATION_REQUIRED",
    }
    if state not in allowed:
        raise ValueError("OWNER_FULL_CONTOUR_RUN_STATE_INVALID")
    values = dict(lineage or {})
    connection.execute(
        """UPDATE control.owner_full_contour_diagnostic_runs
              SET state=%s,
                  signal_id=coalesce(%s,signal_id),
                  strategy_attempt_id=coalesce(%s,strategy_attempt_id),
                  entry_decision_id=coalesce(%s,entry_decision_id),
                  entry_execution_request_id=coalesce(%s,entry_execution_request_id),
                  entry_command_id=coalesce(%s,entry_command_id),
                  strategy_position_id=coalesce(%s,strategy_position_id),
                  error=%s,
                  completed_at=CASE WHEN %s IN ('CLOSED','FAILED') THEN %s ELSE NULL END,
                  updated_at=clock_timestamp()
            WHERE run_id=%s""",
        (
            state,
            values.get("signal_id"),
            values.get("strategy_attempt_id"),
            values.get("entry_decision_id"),
            values.get("entry_execution_request_id"),
            values.get("entry_command_id"),
            values.get("strategy_position_id"),
            error,
            state,
            now.astimezone(UTC),
            run_id,
        ),
    )
