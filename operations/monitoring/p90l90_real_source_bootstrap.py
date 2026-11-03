"""Actual Observer-owned start of a fresh P90 REAL source epoch (no orders).

This is called ONLY after both live publicTrade mirror and primary kline/trade
WebSocket subscriptions acknowledge the exact five markets. It consumes the
two *current credential-local* signed flat-account IPC providers, not a stored
status file or an assumed empty account. Source/candle DB mutations use one
SERIALIZABLE transaction; an epoch change during the proof rolls everything
back. It never activates a Strategy or changes any trading permission.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from bybit_workbench.universal_entry.p90l90_real_account_routes import (
    P90RealAccountRoutes,
    P90RealAccountRoutesUnverified,
    read_p90_real_account_routes,
    read_p90_real_signed_flat_accounts,
)
from bybit_workbench.universal_entry.p90l90_real_source_epoch_start import (
    P90RealNewWsSourceProof,
    start_p90_real_source_epoch,
)
from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90_ACCOUNT_PROOF_TIMEOUT_SECONDS,
)
from operations.connectivity.p90l90_real_command_epoch_proof import (
    read_p90_real_no_pending_runtime_commands,
)


class P90RealLiveSourceEpochUnverified(RuntimeError):
    """Either the current live market or both signed account owners are absent."""


_ACCOUNT_GENERATION_BARRIER_MAX_AGE_SECONDS = 8.0
_ACCOUNT_GENERATION_BARRIER_WAIT_SECONDS = 5.0
_ACCOUNT_GENERATION_BARRIER_POLL_SECONDS = 0.2
_PRIVATE_PROOF_RETRY_BUDGET_SECONDS = 8.0
_PRIVATE_PROOF_ATTEMPT_SECONDS = 2.0
_PRIVATE_PREFLIGHT_RETRY_BUDGET_SECONDS = P90_ACCOUNT_PROOF_TIMEOUT_SECONDS * 2.0
_PRIVATE_PREFLIGHT_ATTEMPT_SECONDS = P90_ACCOUNT_PROOF_TIMEOUT_SECONDS
_PRIVATE_PREFLIGHT_GENERATION_WAIT_SECONDS = P90_ACCOUNT_PROOF_TIMEOUT_SECONDS
_PRIVATE_PREFLIGHT_GENERATION_MAX_AGE_SECONDS = P90_ACCOUNT_PROOF_TIMEOUT_SECONDS


def _flat_generation_ready(
    connection: Any,
    *,
    account_id: str,
    connector_id: str,
    now: datetime,
    max_age_seconds: float = _ACCOUNT_GENERATION_BARRIER_MAX_AGE_SECONDS,
) -> bool:
    row = connection.execute(
        """SELECT generation_id,state,completed_at,positions_count,active_orders_count,
                  identity_status
             FROM runtime.account_state_generations
            WHERE trading_account_id=%s AND account_connector_id=%s
              AND identity_status='PROVEN'
              AND state IN ('COMPLETE','FAILED')
            ORDER BY started_at DESC,generation_id DESC LIMIT 1""",
        (account_id, connector_id),
    ).fetchone()
    if (
        not isinstance(row, (tuple, list))
        or len(row) != 6
        or not isinstance(row[0], str)
        or not row[0]
        or row[1] != "COMPLETE"
        or not isinstance(row[2], datetime)
        or row[2].tzinfo is None
        or row[2].utcoffset() is None
        or type(row[3]) is not int
        or row[3] != 0
        or type(row[4]) is not int
        or row[4] != 0
        or row[5] != "PROVEN"
    ):
        return False
    age = (now - row[2].astimezone(UTC)).total_seconds()
    return 0 <= age <= max_age_seconds


def _wait_for_fresh_flat_generations(
    connection: Any,
    *,
    routes: P90RealAccountRoutes,
    subscribed_local_at: datetime,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleeper: Callable[[float], None] = time.sleep,
    wait_seconds: float = _ACCOUNT_GENERATION_BARRIER_WAIT_SECONDS,
    max_age_seconds: float = _ACCOUNT_GENERATION_BARRIER_MAX_AGE_SECONDS,
) -> None:
    """Bounded pre-proof barrier inside the existing fresh WS epoch budget.

    This never substitutes DB state for signed private proof.  It only avoids
    calling the credential-local proof during the normal gap between one
    COMPLETE account generation becoming stale and the next COMPLETE
    generation finishing.
    """
    if (
        wait_seconds <= 0
        or not 0 < max_age_seconds <= P90_ACCOUNT_PROOF_TIMEOUT_SECONDS
    ):
        raise P90RealLiveSourceEpochUnverified(
            "P90_REAL_FLAT_GENERATION_BARRIER_CONFIG_INVALID"
        )
    deadline = subscribed_local_at + timedelta(seconds=wait_seconds)
    route_order = (routes.main, routes.sub)
    while True:
        now = clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise P90RealLiveSourceEpochUnverified(
                "P90_REAL_FLAT_GENERATION_CLOCK_INVALID"
            )
        now = now.astimezone(UTC)
        if all(
            _flat_generation_ready(
                connection,
                account_id=route.trading_account_id,
                connector_id=route.account_connector_id,
                now=now,
                max_age_seconds=max_age_seconds,
            )
            for route in route_order
        ):
            return
        remaining = (deadline - now).total_seconds()
        if remaining <= 0:
            raise P90RealLiveSourceEpochUnverified(
                "P90_REAL_FLAT_ACCOUNT_GENERATION_WAIT_TIMEOUT"
            )
        sleeper(min(_ACCOUNT_GENERATION_BARRIER_POLL_SECONDS, remaining))


def _read_signed_accounts_bounded(
    connection: Any,
    *,
    routes: P90RealAccountRoutes,
    started_at: datetime,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleeper: Callable[[float], None] = time.sleep,
    reader: Callable[..., tuple[Any, Any]] | None = None,
    retry_budget_seconds: float = _PRIVATE_PROOF_RETRY_BUDGET_SECONDS,
    attempt_seconds: float = _PRIVATE_PROOF_ATTEMPT_SECONDS,
    generation_wait_seconds: float = _ACCOUNT_GENERATION_BARRIER_WAIT_SECONDS,
    generation_max_age_seconds: float = _ACCOUNT_GENERATION_BARRIER_MAX_AGE_SECONDS,
) -> tuple[Any, Any]:
    """Retry only transport/provider-missing proof inside the existing fresh window.

    Identity, account, connector and signed-scope mismatches remain immediate
    fail-closed errors.  A short per-attempt IPC timeout prevents one local
    transport stall from consuming the whole source-attestation budget.
    """
    proof_reader = reader or read_p90_real_signed_flat_accounts
    if (
        retry_budget_seconds <= 0
        or not 0 < attempt_seconds <= P90_ACCOUNT_PROOF_TIMEOUT_SECONDS
        or generation_wait_seconds <= 0
        or not 0 < generation_max_age_seconds <= P90_ACCOUNT_PROOF_TIMEOUT_SECONDS
    ):
        raise P90RealLiveSourceEpochUnverified(
            "P90_REAL_FLAT_PRIVATE_PROOF_BUDGET_CONFIG_INVALID"
        )
    deadline = started_at + timedelta(seconds=retry_budget_seconds)
    while True:
        _wait_for_fresh_flat_generations(
            connection,
            routes=routes,
            subscribed_local_at=started_at,
            clock=clock,
            sleeper=sleeper,
            wait_seconds=generation_wait_seconds,
            max_age_seconds=generation_max_age_seconds,
        )
        now = clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise P90RealLiveSourceEpochUnverified(
                "P90_REAL_FLAT_PRIVATE_PROOF_CLOCK_INVALID"
            )
        remaining = (deadline - now.astimezone(UTC)).total_seconds()
        if remaining <= 0:
            raise P90RealLiveSourceEpochUnverified(
                "P90_REAL_FLAT_ACCOUNT_PRIVATE_PROOF_RETRY_BUDGET_EXPIRED"
            )
        attempt_timeout = min(attempt_seconds, remaining)
        try:
            return proof_reader(routes, timeout_seconds=attempt_timeout)
        except P90RealAccountRoutesUnverified as exc:
            if str(exc) != "P90_REAL_FLAT_ACCOUNT_PRIVATE_PROOF_MISSING":
                raise
            after = clock()
            if after.tzinfo is None or after.utcoffset() is None:
                raise P90RealLiveSourceEpochUnverified(
                    "P90_REAL_FLAT_PRIVATE_PROOF_CLOCK_INVALID"
                ) from None
            remaining = (deadline - after.astimezone(UTC)).total_seconds()
            if remaining <= _ACCOUNT_GENERATION_BARRIER_POLL_SECONDS:
                raise P90RealLiveSourceEpochUnverified(
                    "P90_REAL_FLAT_ACCOUNT_PRIVATE_PROOF_RETRY_BUDGET_EXPIRED"
                ) from None
            sleeper(min(_ACCOUNT_GENERATION_BARRIER_POLL_SECONDS, remaining))


def preflight_p90_real_private_flat_source(
    connection: Any,
    *,
    bundles: tuple[object, ...],
    started_at: datetime | None = None,
) -> None:
    """Warm exact MAIN/SUB private proof before the public WS attestation clock.

    This grants no permission and persists nothing.  The post-subscription
    bootstrap still performs a second fresh credential-local proof.
    """
    current = started_at or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise P90RealLiveSourceEpochUnverified(
            "P90_REAL_PRIVATE_PREFLIGHT_TIME_INVALID"
        )
    current = current.astimezone(UTC)
    routes = read_p90_real_account_routes(
        connection,
        bundles=bundles,
        at=current,
    )
    _read_signed_accounts_bounded(
        connection,
        routes=routes,
        started_at=current,
        retry_budget_seconds=_PRIVATE_PREFLIGHT_RETRY_BUDGET_SECONDS,
        attempt_seconds=_PRIVATE_PREFLIGHT_ATTEMPT_SECONDS,
        generation_wait_seconds=_PRIVATE_PREFLIGHT_GENERATION_WAIT_SECONDS,
        generation_max_age_seconds=_PRIVATE_PREFLIGHT_GENERATION_MAX_AGE_SECONDS,
    )


def _checked_mirror_epoch(
    mirror: PublicTradeMirrorBuffer, *,
    epoch_started_at: datetime,
    subscribed_local_at: datetime,
) -> tuple[int, datetime]:
    snapshot = mirror.snapshot()
    try:
        ready = datetime.fromisoformat(str(snapshot["epoch_started_at"]))
        serial = snapshot["epoch"]
        if (
            snapshot["state"] != "ACTIVE"
            or type(serial) is not int
            or serial <= 0
            or type(snapshot.get("gaps")) is not int
            or snapshot["gaps"] != 0
            or ready.tzinfo is None or ready.utcoffset() != timedelta()
            or not epoch_started_at <= ready <= subscribed_local_at
        ):
            raise ValueError("unproven mirror epoch")
    except (ValueError, TypeError, KeyError):
        raise P90RealLiveSourceEpochUnverified(
            "P90_REAL_LIVE_MIRROR_EPOCH_NOT_ATTESTED"
        ) from None
    return serial, ready


def start_current_p90_real_observer_epoch(
    connection: Any,
    *,
    bundles: tuple[object, ...],
    source_commit: str,
    mirror: PublicTradeMirrorBuffer,
    subscribed_symbols: frozenset[str],
    subscribed_intervals: frozenset[str],
    subscribed_local_at: datetime,
    subscribed_server_at: datetime,
    epoch_started_at: datetime,
) -> str:
    """Physically bind live Observer WS + private MAIN/SUB to durable REAL epoch.

    No account can provide the other's Exchange UID or flat state. The fresh
    publicTrade epoch begins EMPTY; OHLC history NEVER synthesizes native trades.
    On restart with exposure or uncertain source, startup blocks rather than
    replacing native checkpoints with future candles.
    """
    if (
        len(bundles) != 2
        or not source_commit
        or len(source_commit) != 40
        or any(c not in "0123456789abcdef" for c in source_commit)
        or any(
            x.tzinfo is None or x.utcoffset() != timedelta()
            for x in (subscribed_local_at, subscribed_server_at, epoch_started_at)
        )
    ):
        raise P90RealLiveSourceEpochUnverified(
            "P90_REAL_LIVE_EXACT_RELEASE_OR_BUNDLES_MISSING"
        )
    serial, mirror_ready_at = _checked_mirror_epoch(
        mirror, epoch_started_at=epoch_started_at,
        subscribed_local_at=subscribed_local_at,
    )
    proof = P90RealNewWsSourceProof(
        source_commit=source_commit,
        subscribed_symbols=subscribed_symbols,
        subscribed_intervals=subscribed_intervals,
        mirror_active=True,
        subscribed_local_at=subscribed_local_at,
        subscribed_server_at=subscribed_server_at,
        epoch_started_at=epoch_started_at,
    )
    expected = frozenset(
        ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")
    )
    if not proof.verified_for(expected):
        raise P90RealLiveSourceEpochUnverified(
            "P90_REAL_LIVE_PUBLIC_WS_SOURCE_UNVERIFIED"
        )
    # Both IPC producers are called while the real Observer is live, not from
    # untrusted persisted JSON. Their signed account identity and flat state
    # are revalidated inside the source transaction below.
    routes = read_p90_real_account_routes(
        connection, bundles=bundles, at=datetime.now(UTC),
    )
    signed_accounts = _read_signed_accounts_bounded(
        connection,
        routes=routes,
        started_at=subscribed_local_at,
    )
    # Autocommit observer requires an explicit SERIALIZABLE transaction. No
    # caller-supplied boolean grants a fresh market epoch.
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        requested_at = datetime.now(UTC)
        if not 0 <= (requested_at - subscribed_local_at).total_seconds() <= 10:
            raise P90RealLiveSourceEpochUnverified(
                "P90_REAL_LIVE_WS_ATTESTATION_EXPIRED"
            )
        epoch_id = start_p90_real_source_epoch(
            connection,
            source_commit=source_commit,
            source=proof,
            signed_accounts=signed_accounts,
            independently_checked_runtime_commands=(
                read_p90_real_no_pending_runtime_commands
            ),
            mirror_ready_at=mirror_ready_at,
            new_epoch_requested_at=requested_at,
            retire_previous=True,
        )
        latest_serial, latest_ready = _checked_mirror_epoch(
            mirror, epoch_started_at=epoch_started_at,
            subscribed_local_at=subscribed_local_at,
        )
        if latest_serial != serial or latest_ready != mirror_ready_at:
            raise P90RealLiveSourceEpochUnverified(
                "P90_REAL_LIVE_MIRROR_RESTART_DURING_EPOCH"
            )
    return epoch_id
