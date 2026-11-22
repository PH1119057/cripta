"""#849: credential-local fail-closed signed MAIN/SUB Exit-only preflight.

Runs ONLY as Unix cripta with a PostgreSQL read-only transaction. The two
existing credential-bound private workers issue signed Bybit GET through
peer-credential-checked AF_UNIX IPC. No secrets, Exchange write transport,
systemd, root or financial control API is available here.

PASS is transient input evidence, never release authorization or proof of
rollback behavior, API/ABI compatibility, owner reconciliation or postcutover.
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_SOURCE = "bybit:signed-recovery-linear-usdt:exact-account"
_ACCOUNT = re.compile(r"ta-[0-9a-f]{32}\Z")
_MAX_AGE_SECONDS = 15
_MAX_ACCOUNT_POSITIONS = 5
_POSITION_FIELDS = frozenset({
    "symbol", "side", "positionIdx", "size", "avgPrice", "stopLoss",
    "takeProfit", "trailingStop", "markPrice",
})


def _deny(reason: str) -> None:
    raise ValueError("EXIT_SIGNED_PREFLIGHT_" + reason)


def _decimal(raw: object, *, allow_zero: bool) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (TypeError, ValueError, InvalidOperation):
        _deny("POSITION_NUMBER_INVALID")
    if not value.is_finite() or value < 0 or (not allow_zero and value == 0):
        _deny("POSITION_NUMBER_INVALID")
    return value


def _route_ok(route: object, kind: str) -> bool:
    uid = getattr(route, "registered_exchange_uid", None)
    account = getattr(route, "trading_account_id", None)
    connector = getattr(route, "account_connector_id", None)
    return (
        getattr(route, "account_kind", None) == kind
        and isinstance(account, str)
        and _ACCOUNT.fullmatch(account) is not None
        and isinstance(connector, str) and bool(connector)
        and isinstance(uid, str)
        and uid.isascii() and uid.isdigit() and int(uid) > 0
    )


def _check_signed_exit_account_pair(
    *, routes: object, proofs: object, observed_at: datetime,
) -> dict[str, object]:
    """Validate reader output; synthetic data can never grant release."""
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or not hasattr(routes, "main") or not hasattr(routes, "sub")
        or not isinstance(proofs, (tuple, list)) or len(proofs) != 2
    ):
        _deny("REQUIRED_SOURCES_MISSING")
    main, sub = routes.main, routes.sub
    if not _route_ok(main, "MAIN") or not _route_ok(sub, "SUB"):
        _deny("ROUTE_OWNER_UNVERIFIED")
    if (
        main.trading_account_id == sub.trading_account_id
        or main.account_connector_id == sub.account_connector_id
        or main.registered_exchange_uid == sub.registered_exchange_uid
    ):
        _deny("MAIN_SUB_OWNER_COLLISION")

    position_counts: dict[str, int] = {}
    open_orders: dict[str, int] = {}
    for kind, route, proof in (("MAIN", main, proofs[0]), ("SUB", sub, proofs[1])):
        timestamp = getattr(proof, "observed_at", None)
        positions = getattr(proof, "positions", None)
        if (
            getattr(proof, "trading_account_id", None)
            != route.trading_account_id
            or getattr(proof, "signed_uid", None)
            != route.registered_exchange_uid
            or getattr(proof, "source_ref", None) != _SOURCE
            or not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
            or not isinstance(positions, (tuple, list))
            or len(positions) > _MAX_ACCOUNT_POSITIONS
            or type(getattr(proof, "open_orders", None)) is not int
            or proof.open_orders != 0
            or type(getattr(proof, "pending_commands", None)) is not int
            or proof.pending_commands != 0
        ):
            _deny("ACCOUNT_SIGNED_INVENTORY_UNVERIFIED")
        age = (observed_at.astimezone(UTC) - timestamp.astimezone(UTC)).total_seconds()
        if not 0 <= age <= _MAX_AGE_SECONDS:
            _deny("ACCOUNT_SIGNED_INVENTORY_STALE")
        symbols: set[str] = set()
        for row in positions:
            if (
                not isinstance(row, Mapping)
                or set(row) != _POSITION_FIELDS
                or not isinstance(row["symbol"], str)
                or not row["symbol"]
                or row["symbol"] in symbols
                or row["side"] not in ("Buy", "Sell")
                or str(row["positionIdx"]) != "0"
            ):
                _deny("SIGNED_POSITION_IDENTITY_INVALID")
            symbols.add(row["symbol"])
            for key in ("size", "avgPrice", "stopLoss", "takeProfit", "markPrice"):
                _decimal(row[key], allow_zero=False)
            _decimal(row["trailingStop"], allow_zero=True)
        position_counts[kind] = len(positions)
        open_orders[kind] = 0
    return {
        "state": "SIGNED_MAIN_SUB_GET_CONSISTENCY_PASS",
        "component_id": "EXIT",
        "source_ref": _SOURCE,
        "account_position_counts": position_counts,
        "account_open_orders": open_orders,
        "release_authorized": False,
        "financial_mutation_authorized": False,
        "runtime_behavior_verified": False,
        "rollback_ready": False,
        "source_compatibility_verified": False,
        "actual_host_attested": False,
    }


def collect_current_signed_exit_preflight_once() -> dict[str, object]:
    """No-argument credential-local GET/SELECT; no POST or trading action."""
    if sys.platform != "linux":
        _deny("ACTOR_NOT_CRIPTA")
    import pwd

    actual_uid = os.geteuid()
    try:
        cripta_uid = pwd.getpwnam("cripta").pw_uid
    except KeyError:
        _deny("ACTOR_NOT_CRIPTA")
    if actual_uid != cripta_uid or actual_uid == 0:
        _deny("ACTOR_NOT_CRIPTA")

    import psycopg

    from bybit_workbench.universal_entry.p90l90_legacy_strategy import (
        MAIN_ID,
        SUB_ID,
    )
    from bybit_workbench.universal_entry.p90l90_real_account_routes import (
        read_p90_real_account_routes,
    )
    from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
    from bybit_workbench.universal_entry.runtime_loader import (
        load_active_strategy_bundles,
    )
    from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
        P90PrivateProofClient,
    )

    try:
        with psycopg.connect(
            "dbname=cripta user=cripta host=/var/run/postgresql "
            "application_name=exit_849_signed_readonly_release_preflight",
            connect_timeout=5,
        ) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            identity = connection.execute(
                "SELECT current_user,current_database(),"
                "current_setting('transaction_read_only')"
            ).fetchone()
            if identity != ("cripta", "cripta", "on"):
                _deny("READ_ONLY_DB_ACTOR_UNVERIFIED")
            active = load_active_strategy_bundles(connection)
            bundles = tuple(
                bundle for bundle in active
                if bundle.card.strategy_version == VERSION
                and bundle.card.strategy_id in (MAIN_ID, SUB_ID)
            )
            if len(bundles) != 2:
                _deny("EXACT_TWO_REAL_BUNDLES_MISSING")
            routes = read_p90_real_account_routes(
                connection, bundles=bundles, at=datetime.now(UTC),
            )
            proofs: list[Any] = []
            for route in (routes.main, routes.sub):
                proof = P90PrivateProofClient(
                    account=route.trading_account_id,
                    connector=route.account_connector_id,
                    timeout_seconds=8.0,
                ).read_recovery_account()
                proofs.append(proof)
            return _check_signed_exit_account_pair(
                routes=routes, proofs=tuple(proofs),
                observed_at=datetime.now(UTC),
            )
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "EXIT_SIGNED_PREFLIGHT_"
        ):
            raise
        _deny("CREDENTIAL_LOCAL_PROOF_OR_DB_UNAVAILABLE")
