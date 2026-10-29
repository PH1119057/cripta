"""Actual Observer-owned start of a fresh P90 REAL source epoch (no orders).

This is called ONLY after both live publicTrade mirror and primary kline/trade
WebSocket subscriptions acknowledge the exact five markets. It consumes the
two *current credential-local* signed flat-account IPC providers, not a stored
status file or an assumed empty account. Source/candle DB mutations use one
SERIALIZABLE transaction; an epoch change during the proof rolls everything
back. It never activates a Strategy or changes any trading permission.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.universal_entry.p90l90_real_account_routes import (
    read_p90_real_account_routes,
    read_p90_real_signed_flat_accounts,
)
from bybit_workbench.universal_entry.p90l90_real_source_epoch_start import (
    P90RealNewWsSourceProof,
    start_p90_real_source_epoch,
)
from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from operations.connectivity.p90l90_real_command_epoch_proof import (
    read_p90_real_no_pending_runtime_commands,
)


class P90RealLiveSourceEpochUnverified(RuntimeError):
    """Either the current live market or both signed account owners are absent."""


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
    signed_accounts = read_p90_real_signed_flat_accounts(routes)
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
