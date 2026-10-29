"""Fail-closed P90 REAL restart proof for already-open StrategyPositions.

Fresh source bootstrap deliberately requires both trading accounts to be FLAT.
This module owns the opposite startup branch: reuse the one active durable
source epoch only when local position lineage, complete signed MAIN/SUB account
inventory and every Exchange-side TP/SL/TRAIL035 position can all be proven.

It does not replay market data, change a source checkpoint, dispatch Entry/Exit
or grant LIVE rights.  Native old->new continuity is a separate source-owner
proof performed after this inventory certificate.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import ExitPlan, TradeDirection
from bybit_workbench.universal_exit.p90l90_real_observation import (
    ScopedPrivatePositionEvidence,
)
from bybit_workbench.universal_exit.p90l90_real_position_provider import (
    P90RealPositionProvider,
)
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateProofClient,
)
from bybit_workbench.universal_exit.p90l90_real_recovery_account import (
    P90RealSignedRecoveryAccount,
)
from bybit_workbench.universal_exit.p90l90_real_row_connection import (
    P90RealMappedExitConnection,
)

from .p90l90_causal_transport_join import P90L90CausalTransportJoin
from .p90l90_legacy_strategy import MAIN_ID, SUB_ID
from .p90l90_real_account_routes import (
    P90RealAccountRoute,
    P90RealAccountRoutes,
    read_p90_real_account_routes,
)
from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_EPOCH = re.compile(r"p90-real-epoch-[0-9a-f]{40}\Z")
_SYMBOLS = frozenset().union(*ALLOWED_SYMBOLS.values())
_RECOVERY_KEYS = frozenset((
    "symbol", "side", "positionIdx", "size", "avgPrice",
    "stopLoss", "takeProfit", "trailingStop", "markPrice",
))


class P90RealOpenRestartUnverified(RuntimeError):
    """Open REAL position cannot safely rejoin the original source epoch."""


@dataclass(frozen=True, slots=True)
class P90RealRestartPosition:
    position: StrategyPosition
    exit_plan: ExitPlan
    signed: ScopedPrivatePositionEvidence


@dataclass(frozen=True, slots=True)
class P90RealOpenRestartInventory:
    source_epoch_id: str
    source_commit: str
    routes: P90RealAccountRoutes
    main: P90RealSignedRecoveryAccount
    sub: P90RealSignedRecoveryAccount
    positions: tuple[P90RealRestartPosition, ...]
    checked_at: datetime

    @property
    def by_symbol(self) -> dict[str, P90RealRestartPosition]:
        return {item.position.symbol: item for item in self.positions}


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise P90RealOpenRestartUnverified("P90_REAL_RESTART_" + reason)


def _active_epoch_with_open_positions(
    connection: Any, *, source_commit: str,
) -> str | None:
    """Return the one resumable epoch, or None only for genuinely flat v3."""
    _require(bool(_SHA.fullmatch(source_commit)), "SOURCE_COMMIT_INVALID")
    rows = connection.execute(
        """SELECT position_id,state,strategy_id,symbol,trading_account_id
             FROM runtime.position_ownership
            WHERE strategy_version=%s
              AND state IN ('OPEN','RECONCILIATION_REQUIRED')
            ORDER BY position_id""",
        (VERSION,),
    ).fetchall()
    if not rows:
        return None
    _require(
        all(
            len(row) == 5
            and row[1] == "OPEN"
            and row[2] in (MAIN_ID, SUB_ID)
            and row[3] in ALLOWED_SYMBOLS[row[2]]
            and isinstance(row[4], str) and row[4]
            for row in rows
        ),
        "LOCAL_POSITION_STATE_OR_OWNER_UNVERIFIED",
    )
    _require(
        len({(row[4], row[3]) for row in rows}) == len(rows),
        "DUPLICATE_PHYSICAL_POSITION",
    )
    # Account-wide nonterminal command state is proven by the credential-local
    # recovery reader in universal_exit before this epoch can be resumed.
    # Universal Entry deliberately does not inspect the Execution command rail.
    entry_pending = connection.execute(
        """SELECT count(*) FROM strategy_entry.execution_requests e
            WHERE e.strategy_version=%s
              AND NOT EXISTS (
                  SELECT 1 FROM strategy_entry.execution_dispatches d
                   WHERE d.execution_request_id=e.execution_request_id
              )""",
        (VERSION,),
    ).fetchone()
    exit_pending = connection.execute(
        """SELECT count(*) FROM strategy_exit.execution_requests e
            WHERE e.strategy_version=%s
              AND NOT EXISTS (
                  SELECT 1 FROM strategy_exit.execution_dispatches d
                   WHERE d.execution_request_id=e.execution_request_id
              )""",
        (VERSION,),
    ).fetchone()
    _require(
        entry_pending == (0,) and exit_pending == (0,),
        "UNDISPATCHED_EXECUTION_REQUEST",
    )
    epochs = connection.execute(
        """SELECT epoch_id,source_commit
             FROM strategy_entry.p90l90_real_source_epochs
            WHERE status='ACTIVE' ORDER BY opened_at,epoch_id"""
    ).fetchall()
    _require(
        len(epochs) == 1
        and isinstance(epochs[0][0], str)
        and _EPOCH.fullmatch(epochs[0][0]) is not None
        and epochs[0][1] == source_commit,
        "ACTIVE_SOURCE_EPOCH_IDENTITY_UNVERIFIED",
    )
    epoch_id = str(epochs[0][0])
    checkpoints = connection.execute(
        """SELECT symbol,checkpoint_json,source_commit
             FROM strategy_entry.p90l90_real_causal_checkpoints
            WHERE epoch_id=%s ORDER BY symbol""",
        (epoch_id,),
    ).fetchall()
    _require(
        len(checkpoints) == len(_SYMBOLS)
        and {row[0] for row in checkpoints} == _SYMBOLS
        and all(row[2] == source_commit for row in checkpoints),
        "EXACT_SOURCE_CHECKPOINT_SET_MISSING",
    )
    for symbol, snapshot, _commit in checkpoints:
        try:
            join = P90L90CausalTransportJoin.restore(snapshot)
        except Exception:
            raise P90RealOpenRestartUnverified(
                "P90_REAL_RESTART_SOURCE_CHECKPOINT_CORRUPT"
            ) from None
        _require(
            join.symbol == symbol
            and not join.pending
            and join.last_emitted_trade_at is not None
            and type(join.last_emitted_trade_sequence) is int
            and join.last_emitted_trade_sequence >= 0
            and bool(join.last_emitted_exec_ids),
            "DURABLE_NATIVE_CURSOR_INCOMPLETE",
        )
    return epoch_id


def inspect_p90_real_open_restart_epoch(
    connection: Any, *, source_commit: str,
) -> str | None:
    """Read-only startup branch selector; performs no signed/network reads."""
    return _active_epoch_with_open_positions(
        connection, source_commit=source_commit,
    )


def _route_by_account(routes: P90RealAccountRoutes) -> dict[str, P90RealAccountRoute]:
    return {
        routes.main.trading_account_id: routes.main,
        routes.sub.trading_account_id: routes.sub,
    }


def _decimal(raw: object, reason: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise P90RealOpenRestartUnverified(
            "P90_REAL_RESTART_" + reason
        ) from None
    if not value.is_finite():
        raise P90RealOpenRestartUnverified("P90_REAL_RESTART_" + reason)
    return value


def _verify_trailing(
    position: StrategyPosition,
    evidence: ScopedPrivatePositionEvidence,
) -> None:
    tick = evidence.tick_size
    expected = (
        (position.actual_avg_fill * Decimal("0.0035") / tick)
        .to_integral_value(rounding=ROUND_FLOOR) * tick
    )
    actual = _decimal(
        evidence.position.get("trailingStop"),
        "SIGNED_TRAILING_VALUE_INVALID",
    )
    _require(
        expected > 0
        and actual == expected
        and actual % tick == 0,
        "SIGNED_TRAIL035_NOT_ARMED",
    )


def _minimal_position(row: Mapping[str, object]) -> dict[str, object]:
    _require(
        set(row).issuperset(_RECOVERY_KEYS),
        "SIGNED_POSITION_SHAPE_INCOMPLETE",
    )
    return {key: row.get(key) for key in _RECOVERY_KEYS}


def prove_p90_real_open_restart_inventory(
    connection: Any,
    *,
    bundles: tuple[object, ...],
    source_commit: str,
    source_epoch_id: str,
    checked_at: datetime,
) -> P90RealOpenRestartInventory:
    """Prove exact durable positions == complete signed MAIN/SUB inventory."""
    _require(
        checked_at.tzinfo is not None
        and checked_at.utcoffset() is not None
        and _EPOCH.fullmatch(source_epoch_id) is not None,
        "CHECK_TIME_OR_EPOCH_INVALID",
    )
    current = _active_epoch_with_open_positions(
        connection, source_commit=source_commit,
    )
    _require(current == source_epoch_id, "SOURCE_EPOCH_CHANGED")

    routes = read_p90_real_account_routes(
        connection, bundles=bundles, at=checked_at,
    )
    mapped = P90RealMappedExitConnection(connection)
    provider = P90RealPositionProvider(
        mapped, account_by_strategy=routes.account_by_strategy,
    )
    durable: list[tuple[StrategyPosition, ExitPlan]] = []
    for symbol in sorted(_SYMBOLS):
        durable.extend(provider(symbol))
    _require(bool(durable), "NO_DURABLE_OPEN_POSITION")

    account_proofs: dict[str, P90RealSignedRecoveryAccount] = {}
    for route in (routes.main, routes.sub):
        try:
            proof = P90PrivateProofClient(
                account=route.trading_account_id,
                connector=route.account_connector_id,
                timeout_seconds=3.0,
            ).read_recovery_account()
        except Exception:
            raise P90RealOpenRestartUnverified(
                "P90_REAL_RESTART_ACCOUNT_SIGNED_INVENTORY_MISSING"
            ) from None
        _require(
            proof.trading_account_id == route.trading_account_id
            and proof.signed_uid == route.registered_exchange_uid
            and bool(proof.private_generation_id)
            and proof.source_ref
            == "bybit:signed-recovery-linear-usdt:exact-account"
            and proof.open_orders == 0
            and proof.pending_commands == 0,
            "ACCOUNT_SIGNED_INVENTORY_IDENTITY_OR_FRESHNESS",
        )
        account_proofs[route.trading_account_id] = proof
    _require(
        routes.main.trading_account_id != routes.sub.trading_account_id
        and routes.main.registered_exchange_uid != routes.sub.registered_exchange_uid
        and account_proofs[routes.main.trading_account_id].private_generation_id
        != account_proofs[routes.sub.trading_account_id].private_generation_id,
        "MAIN_SUB_RECOVERY_PROOF_COLLISION",
    )

    expected_symbols: dict[str, set[str]] = {
        routes.main.trading_account_id: set(),
        routes.sub.trading_account_id: set(),
    }
    for position, _plan in durable:
        account = position.trading_account_id
        _require(account in expected_symbols, "DURABLE_FOREIGN_ACCOUNT")
        expected_symbols[str(account)].add(position.symbol)
    for account, proof in account_proofs.items():
        actual_symbols = {str(row.get("symbol")) for row in proof.positions}
        _require(
            len(actual_symbols) == len(proof.positions)
            and actual_symbols == expected_symbols[account],
            "SIGNED_ACCOUNT_POSITION_SET_MISMATCH",
        )

    routes_by_account = _route_by_account(routes)
    verified: list[P90RealRestartPosition] = []
    for position, plan in durable:
        account = str(position.trading_account_id)
        route = routes_by_account[account]
        account_proof = account_proofs[account]
        try:
            signed = P90PrivateProofClient(
                account=account,
                connector=route.account_connector_id,
                timeout_seconds=3.0,
            ).for_restart_recovery(position, datetime.now(UTC))
        except Exception:
            raise P90RealOpenRestartUnverified(
                "P90_REAL_RESTART_POSITION_SIGNED_EVIDENCE_MISSING"
            ) from None
        account_rows = [
            row for row in account_proof.positions
            if row.get("symbol") == position.symbol
        ]
        _require(
            len(account_rows) == 1
            and signed.trading_account_id == account
            and signed.connector_id == route.account_connector_id
            and signed.signed_provider_uid == route.registered_exchange_uid
            and signed.generation_id == account_proof.private_generation_id
            and signed.exchange_position_key == position.exchange_position_key
            and signed.position_idx == 0
            and signed.pending_exit_mutation is False
            and _minimal_position(signed.position)
            == _minimal_position(account_rows[0]),
            "POSITION_SIGNED_INVENTORY_DRIFT",
        )
        side = "Buy" if position.direction is TradeDirection.LONG else "Sell"
        _require(
            signed.position.get("side") == side
            and _decimal(signed.position.get("size"), "SIGNED_SIZE_INVALID")
            == position.actual_qty
            and _decimal(signed.position.get("avgPrice"), "SIGNED_FILL_INVALID")
            == position.actual_avg_fill,
            "POSITION_FILL_OR_SIDE_DRIFT",
        )
        _verify_trailing(position, signed)
        verified.append(P90RealRestartPosition(position, plan, signed))

    return P90RealOpenRestartInventory(
        source_epoch_id=source_epoch_id,
        source_commit=source_commit,
        routes=routes,
        main=account_proofs[routes.main.trading_account_id],
        sub=account_proofs[routes.sub.trading_account_id],
        positions=tuple(sorted(
            verified, key=lambda item: item.position.symbol,
        )),
        checked_at=datetime.now(UTC),
    )
