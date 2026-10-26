"""Strategy financial-account choice resolves to an execution binding, not policy.

AccountConnector owns signed private identity proof. This module records only
approved StrategyActivation/ExecutionLeg -> TradingAccount placement. It never
handles credentials, changes StrategyCard, activates a Strategy or arms LIVE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class FinancialAccountChoice:
    trading_account_id: str
    account_connector_id: str
    ready: bool
    reasons: tuple[str, ...]


def inspect_financial_account_choice(
    connection: Any, *, trading_account_id: str, account_connector_id: str
) -> FinancialAccountChoice:
    """Account-registration preflight for Strategy financial account selection.

    This is not a substitute for an authenticated credential-mounted probe.
    Selection is blocked if the exact UID/connector is not registered.
    """
    reasons: set[str] = set()
    if not trading_account_id or not account_connector_id:
        reasons.add("FINANCIAL_ACCOUNT_CHOICE_MISSING")
        return FinancialAccountChoice(
            trading_account_id, account_connector_id, False, tuple(sorted(reasons))
        )
    rows = connection.execute(
        """SELECT a.venue_id,a.environment,a.status,a.account_kind,
                  a.account_type,a.external_account_ref,
                  a.metadata->>'uid_registration_status',
                  c.trading_account_id,c.connector_kind,c.adapter_id,
                  c.status,c.credential_ref,c.retired_at
             FROM identity.trading_accounts a
             JOIN identity.account_connectors c
               ON c.trading_account_id=a.trading_account_id
            WHERE a.trading_account_id=%s AND c.account_connector_id=%s""",
        (trading_account_id, account_connector_id),
    ).fetchall()
    if len(rows) != 1:
        reasons.add("FINANCIAL_ACCOUNT_CONNECTOR_NOT_UNIQUE")
    else:
        (venue, environment, state, kind, account_type, uid, uid_status,
         connector_account, connector_kind, adapter, connector_state,
         credential_ref, retired) = rows[0]
        if (
            venue != "venue-bybit" or environment != "MAINNET"
            or state != "ACTIVE" or kind not in ("MAIN", "SUB")
            or account_type != "UNIFIED"
        ):
            reasons.add("FINANCIAL_ACCOUNT_NOT_ACTIVE_BYBIT_UNIFIED")
        registered_uid = str(uid or "")
        if (
            not registered_uid or not registered_uid.isascii()
            or not registered_uid.isdecimal() or int(registered_uid) <= 0
        ):
            reasons.add("FINANCIAL_ACCOUNT_PROVIDER_UID_NOT_REGISTERED")
        # An owner-asserted UID is a provisional registry value, not a
        # signed/exchange-authenticated AccountConnector enrollment.
        if uid_status == "OWNER_ASSERTED_PENDING":
            reasons.add("FINANCIAL_ACCOUNT_UID_PENDING_SIGNED_ATTESTATION")
        if (
            connector_account != trading_account_id
            or connector_kind != "CEX_PRIVATE_RUNTIME"
            or adapter != "BYBIT_V5_PRIVATE"
            or connector_state != "ACTIVE" or retired is not None
            or not str(credential_ref or "").strip()
        ):
            reasons.add("FINANCIAL_ACCOUNT_PRIVATE_CONNECTOR_NOT_READY")
    # StrategyExecutionBinding currently stores the stable TradingAccount,
    # not a connector ID. Unless exactly one private connector is usable,
    # choosing a connector cannot be persisted unambiguously.
    active = connection.execute(
        """SELECT account_connector_id FROM identity.account_connectors
            WHERE trading_account_id=%s
              AND connector_kind='CEX_PRIVATE_RUNTIME'
              AND adapter_id='BYBIT_V5_PRIVATE'
              AND status='ACTIVE' AND retired_at IS NULL
              AND nullif(btrim(credential_ref),'') IS NOT NULL""",
        (trading_account_id,),
    ).fetchall()
    if len(active) != 1 or str(active[0][0]) != account_connector_id:
        reasons.add("FINANCIAL_ACCOUNT_CONNECTOR_AMBIGUOUS")
    return FinancialAccountChoice(
        trading_account_id, account_connector_id, not reasons, tuple(sorted(reasons))
    )


def record_financial_account_choice(
    connection: Any,
    *,
    strategy_execution_binding_id: str,
    strategy_activation_id: str,
    execution_leg: str,
    trading_account_id: str,
    account_connector_id: str,
    effective_from: datetime,
    operator: str,
    reason: str,
) -> None:
    """Write a dedicated binding in an already authorized SERIALIZABLE transaction.

    Caller is responsible for authenticating operator and enforcing any current
    CRIPTAUser/owner access boundary. This function never commits. No bind
    switches LIVE on, even when StrategyActivation is enabled separately.
    """
    if not all((
        strategy_execution_binding_id.strip(), strategy_activation_id.strip(),
        execution_leg.strip(), operator.strip(), reason.strip(),
    )):
        raise RuntimeError("FINANCIAL_ACCOUNT_BINDING_INPUT_MISSING")
    # The current producer/consumer supports PRIMARY only. Do not imply
    # multi-leg fan-out until its execution consumers implement exact routing.
    if execution_leg != "PRIMARY":
        raise RuntimeError("FINANCIAL_ACCOUNT_EXECUTION_LEG_UNSUPPORTED")
    if effective_from.tzinfo is None or effective_from.utcoffset() is None:
        raise RuntimeError("FINANCIAL_ACCOUNT_BINDING_TIME_INVALID")
    level = connection.execute("SHOW transaction_isolation").fetchone()
    if level is None or str(level[0]).lower() != "serializable":
        raise RuntimeError("FINANCIAL_ACCOUNT_BINDING_SERIALIZABLE_REQUIRED")
    choice = inspect_financial_account_choice(
        connection, trading_account_id=trading_account_id,
        account_connector_id=account_connector_id,
    )
    if not choice.ready:
        raise RuntimeError("FINANCIAL_ACCOUNT_CHOICE_BLOCKED:" + ",".join(choice.reasons))
    activation = connection.execute(
        """SELECT activation_id FROM strategy_entry.strategy_activations
            WHERE activation_id=%s""",
        (strategy_activation_id,),
    ).fetchall()
    if len(activation) != 1:
        raise RuntimeError("STRATEGY_ACTIVATION_NOT_REGISTERED")
    overlap = connection.execute(
        """SELECT strategy_execution_binding_id
             FROM strategy_entry.strategy_execution_bindings
            WHERE binding_state='ACTIVE'
              AND (effective_to IS NULL OR effective_to>%s)
              AND (trading_account_id=%s OR
                   (strategy_activation_id=%s AND execution_leg=%s))
            LIMIT 1""",
        (effective_from, trading_account_id, strategy_activation_id, execution_leg),
    ).fetchall()
    if overlap:
        raise RuntimeError("FINANCIAL_ACCOUNT_ALREADY_BOUND_OR_LEG_CONFLICT")
    connection.execute(
        """INSERT INTO strategy_entry.strategy_execution_bindings (
            strategy_execution_binding_id,strategy_activation_id,execution_leg,
            execution_role,trading_account_id,allocation_mode,binding_state,
            effective_from,effective_to,operator,source,reason,created_at,updated_at
        ) VALUES (%s,%s,%s,'PRIMARY',%s,'DEDICATED_STRATEGY','ACTIVE',
                  %s,NULL,%s,'OWNER_FINANCIAL_ACCOUNT_SELECTION',%s,%s,%s)""",
        (
            strategy_execution_binding_id, strategy_activation_id, execution_leg,
            trading_account_id, effective_from.astimezone(UTC), operator, reason,
            effective_from.astimezone(UTC), effective_from.astimezone(UTC),
        ),
    )
