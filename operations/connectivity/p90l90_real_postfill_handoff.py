"""Immediate REAL first-fill -> Universal Exit/Execution account-local bridge.

Called by the *same private Execution worker* after signed Bybit first-fill,
attached TP+SL readback, durable StrategyPosition and reconciliation. It does
not send Exchange POST. It requires a fresh independent native source window
from the currently bound Observer, and queues an idempotent account-scoped
Universal Exit command only through the existing atomic DB ledger.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.p90l90_real_account_routes import (
    read_p90_real_account_routes,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_entry.runtime_loader import (
    load_active_strategy_bundles,
)
from bybit_workbench.universal_exit.loader import load_exit_binding
from bybit_workbench.universal_exit.p90l90_real_exit_execution import (
    P90RealExitExecutionPort,
)
from bybit_workbench.universal_exit.p90l90_real_postfill_port import (
    P90RealPostfillExitPort,
)
from bybit_workbench.universal_exit.storage import PostgresExitShadowStore
from operations.connectivity.p90l90_real_postfill_source_client import (
    read_real_postfill_source_window,
)

SignedPositionFactory = Callable[[Any], Any]
PositionRuntimeProof = Callable[[StrategyPosition], bool]


class P90RealImmediatePostfillUnverified(RuntimeError):
    """First-fill account/source/protection truth is missing; reconcile safely."""


def handoff_real_first_protected_fill(
    connection: Any,
    *,
    position_id: str,
    trading_account_id: str,
    account_connector_id: str,
    exchange_entry_order_id: str,
    signed_execution_ids: tuple[str, ...],
    loaded_release_commit: str,
    signed_position_reader: SignedPositionFactory,
    current_private_ws: PositionRuntimeProof,
    no_pending_exit_command: PositionRuntimeProof,
) -> object:
    """Persist source-verified native TRAIL035 decision/request as account owner.

    The caller owns an actual INTRANS account-local transaction. A rejected
    proof MUST NOT be retried as another Market Entry. Existing Exchange TP/SL
    remain resident, while the normal fault/reconciliation workflow takes over.
    """
    now = datetime.now(UTC)
    if (
        not position_id or not trading_account_id or not account_connector_id
        or not exchange_entry_order_id or not signed_execution_ids
        or len(set(signed_execution_ids)) != len(signed_execution_ids)
        or any(not isinstance(value, str) or not value
               for value in signed_execution_ids)
        or len(loaded_release_commit) != 40
        or any(c not in "0123456789abcdef" for c in loaded_release_commit)
        or not callable(signed_position_reader)
        or not callable(current_private_ws)
        or not callable(no_pending_exit_command)
        or getattr(
            getattr(getattr(connection, "info", None), "transaction_status", None),
            "name", None,
        ) != "INTRANS"
    ):
        raise P90RealImmediatePostfillUnverified(
            "P90_REAL_POSTFILL_ACCOUNT_TRANSACTION_OR_INPUT_UNVERIFIED"
        )
    position, plan = load_exit_binding(
        connection, strategy_position_id=position_id
    )
    if (
        position.strategy_id not in ALLOWED_SYMBOLS
        or position.strategy_version != VERSION
        or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
        or position.trading_account_id != trading_account_id
        or position.strategy_execution_binding_id is None
        or position.identity_status != "PROVEN"
        or position.initial_protection_confirmed_at is None
        or plan.exit_plan_fingerprint != position.exit_plan_fingerprint
    ):
        raise P90RealImmediatePostfillUnverified(
            "P90_REAL_POSTFILL_DURABLE_POSITION_NOT_EXACT"
        )
    evidence = connection.execute(
        """SELECT initial_protection_evidence
             FROM runtime.position_ownership
            WHERE position_id=%s AND trading_account_id=%s
              AND identity_status='PROVEN' AND state='OPEN'""",
        (position_id, trading_account_id),
    ).fetchone()
    if (
        evidence is None or len(evidence) != 1
        or not isinstance(evidence[0], dict)
        or evidence[0].get("entry_order_id") != exchange_entry_order_id
        or evidence[0].get("entry_execution_ids") != list(signed_execution_ids)
        or evidence[0].get("symbol") != position.symbol
        or evidence[0].get("trading_account_id") != trading_account_id
    ):
        raise P90RealImmediatePostfillUnverified(
            "P90_REAL_POSTFILL_SIGNED_EXECUTION_OWNER_DRIFT"
        )
    rows = connection.execute(
        """SELECT epoch_id,source_commit
             FROM strategy_entry.p90l90_real_source_epochs
            WHERE status='ACTIVE'""",
    ).fetchall()
    if (
        len(rows) != 1 or len(rows[0]) != 2
        or rows[0][1] != loaded_release_commit
    ):
        raise P90RealImmediatePostfillUnverified(
            "P90_REAL_POSTFILL_SOURCE_EPOCH_NOT_EXACT_RELEASE"
        )
    epoch_id = str(rows[0][0])
    routes = read_p90_real_account_routes(
        connection,
        bundles=tuple(
            bundle for bundle in load_active_strategy_bundles(connection)
            if bundle.card.strategy_version == VERSION
        ),
        at=now,
    )
    route = (
        routes.main if routes.main.strategy_id == position.strategy_id
        else routes.sub
    )
    if (
        route.strategy_id != position.strategy_id
        or route.trading_account_id != trading_account_id
        or route.account_connector_id != account_connector_id
        or route.strategy_execution_binding_id
           != position.strategy_execution_binding_id
    ):
        raise P90RealImmediatePostfillUnverified(
            "P90_REAL_POSTFILL_MAIN_SUB_ACCOUNT_OR_BINDING_DRIFT"
        )
    if current_private_ws(position) is not True or (
        no_pending_exit_command(position) is not True
    ):
        raise P90RealImmediatePostfillUnverified(
            "P90_REAL_POSTFILL_PRIVATE_WS_OR_COMMAND_UNVERIFIED"
        )
    # The source provider is inside the actual Observer original market
    # epoch. No locally fabricated best price, candle or publicTrade proxy.
    coverage = read_real_postfill_source_window(
        position=position, source_epoch_id=epoch_id,
        source_commit=loaded_release_commit,
    )
    reader = signed_position_reader(connection)
    private = reader.for_closed_boundary(
        position, coverage.covered_through,
    )
    if (
        private.trading_account_id != trading_account_id
        or private.connector_id != account_connector_id
        or private.strategy_execution_binding_id
           != position.strategy_execution_binding_id
        or private.observed_at < coverage.covered_through
    ):
        raise P90RealImmediatePostfillUnverified(
            "P90_REAL_POSTFILL_SIGNED_PRIVATE_ACCOUNT_DRIFT"
        )
    active = connection.execute(
        """SELECT count(*)
             FROM runtime.position_ownership
            WHERE symbol=%s AND strategy_version=%s
              AND state IN ('OPEN','RECONCILIATION_REQUIRED')""",
        (position.symbol, VERSION),
    ).fetchone()
    if active != (1,):
        raise P90RealImmediatePostfillUnverified(
            "P90_REAL_POSTFILL_DUPLICATE_PHYSICAL_POSITION"
        )

    def read_durable_position(symbol: str):
        if symbol != position.symbol:
            return ()
        current, current_plan = load_exit_binding(
            connection, strategy_position_id=position_id
        )
        return ((current, current_plan),)

    def mutation_safety(
        subject: StrategyPosition, _evaluation: object, checked_at: datetime,
    ) -> bool:
        # Protection/ownership has already passed a current *signed* read
        # inside this account worker. These dynamic predicates are rechecked
        # after the durable account lock, not taken from a test-only Boolean.
        if (
            subject != position
            or checked_at.tzinfo is None
            or (datetime.now(UTC) - checked_at).total_seconds() > 5
            or current_private_ws(subject) is not True
            or no_pending_exit_command(subject) is not True
        ):
            return False
        gate = connection.execute(
            """SELECT epoch_id FROM strategy_entry.p90l90_real_source_epochs
                WHERE status='ACTIVE' AND epoch_id=%s
                  AND source_commit=%s""",
            (epoch_id, loaded_release_commit),
        ).fetchone()
        return gate == (epoch_id,)

    execution = P90RealExitExecutionPort(
        connection,
        independently_verified_execution_safety=mutation_safety,
        account_by_strategy=routes.account_by_strategy,
    )
    return P90RealPostfillExitPort(
        connection,
        position_provider=read_durable_position,
        exit_store=PostgresExitShadowStore(connection),
        execution_port=execution,
    )(
        position=position, plan=plan,
        private=private, coverage=coverage,
        signed_fill_execution_ids=signed_execution_ids,
        exact_entry_order_id=exchange_entry_order_id,
        received_at=max(datetime.now(UTC), private.observed_at),
    )
