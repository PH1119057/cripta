"""Persistent, bounded account-scoped P90 TRAIL035 reconciliation (#860).

Only NEW protected fills with immutable per-fill opt-in are considered. Every
cycle reads current Exchange position and fresh original LTP; an old 15-second
first-fill failure never gives trading authority or a fabricated MFE. The
ordinary Universal Exit execution port publishes the sole account-scoped
command, which only credential-local private_runtime may send to Bybit.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from collections.abc import Mapping
from typing import Any

from psycopg.rows import tuple_row

from bybit_workbench.market_data.r10_execution_publication import (
    R10ExecutionPublicationUnavailable,
    read_r10_execution_quote,
)
from bybit_workbench.universal_entry.p90l90_real_account_routes import (
    read_p90_real_account_routes,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.p90l90_real_source_compatibility import (
    inspect_real_source_identity,
)
from bybit_workbench.universal_entry.runtime_loader import (
    load_active_strategy_bundles,
)
from bybit_workbench.universal_exit.contracts import ExitEvaluationStatus
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.loader import load_exit_binding
from bybit_workbench.universal_exit.p90l90_real_exit_execution import (
    P90RealExitExecutionPort,
    lock_p90_exit_account,
)
from bybit_workbench.universal_exit.p90l90_real_pending import (
    p90_real_exit_no_pending,
)
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateProofClient,
)
from bybit_workbench.universal_exit.p90l90_real_trail_reconcile import (
    build_p90_trail_recovery_observation,
)
from bybit_workbench.universal_exit.p90l90_real_v3 import (
    P90_TRAIL_RECOVERY_POLICY,
)
from bybit_workbench.universal_exit.storage import PostgresExitShadowStore

_CODE = re.compile(r"^[A-Z][A-Z0-9_]{5,110}$")


class P90PersistentTrailBlocked(RuntimeError):
    """Exact source, private or account ownership proof not sufficient."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise P90PersistentTrailBlocked("P90_TRAIL_RECOVERY_" + code)


def _candidate(connection: Any) -> dict[str, Any] | None:
    """New-fill marker is an actual financial scope fence, not a rollout guess."""
    return connection.execute(
        """SELECT p.position_id,p.trading_account_id,p.symbol
             FROM runtime.position_ownership p
            WHERE p.strategy_version=%s AND p.state='OPEN'
              AND p.identity_status='PROVEN'
              AND p.initial_protection_confirmed_at IS NOT NULL
              AND p.initial_protection_evidence->>'trailing_recovery_policy'=%s
            ORDER BY p.initial_protection_confirmed_at,p.position_id
            LIMIT 1""",
        (VERSION, P90_TRAIL_RECOVERY_POLICY),
    ).fetchone()


def _source_quote(
    source_connection: Any, *,
    symbol: str,
    observed_at: datetime,
) -> tuple[object, str]:
    """Only one exact original MarketPublication from one read-only PG view."""
    with source_connection.transaction():
        source_connection.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        sessions = source_connection.execute(
            """SELECT source_session_id
                 FROM analytics.native_public_trade_source_sessions
                WHERE state='ACTIVE' ORDER BY subscribed_at DESC LIMIT 2"""
        ).fetchall()
        _require(len(sessions) == 1, "ACTIVE_NATIVE_SOURCE_UNVERIFIED")
        session_id = sessions[0][0]
        quote = read_r10_execution_quote(
            source_connection,
            symbol=symbol,
            observed_at=observed_at,
        )
        _require(
            quote.source_session_id == session_id
            and quote.field_times[0] <= quote.as_of_second
            and timedelta() <= observed_at - quote.as_of_second
            <= timedelta(seconds=2),
            "CURRENT_ORIGINAL_LTP_PUBLICATION_NOT_FRESH",
        )
    return quote, str(session_id)


def _private_snapshot(private: object) -> tuple[object, ...] | None:
    # Stable authenticated protection, never include changing mark/margin/PnL.
    raw = getattr(private, "position", None)
    keys = (
        "symbol", "side", "positionIdx", "size", "avgPrice",
        "stopLoss", "takeProfit", "trailingStop",
    )
    if not isinstance(raw, Mapping) or any(k not in raw for k in keys):
        return None
    return tuple(raw[k] for k in keys)


def run_p90_persistent_trail_once(
    connection: Any,
    source_connection: Any,
    *,
    loaded_commit: str,
    now: datetime | None = None,
) -> str:
    """One current-position recovery attempt, never a REST POST or an ARM."""
    row = _candidate(connection)
    if row is None:
        return "P90_TRAIL_RECOVERY_NO_ELIGIBLE_POSITION"
    at = (now or datetime.now(UTC)).astimezone(UTC)
    position_id = str(row["position_id"])
    account_id = str(row["trading_account_id"])
    symbol = str(row["symbol"])

    # The caller owns this single account transaction, matching the private
    # worker's account-first lock ordering. No pending competing mutation.
    with connection.transaction():
        lock_p90_exit_account(connection, account_id)
        position, plan = load_exit_binding(
            connection, strategy_position_id=position_id,
        )
        _require(
            position.trading_account_id == account_id
            and position.symbol == symbol
            and position.initial_protection_confirmed_at is not None
            and position.strategy_version == VERSION,
            "POSITION_PLAN_CHANGED",
        )
        with connection.cursor(row_factory=tuple_row) as db:
            bundles = tuple(
                bundle for bundle in load_active_strategy_bundles(db)
                if bundle.card.strategy_version == VERSION
            )
            routes = read_p90_real_account_routes(
                db, bundles=bundles, at=datetime.now(UTC),
            )
            route = routes.main if routes.main.strategy_id == position.strategy_id else (
                routes.sub if routes.sub.strategy_id == position.strategy_id else None
            )
            _require(
                route is not None
                and route.trading_account_id == account_id
                and route.strategy_execution_binding_id
                == position.strategy_execution_binding_id
                and route.activation_id == position.strategy_activation_id,
                "ACCOUNT_ROUTE_CHANGED",
            )
            epoch_rows = db.execute(
                """SELECT epoch_id
                     FROM strategy_entry.p90l90_real_source_epochs
                    WHERE status='ACTIVE'"""
            ).fetchall()
            _require(len(epoch_rows) == 1, "SOURCE_EPOCH_UNVERIFIED")
            identity = inspect_real_source_identity(
                db,
                epoch_id=str(epoch_rows[0][0]),
                loaded_commit=loaded_commit,
                bundles=bundles,
            )
        _require(
            p90_real_exit_no_pending(
                connection, position=position, trading_account_id=account_id
            ) is True,
            "PENDING_MUTATION",
        )
        quote, session_id = _source_quote(
            source_connection,
            symbol=symbol,
            observed_at=datetime.now(UTC),
        )
        _require(
            quote.producer_release_commit in identity.approved_producers,
            "SOURCE_PRODUCER_UNVERIFIED",
        )
        client = P90PrivateProofClient(
            account=account_id, connector=route.account_connector_id,
            timeout_seconds=3.0,
        )
        private = client.for_restart_recovery(position, at=datetime.now(UTC))
        _require(
            private.signed_provider_uid == route.registered_exchange_uid,
            "PRIVATE_UID_MISMATCH",
        )
        evidence = connection.execute(
            """SELECT initial_protection_evidence
                 FROM runtime.position_ownership
                WHERE position_id=%s AND state='OPEN'
                  AND trading_account_id=%s FOR UPDATE""",
            (position_id, account_id),
        ).fetchone()
        _require(
            evidence is not None and isinstance(
                evidence["initial_protection_evidence"], dict
            )
            and evidence["initial_protection_evidence"].get(
                "trailing_recovery_policy"
            ) == P90_TRAIL_RECOVERY_POLICY,
            "DURABLE_ELIGIBILITY_MISSING",
        )
        observation = build_p90_trail_recovery_observation(
            position=position, private=private, quote=quote,
            eligibility=P90_TRAIL_RECOVERY_POLICY,
            expected_source_session_id=session_id,
            received_at=datetime.now(UTC),
        )
        snapshot = _private_snapshot(private)
        _require(snapshot is not None, "SIGNED_PROTECTION_MISSING")

        def safety(
            subject: object,
            evaluation: object,
            checked_at: datetime,
        ) -> bool:
            decision = getattr(evaluation, "decision", None)
            if (
                subject != position
                or decision is None
                or datetime.now(UTC) - checked_at > timedelta(seconds=5)
                or not p90_real_exit_no_pending(
                    connection,
                    position=position,
                    trading_account_id=account_id,
                    publishing_exit_decision_id=decision.exit_decision_id,
                )
            ):
                return False
            # Source freshness / signed identity can change between evaluation
            # and command persistence: recheck both under the account lock.
            try:
                latest_quote, latest_session = _source_quote(
                    source_connection, symbol=symbol,
                    observed_at=datetime.now(UTC),
                )
                latest_private = client.for_restart_recovery(
                    position, at=datetime.now(UTC),
                )
                return (
                    latest_session == session_id
                    and latest_quote.producer_release_commit
                    == quote.producer_release_commit
                    and latest_quote.last_price == quote.last_price
                    and latest_quote.field_times[0] == quote.field_times[0]
                    and latest_private.signed_provider_uid
                    == private.signed_provider_uid
                    and latest_private.trading_account_id == account_id
                    and latest_private.generation_id == private.generation_id
                    and latest_private.pending_exit_mutation is False
                    and latest_private.observed_at >= private.observed_at
                    and _private_snapshot(latest_private) == snapshot
                )
            except Exception:
                return False

        execution = P90RealExitExecutionPort(
            connection,
            independently_verified_execution_safety=safety,
            account_by_strategy=routes.account_by_strategy,
        )
        evaluation = UniversalExitEngine().evaluate(
            position, plan, observation,
        )
        if evaluation.status is ExitEvaluationStatus.BLOCKED:
            reason = evaluation.reason
            if not _CODE.fullmatch(reason):
                reason = "POLICY_UNVERIFIED"
            raise P90PersistentTrailBlocked(
                "P90_TRAIL_RECOVERY_" + reason
            )
        store = PostgresExitShadowStore(connection)
        store.record(evaluation, position=position, plan=plan)
        if evaluation.status is ExitEvaluationStatus.DECISION_CREATED:
            execution(evaluation, position, plan)
            return "P90_TRAIL_RECOVERY_REQUEST_PUBLISHED"
        return "P90_TRAIL_RECOVERY_NO_NEW_ACTION"
