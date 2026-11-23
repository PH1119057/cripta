"""#849: credential-local fail-closed signed MAIN/SUB Exit-only preflight.

Runs ONLY as Unix cripta with a PostgreSQL read-only transaction. The two
existing credential-bound private workers issue signed Bybit GET through
peer-credential-checked AF_UNIX IPC. No secrets, Exchange write transport,
systemd, root or financial control API is available here.

PASS is transient input evidence, never release authorization or proof of
rollback behavior, API/ABI compatibility, owner reconciliation or postcutover.
"""
from __future__ import annotations

import hashlib
import json
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



def _check_signed_exit_plan_recovery(
    *, routes: object, proofs: object, bindings: object,
) -> dict[str, object]:
    """Correlate signed Bybit positions with actual immutable DB Exit bindings.

    A positive result is only a short-lived read-only consistency proof.
    It never authorizes root cutover or a financial mutation.
    """
    if (
        not hasattr(routes, "main") or not hasattr(routes, "sub")
        or not isinstance(proofs, (tuple, list)) or len(proofs) != 2
        or not isinstance(bindings, (tuple, list))
    ):
        _deny("POSITION_EXIT_BINDING_INPUT_INVALID")

    accounts = (routes.main.trading_account_id, routes.sub.trading_account_id)
    if accounts[0] == accounts[1]:
        _deny("POSITION_EXIT_ACCOUNT_COLLISION")
    indexed: dict[str, dict[str, object]] = {account: {} for account in accounts}
    for binding in bindings:
        if not isinstance(binding, (tuple, list)) or len(binding) != 2:
            _deny("POSITION_EXIT_BINDING_INVALID")
        position, plan = binding
        account = getattr(position, "trading_account_id", None)
        symbol = getattr(position, "symbol", None)
        if (
            account not in indexed
            or not isinstance(symbol, str) or not symbol
            or symbol in indexed[account]
            or getattr(position, "identity_status", None) != "PROVEN"
            or getattr(position, "position_idx", None) != 0
            or getattr(position, "initial_protection_confirmed_at", None) is None
            or not getattr(position, "exit_plan_fingerprint", None)
            or getattr(plan, "exit_plan_fingerprint", None)
            != position.exit_plan_fingerprint
            or getattr(plan, "strategy_activation_id", None)
            != position.strategy_activation_id
        ):
            _deny("POSITION_EXIT_BINDING_UNVERIFIED")
        indexed[account][symbol] = position

    counts: dict[str, int] = {}
    for kind, account, proof in zip(("MAIN", "SUB"), accounts, proofs, strict=True):
        signed = getattr(proof, "positions", None)
        if not isinstance(signed, (tuple, list)):
            _deny("POSITION_SIGNED_ROWS_UNVERIFIED")
        signed_by_symbol = {
            row.get("symbol"): row for row in signed
            if isinstance(row, Mapping)
        }
        if (
            len(signed_by_symbol) != len(signed)
            or set(signed_by_symbol) != set(indexed[account])
        ):
            _deny("SIGNED_DB_POSITION_SET_MISMATCH")
        for symbol, position in indexed[account].items():
            row = signed_by_symbol[symbol]
            direction = getattr(getattr(position, "direction", None), "value", None)
            expected_side = {"LONG": "Buy", "SHORT": "Sell"}.get(direction)
            if (
                expected_side is None
                or row.get("side") != expected_side
                or str(row.get("positionIdx")) != "0"
                or _decimal(row.get("size"), allow_zero=False)
                != _decimal(getattr(position, "actual_qty", None), allow_zero=False)
            ):
                _deny("SIGNED_DB_POSITION_DETAILS_MISMATCH")
        counts[kind] = len(indexed[account])
    return {
        "state": "SIGNED_POSITION_EXIT_PLAN_DB_ALIGNMENT_PASS",
        "account_position_counts": counts,
        "immutable_exit_plan_lineage_checked": True,
        "release_authorized": False,
        "rollback_ready": False,
        "financial_mutation_authorized": False,
        "runtime_behavior_verified": False,
    }



def _recovery_snapshot(
    *, routes: object, proofs: object, bindings: object, observed_at: datetime,
) -> dict[str, object]:
    """Produce immutable read-only pre/post cutover comparison evidence.

    This is NOT a deployment or rollback verifier.  The future privileged
    owner must obtain both snapshots independently as credential-local real
    Bybit GET/DB SELECT, then verify actual process/consumer E2E separately.
    """
    signed = _check_signed_exit_account_pair(
        routes=routes, proofs=proofs, observed_at=observed_at,
    )
    lineage = _check_signed_exit_plan_recovery(
        routes=routes, proofs=proofs, bindings=bindings,
    )
    if (
        signed["account_position_counts"]
        != lineage["account_position_counts"]
    ):
        _deny("RECOVERY_POSITION_COUNT_DRIFT")
    assert isinstance(bindings, (list, tuple))
    bound = {
        (position.trading_account_id, position.symbol): (position, plan)
        for position, plan in bindings
    }
    digest: dict[str, str] = {}
    for kind, route, proof in (
        ("MAIN", routes.main, proofs[0]), ("SUB", routes.sub, proofs[1]),
    ):
        identities: list[list[str]] = []
        for row in sorted(proof.positions, key=lambda x: x["symbol"]):
            position, plan = bound[(route.trading_account_id, row["symbol"])]
            identities.append([
                str(position.strategy_position_id),
                str(position.exchange_position_key),
                str(position.strategy_activation_id),
                str(plan.exit_plan_fingerprint),
                str(row["symbol"]),
                str(row["side"]),
                str(row["positionIdx"]),
                str(_decimal(row["size"], allow_zero=False).normalize()),
                str(_decimal(row["avgPrice"], allow_zero=False).normalize()),
                str(_decimal(row["stopLoss"], allow_zero=False).normalize()),
                str(_decimal(row["takeProfit"], allow_zero=False).normalize()),
                str(_decimal(row["trailingStop"], allow_zero=True).normalize()),
            ])
        raw = json.dumps(
            [kind, route.trading_account_id, route.registered_exchange_uid,
             identities],
            separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")
        digest[kind] = hashlib.sha256(raw).hexdigest()
    return {
        "state": "SIGNED_EXIT_POSITION_SNAPSHOT_READ_ONLY",
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "account_position_counts": signed["account_position_counts"],
        "account_position_identity_and_protection_sha256": digest,
        "immutable_exit_plan_lineage_checked": True,
        "release_authorized": False,
        "rollback_ready": False,
        "runtime_behavior_verified": False,
        "financial_mutation_authorized": False,
    }


def _compare_recovery_snapshots(
    *, before: object, after: object,
) -> dict[str, object]:
    """Check exact signed/DB position preservation; never grant cutover.

    Mark price and account telemetry are excluded because they legitimately
    move.  Any position, quantity, TP, SL, trailing or ExitPlan drift blocks.
    """
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        _deny("RECOVERY_SNAPSHOT_MISSING")
    times: list[datetime] = []
    for snapshot in (before, after):
        if (
            snapshot.get("state") != "SIGNED_EXIT_POSITION_SNAPSHOT_READ_ONLY"
            or snapshot.get("immutable_exit_plan_lineage_checked") is not True
            or any(snapshot.get(flag) is not False for flag in (
                "release_authorized", "rollback_ready",
                "runtime_behavior_verified", "financial_mutation_authorized",
            ))
            or not isinstance(snapshot.get("account_position_counts"), dict)
            or set(snapshot["account_position_counts"]) != {"MAIN", "SUB"}
            or any(type(v) is not int or v < 0 or v > _MAX_ACCOUNT_POSITIONS
                   for v in snapshot["account_position_counts"].values())
            or not isinstance(
                snapshot.get("account_position_identity_and_protection_sha256"),
                dict,
            )
            or set(snapshot["account_position_identity_and_protection_sha256"])
            != {"MAIN", "SUB"}
            or any(
                not isinstance(v, str)
                or re.fullmatch("[0-9a-f]{64}", v) is None
                for v in snapshot[
                    "account_position_identity_and_protection_sha256"
                ].values()
            )
        ):
            _deny("RECOVERY_SNAPSHOT_UNTRUSTED")
        try:
            observed = datetime.fromisoformat(snapshot["observed_at"])
        except (KeyError, TypeError, ValueError):
            _deny("RECOVERY_TIMESTAMP_INVALID")
        if observed.tzinfo is None or observed.utcoffset() is None:
            _deny("RECOVERY_TIMESTAMP_INVALID")
        times.append(observed.astimezone(UTC))
    if not 0 <= (times[1] - times[0]).total_seconds() <= 180:
        _deny("RECOVERY_SNAPSHOTS_NOT_FRESH")
    for key in (
        "account_position_counts",
        "account_position_identity_and_protection_sha256",
    ):
        if before[key] != after[key]:
            _deny("RECOVERY_POSITION_OR_EXCHANGE_PROTECTION_DRIFT")
    return {
        "state": "SIGNED_EXISTING_POSITION_AND_PROTECTION_PRESERVED_READ_ONLY",
        "account_position_counts": after["account_position_counts"],
        "signed_protection_snapshot_unchanged": True,
        "release_authorized": False,
        "rollback_ready": False,
        "runtime_behavior_verified": False,
        "financial_mutation_authorized": False,
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
    from psycopg.rows import dict_row

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
    from bybit_workbench.universal_exit.p90l90_real_position_provider import (
        P90RealPositionProvider,
    )
    from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
        ALLOWED_SYMBOLS,
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
            signed = _check_signed_exit_account_pair(
                routes=routes, proofs=tuple(proofs),
                observed_at=datetime.now(UTC),
            )
            # Read the actual position/ExitPlan lineage under the same
            # READ ONLY transaction, after the signed GETs.  Never infer
            # that positive Bybit SL/TP alone restores an Exit owner.
            bindings: list[tuple[object, object]] = []
            with connection.cursor(row_factory=dict_row) as position_db:
                provider = P90RealPositionProvider(
                    position_db,
                    account_by_strategy=routes.account_by_strategy,
                )
                for symbol in sorted(set().union(*ALLOWED_SYMBOLS.values())):
                    bindings.extend(provider(symbol))
            snapshot = _recovery_snapshot(
                routes=routes, proofs=tuple(proofs), bindings=tuple(bindings),
                observed_at=datetime.now(UTC),
            )
            return {
                **signed,
                "position_exit_plan_alignment":
                    "SIGNED_POSITION_EXIT_PLAN_DB_ALIGNMENT_PASS",
                "existing_position_recovery_snapshot": snapshot,
            }
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "EXIT_SIGNED_PREFLIGHT_"
        ):
            raise
        _deny("CREDENTIAL_LOCAL_PROOF_OR_DB_UNAVAILABLE")
