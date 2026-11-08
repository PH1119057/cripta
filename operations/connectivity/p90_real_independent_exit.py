"""Independent P90 REAL protected-position -> Universal Exit consumer.

This process NEVER opens an Entry or calls a Bybit POST. It discovers durable
protected OPEN positions by position identity, uses the original MarketSource
and independently signed MAIN/SUB private-GET proof, then invokes the existing
Universal Exit Engine and account-local Execution request publisher.

Only an immediate, still-proven first-fill source window is eligible for the
existing TRAIL035 activePrice. Old/missing-history positions stay BLOCKED;
they are not retroactively armed. No Strategy activation/Entry permission is
created by this consumer.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any

from psycopg.rows import tuple_row

from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_account_routes import (
    read_p90_real_account_routes,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.p90l90_real_source_compatibility import (
    inspect_real_source_identity,
)
from bybit_workbench.universal_entry.runtime_loader import load_active_strategy_bundles
from bybit_workbench.universal_exit.contracts import ExitEvaluationStatus
from bybit_workbench.universal_exit.loader import load_exit_binding
from bybit_workbench.universal_exit.p90l90_real_exit_execution import (
    P90RealExitExecutionPort,
)
from bybit_workbench.universal_exit.p90l90_real_pending import p90_real_exit_no_pending
from bybit_workbench.universal_exit.p90l90_real_postfill_port import (
    P90RealPostfillExitPort,
)
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateProofClient,
)
from bybit_workbench.universal_exit.storage import PostgresExitShadowStore

# One credential-free import, independent of whether the consumer was
# launched as an installed script or imported as a package in CI.

read_real_postfill_source_window = import_module(
    (str(__package__) + "." if __package__ else "")
    + "p90l90_real_postfill_source_client"
).read_real_postfill_source_window

# A first-fill arm is intentionally not a historical repair operation.
# Source broker has its own stricter protection/clock/coverage deadlines.
_FIRST_FILL_WINDOW = timedelta(seconds=15)


class P90IndependentExitBlocked(RuntimeError):
    """Proof missing: keep the OPEN position and its existing SL/TP intact."""


def _candidate(connection: Any) -> dict[str, object] | None:
    return connection.execute(
        """SELECT p.position_id,p.fill_at,p.initial_protection_confirmed_at,
                  p.trading_account_id,p.symbol
             FROM runtime.position_ownership p
            WHERE p.strategy_version=%s AND p.state='OPEN'
              AND p.identity_status='PROVEN'
              AND p.initial_protection_confirmed_at IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM strategy_exit.exit_decisions d
                   WHERE d.strategy_position_id=p.position_id
                     AND d.action_kind='SET_TRAILING'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM runtime.trade_commands c
                   WHERE c.command_type='strategy_exit'
                     AND c.payload_json::jsonb->>'strategy_position_id'=p.position_id
              )
            ORDER BY p.initial_protection_confirmed_at DESC,p.position_id
            LIMIT 1""",
        (VERSION,),
    ).fetchone()



# Existing connection_events is the durable safety/incident audit; a proof
# failure must not disappear merely because a separate Exit consumer polls.
_FIRSTFILL_SUCCESS = frozenset({
    "P90_NO_PROTECTED_POSITION_REQUIRING_TRAIL",
    "P90_EXIT_TRAIL035_REQUEST_PUBLISHED",
    "P90_EXIT_NO_MUTATION_PROVED",
})
_FIRSTFILL_RESULT_TOKEN = re.compile(r"P90_[A-Za-z0-9_]+(?::[A-Za-z0-9_]+){0,2}\Z")
_FIRSTFILL_PROOF_TOKEN = re.compile(r"P90_[A-Z0-9_]{1,120}\Z")


def safe_firstfill_block_reason(exc: Exception) -> str:
    """Only verified code tokens may leave a private/account exception.

    Never persist raw signed responses, endpoint messages or credentials.
    """
    name = type(exc).__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        name = "UnknownException"
    code = str(exc)
    if _FIRSTFILL_PROOF_TOKEN.fullmatch(code):
        return f"P90_EXIT_PROOF_BLOCKED:{name}:{code}"
    return f"P90_EXIT_PROOF_BLOCKED:{name}"


def audit_firstfill_exit_result(
    connection: Any, *, result: str, observed_at: datetime,
) -> None:
    """Durable exact first-fill denial; never grant or revoke any permission.

    The already proven candidate is re-read under the existing Exit connection;
    one record per changed verdict and position survives process restarts.
    """
    if result in _FIRSTFILL_SUCCESS:
        return
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise RuntimeError("P90_EXIT_AUDIT_TIME_NOT_UTC")
    candidate = _candidate(connection)
    if candidate is None:
        # A filled TP may close the position between proof failure and audit.
        return
    account = candidate.get("trading_account_id")
    position_id = candidate.get("position_id")
    symbol = candidate.get("symbol")
    if not all(isinstance(v, str) and v for v in (account, position_id, symbol)):
        raise RuntimeError("P90_EXIT_AUDIT_IDENTITY_NOT_PROVEN")
    code = result if _FIRSTFILL_RESULT_TOKEN.fullmatch(result) else (
        "P90_EXIT_PROOF_BLOCKED:UNCLASSIFIED_STATUS"
    )
    row = connection.execute(
        """SELECT details_json::jsonb->>'reason_code' AS reason_code
             FROM runtime.connection_events
            WHERE channel='exit' AND event='P90_FIRST_FILL_EXIT_UNVERIFIED'
              AND trading_account_id=%s
              AND details_json::jsonb->>'position_id'=%s
            ORDER BY at_epoch_ms DESC,id DESC LIMIT 1""",
        (account, position_id),
    ).fetchone()
    if row is not None and row["reason_code"] == code:
        return
    protected_at = candidate["initial_protection_confirmed_at"]
    connection.execute(
        """INSERT INTO runtime.connection_events(
               at_epoch_ms,channel,event,details_json,
               trading_account_id,identity_status)
           VALUES(%s,'exit','P90_FIRST_FILL_EXIT_UNVERIFIED',%s,%s,'PROVEN')""",
        (
            int(observed_at.timestamp() * 1000),
            json.dumps({
                "position_id": position_id,
                "symbol": symbol,
                "reason_code": code,
                "protected_at": protected_at.isoformat(),
                "checked_at": observed_at.isoformat(),
                "exchange_mutation": False,
            }, ensure_ascii=False),
            account,
        ),
    )


def _validate_first_fill_age(
    *, fill_at: datetime, protected_at: datetime, now: datetime,
) -> None:
    if (
        fill_at.tzinfo is None or protected_at.tzinfo is None
        or now.tzinfo is None
        or protected_at < fill_at
        or not timedelta() <= now - protected_at <= _FIRST_FILL_WINDOW
    ):
        raise P90IndependentExitBlocked(
            "P90_REAL_EXIT_FIRST_FILL_ORIGINAL_SOURCE_WINDOW_EXPIRED"
        )


def run_p90_exit_once(
    connection: Any, *, now: datetime | None = None,
) -> str:
    """Bounded independent Exit cycle; no new Entry, credentials or REST POST."""
    started = (now or datetime.now(UTC)).astimezone(UTC)
    candidate = _candidate(connection)
    if candidate is None:
        return "P90_NO_PROTECTED_POSITION_REQUIRING_TRAIL"
    position_id = str(candidate["position_id"])
    try:
        _validate_first_fill_age(
            fill_at=candidate["fill_at"],
            protected_at=candidate["initial_protection_confirmed_at"],
            now=started,
        )
    except P90IndependentExitBlocked as exc:
        # Historical OPEN APT must NEVER receive a stale activePrice. Its
        # missing source/MFE is a separate owner-decision recovery incident.
        return str(exc)

    try:
        # This short outer transaction is the sole publisher's xact. The
        # source adapter and signed private client supply proofs, not writes.
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (position_id,),
            )
            position, plan = load_exit_binding(
                connection, strategy_position_id=position_id,
            )
            _validate_first_fill_age(
                fill_at=position.fill_at,
                protected_at=position.initial_protection_confirmed_at,
                now=datetime.now(UTC),
            )
            with connection.cursor(row_factory=tuple_row) as evidence_db:
                bundles = tuple(
                    item for item in load_active_strategy_bundles(evidence_db)
                    if item.card.strategy_version == VERSION
                )
                routes = read_p90_real_account_routes(
                    evidence_db, bundles=bundles, at=datetime.now(UTC),
                )
                route = (
                    routes.main if position.strategy_id == MAIN_ID
                    else routes.sub if position.strategy_id == SUB_ID else None
                )
                if (
                    route is None
                    or route.trading_account_id != position.trading_account_id
                    or route.strategy_execution_binding_id
                    != position.strategy_execution_binding_id
                    or route.activation_id != position.strategy_activation_id
                ):
                    raise P90IndependentExitBlocked(
                        "P90_REAL_EXIT_SIGNED_ACCOUNT_ROUTE_UNVERIFIED"
                    )
                epoch_rows = evidence_db.execute(
                    """SELECT epoch_id FROM strategy_entry.p90l90_real_source_epochs
                        WHERE status='ACTIVE'"""
                ).fetchall()
                if len(epoch_rows) != 1:
                    raise P90IndependentExitBlocked(
                        "P90_REAL_EXIT_ORIGINAL_SOURCE_EPOCH_UNVERIFIED"
                    )
                epoch_id = str(epoch_rows[0][0])
                loaded_commit = os.environ.get(
                    "CRIPTA_RELEASE_COMMIT", ""
                ).strip().lower()
                identity = inspect_real_source_identity(
                    evidence_db, epoch_id=epoch_id,
                    loaded_commit=loaded_commit, bundles=bundles,
                )
            evidence = connection.execute(
                """SELECT initial_protection_evidence
                     FROM runtime.position_ownership
                    WHERE position_id=%s AND state='OPEN'
                      AND initial_protection_confirmed_at IS NOT NULL
                    FOR UPDATE""",
                (position_id,),
            ).fetchone()
            if evidence is None or not isinstance(
                evidence["initial_protection_evidence"], dict
            ):
                raise P90IndependentExitBlocked(
                    "P90_REAL_EXIT_FIRST_FILL_PROTECTION_PROOF_MISSING"
                )
            initial = evidence["initial_protection_evidence"]
            order_id = initial.get("entry_order_id")
            fill_ids = initial.get("entry_execution_ids")
            if (
                initial.get("trading_account_id") != position.trading_account_id
                or initial.get("symbol") != position.symbol
                or not isinstance(order_id, str) or not order_id
                or not isinstance(fill_ids, list) or not fill_ids
                or any(not isinstance(value, str) or not value for value in fill_ids)
                or len(fill_ids) != len(set(fill_ids))
                or not p90_real_exit_no_pending(
                    connection, position=position,
                    trading_account_id=route.trading_account_id,
                )
            ):
                raise P90IndependentExitBlocked(
                    "P90_REAL_EXIT_FIRST_FILL_LINEAGE_OR_MUTATION_UNVERIFIED"
                )
            coverage = read_real_postfill_source_window(
                position=position, source_epoch_id=epoch_id,
                source_commit=identity.origin_commit,
            )
            private_client = P90PrivateProofClient(
                account=route.trading_account_id,
                connector=route.account_connector_id,
                timeout_seconds=3.0,
            )
            private = private_client.for_closed_boundary(
                position, coverage.covered_through,
            )
            if private.signed_provider_uid != route.registered_exchange_uid:
                raise P90IndependentExitBlocked(
                    "P90_REAL_EXIT_PRIVATE_SIGNED_UID_MISMATCH"
                )

            def position_provider(symbol: str) -> tuple[object, ...]:
                if symbol != position.symbol:
                    return ()
                current, current_plan = load_exit_binding(
                    connection, strategy_position_id=position_id,
                )
                return ((current, current_plan),)

            def safety(subject: object, evaluation: object, checked_at: datetime) -> bool:
                decision = getattr(evaluation, "decision", None)
                if (
                    subject != position or decision is None
                    or datetime.now(UTC) - checked_at > timedelta(seconds=5)
                    or not p90_real_exit_no_pending(
                        connection, position=position,
                        trading_account_id=route.trading_account_id,
                        publishing_exit_decision_id=decision.exit_decision_id,
                    )
                ):
                    return False
                # Account-local private producer independently re-GETs Bybit;
                # failure or any changed protection prohibits publication.
                try:
                    again = private_client.for_closed_boundary(
                        position, coverage.covered_through,
                    )
                except Exception:
                    return False
                return (
                    again.signed_provider_uid == private.signed_provider_uid
                    and again.trading_account_id == private.trading_account_id
                    and again.strategy_execution_binding_id
                        == private.strategy_execution_binding_id
                    and again.exchange_position_key == private.exchange_position_key
                    and again.pending_exit_mutation is False
                    and again.observed_at >= private.observed_at
                    and again.position == private.position
                )

            execution = P90RealExitExecutionPort(
                connection,
                independently_verified_execution_safety=safety,
                account_by_strategy=routes.account_by_strategy,
            )
            result = P90RealPostfillExitPort(
                connection,
                position_provider=position_provider,
                exit_store=PostgresExitShadowStore(connection),
                execution_port=execution,
            )(
                position=position, plan=plan, private=private,
                coverage=coverage, signed_fill_execution_ids=tuple(fill_ids),
                exact_entry_order_id=order_id,
                received_at=max(datetime.now(UTC), private.observed_at),
            )
            if result.status is ExitEvaluationStatus.DECISION_CREATED:
                return "P90_EXIT_TRAIL035_REQUEST_PUBLISHED"
            return "P90_EXIT_NO_MUTATION_PROVED"
    except Exception as exc:
        # Caller must roll back if its transaction is left aborted. Do not
        # manufacture Exchange evidence or turn an unknown failure into PASS.
        if isinstance(exc, P90IndependentExitBlocked):
            return str(exc)
        return safe_firstfill_block_reason(exc)
