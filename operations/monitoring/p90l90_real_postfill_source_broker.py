"""Credential-free Observer MarketSource POSTFILL proof broker.

The independent live publicTrade mirror owns original native trade evidence.
Only the credential-owning private Execution worker requests this *read-only*
proof after a real protected Exchange fill. Nothing in this broker may place
or approve an order, advance an epoch, or invent an OHLC/native event.
"""
from __future__ import annotations

import re
import socket
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from bybit_workbench.universal_entry.transport_continuity import ContinuityNotProvable
from bybit_workbench.universal_exit.p90l90_real_observation import (
    P90RealPostfillSourceWindow,
)
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    _frame,
    _receive,
    peer_same_runtime_uid,
)

_NONCE = re.compile(r"[a-f0-9]{32}\Z")
_SHA = re.compile(r"[a-f0-9]{40}\Z")
_ADDRESS = "\0cripta-p90-real-postfill-source-v1"


class P90RealPostfillMarketSourceUnverified(RuntimeError):
    """Independent live fill-forward MarketSource proof is missing."""


def checked_request(
    data: Mapping[str, object], *, source_epoch_id: str,
) -> tuple[str, str, str, str]:
    if (
        set(data) != {
            "kind", "version", "nonce", "position_id", "trading_account_id",
            "symbol", "source_epoch_id",
        }
        or data.get("kind") != "POSTFILL_SOURCE_WINDOW"
        or data.get("version") != 1
        or not isinstance(data.get("nonce"), str)
        or _NONCE.fullmatch(data["nonce"]) is None
        or not isinstance(data.get("position_id"), str)
        or not 1 <= len(data["position_id"]) <= 128
        or not isinstance(data.get("trading_account_id"), str)
        or not re.fullmatch(r"ta-[0-9a-f]{32}", data["trading_account_id"])
        or not isinstance(data.get("symbol"), str)
        or data.get("source_epoch_id") != source_epoch_id
    ):
        raise P90RealPostfillMarketSourceUnverified(
            "P90_REAL_MARKET_SOURCE_REQUEST_IDENTITY_INVALID"
        )
    return (
        str(data["position_id"]), str(data["trading_account_id"]),
        str(data["symbol"]), str(data["nonce"]),
    )


def build_source_window(
    *,
    mirror: PublicTradeMirrorBuffer,
    position: StrategyPosition,
    source_commit: str,
    source_epoch_id: str,
    evidence_epoch_id: str,
    at: datetime,
) -> P90RealPostfillSourceWindow:
    if (
        source_epoch_id != evidence_epoch_id
        or _SHA.fullmatch(source_commit) is None
        or position.strategy_id not in ALLOWED_SYMBOLS
        or position.strategy_version != VERSION
        or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
        or position.identity_status != "PROVEN"
        or position.initial_protection_confirmed_at is None
        or position.fill_at.tzinfo is None
        or position.initial_protection_confirmed_at.tzinfo is None
        or at.tzinfo is None or at.utcoffset() != timedelta()
        or not 0 <= (at - position.initial_protection_confirmed_at).total_seconds() <= 5
    ):
        raise P90RealPostfillMarketSourceUnverified(
            "P90_REAL_MARKET_SOURCE_POSITION_OR_TIME_UNVERIFIED"
        )
    before = mirror.snapshot()
    try:
        events = mirror.verified_fill_forward_events(
            symbol=position.symbol, fill_at=position.fill_at, at=at,
        )
    except ContinuityNotProvable:
        raise P90RealPostfillMarketSourceUnverified(
            "P90_REAL_MARKET_SOURCE_NATIVE_WINDOW_INCOMPLETE"
        ) from None
    after = mirror.snapshot()
    if (
        before["state"] != "ACTIVE"
        or after["state"] != "ACTIVE"
        or before["epoch"] != after["epoch"]
        or before["epoch_started_at"] != after["epoch_started_at"]
        or before["gaps"] != 0 or after["gaps"] != 0
        or not events
        or len(events) > 256
    ):
        raise P90RealPostfillMarketSourceUnverified(
            "P90_REAL_MARKET_SOURCE_EPOCH_RACE_OR_UNBOUNDED"
        )
    last = events[-1]
    if (
        last.traded_at < position.initial_protection_confirmed_at
        or not 0 <= (at - last.traded_at).total_seconds() <= 5
    ):
        raise P90RealPostfillMarketSourceUnverified(
            "P90_REAL_MARKET_SOURCE_POST_PROTECTION_NATIVE_MISSING"
        )
    try:
        prices = [Decimal(event.price) for event in events]
    except (InvalidOperation, TypeError, ValueError):
        raise P90RealPostfillMarketSourceUnverified(
            "P90_REAL_MARKET_SOURCE_NATIVE_PRICE_INVALID"
        ) from None
    if any(not p.is_finite() or p <= 0 for p in prices):
        raise P90RealPostfillMarketSourceUnverified(
            "P90_REAL_MARKET_SOURCE_NATIVE_PRICE_INVALID"
        )
    entry = position.actual_avg_fill
    if not entry.is_finite() or entry <= 0:
        raise P90RealPostfillMarketSourceUnverified(
            "P90_REAL_MARKET_SOURCE_FILL_PRICE_INVALID"
        )
    from bybit_workbench.universal_entry.contracts import TradeDirection

    best = (
        max((entry, *prices))
        if position.direction is TradeDirection.LONG
        else min((entry, *prices))
    )
    source_refs = tuple(
        f"bybit:publicTrade:{event.symbol}:{event.exec_id}"
        for event in events
    )
    return P90RealPostfillSourceWindow(
        position_id=position.strategy_position_id,
        symbol=position.symbol,
        fill_at=position.fill_at,
        covered_through=last.traded_at,
        source_commit=source_commit,
        last_native_price=prices[-1],
        best_favorable_price=best,
        source_refs=source_refs,
        complete_native_window=True,
    )


def read_exact_source_position(
    *,
    database_dsn: str,
    source_epoch_id: str,
    source_commit: str,
    position_id: str,
    trading_account_id: str,
    symbol: str,
) -> tuple[StrategyPosition, str]:
    """Independent READ ONLY durable position + active source-epoch proof.

    The private worker supplies ONLY identifiers. Never accept a position,
    fill, source commitment or native price supplied by the requester.
    """
    import psycopg
    from psycopg.rows import dict_row

    from bybit_workbench.universal_exit.loader import load_exit_binding

    with psycopg.connect(database_dsn, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        position, plan = load_exit_binding(
            connection, strategy_position_id=position_id
        )
        if (
            position.strategy_position_id != position_id
            or position.trading_account_id != trading_account_id
            or position.symbol != symbol
            or position.strategy_id not in ALLOWED_SYMBOLS
            or position.strategy_version != VERSION
            or position.identity_status != "PROVEN"
            or position.initial_protection_confirmed_at is None
            or plan.exit_plan_fingerprint != position.exit_plan_fingerprint
        ):
            raise P90RealPostfillMarketSourceUnverified(
                "P90_REAL_MARKET_SOURCE_DURABLE_POSITION_UNVERIFIED"
            )
        row = connection.execute(
            """SELECT epoch_id,source_commit,status
                 FROM strategy_entry.p90l90_real_source_epochs
                WHERE epoch_id=%s AND source_commit=%s AND status='ACTIVE'""",
            (source_epoch_id, source_commit),
        ).fetchone()
        if (
            not isinstance(row, Mapping)
            or row.get("epoch_id") != source_epoch_id
            or row.get("source_commit") != source_commit
            or row.get("status") != "ACTIVE"
        ):
            raise P90RealPostfillMarketSourceUnverified(
                "P90_REAL_MARKET_SOURCE_DURABLE_EPOCH_UNVERIFIED"
            )
        return position, source_epoch_id

def window_to_wire(window: P90RealPostfillSourceWindow) -> dict[str, object]:
    return {
        "position_id": window.position_id,
        "symbol": window.symbol,
        "fill_at": window.fill_at.isoformat(),
        "covered_through": window.covered_through.isoformat(),
        "source_commit": window.source_commit,
        "last_native_price": str(window.last_native_price),
        "best_favorable_price": str(window.best_favorable_price),
        "source_refs": list(window.source_refs),
        "complete_native_window": window.complete_native_window,
    }


def serve_source_windows(
    *,
    mirror: PublicTradeMirrorBuffer,
    source_commit: str,
    source_epoch_id: str,
    read_position: Callable[[str, str, str], tuple[StrategyPosition, str]],
    running: Callable[[], bool],
    ready: Callable[[], None] | None = None,
) -> None:
    """Socket binding belongs to the live Observer, not to Execution.

    read_position is an exact read-only DB adapter; the private worker cannot
    send a synthetic StrategyPosition or invent its own fill timeline.
    """
    if (
        sys.platform != "linux"
        or _SHA.fullmatch(source_commit) is None
        or not source_epoch_id.startswith("p90-real-epoch-")
        or not callable(read_position)
        or not callable(running)
    ):
        raise P90RealPostfillMarketSourceUnverified(
            "P90_REAL_MARKET_SOURCE_BROKER_CONFIG_INVALID"
        )
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(_ADDRESS)
        server.listen(8)
        server.settimeout(0.5)
        if ready is not None:
            ready()
        while running():
            try:
                stream, _ = server.accept()
            except TimeoutError:
                continue
            with stream:
                stream.settimeout(5.0)
                if not peer_same_runtime_uid(stream):
                    continue
                try:
                    request = _receive(stream)
                    position_id, account, symbol, nonce = checked_request(
                        request, source_epoch_id=source_epoch_id,
                    )
                    position, actual_epoch = read_position(
                        position_id, account, symbol,
                    )
                    if (
                        position.strategy_position_id != position_id
                        or position.trading_account_id != account
                        or position.symbol != symbol
                    ):
                        raise P90RealPostfillMarketSourceUnverified(
                            "P90_REAL_MARKET_SOURCE_DURABLE_IDENTITY_DRIFT"
                        )
                    window = build_source_window(
                        mirror=mirror, position=position,
                        source_commit=source_commit,
                        source_epoch_id=source_epoch_id,
                        evidence_epoch_id=actual_epoch,
                        at=datetime.now(UTC),
                    )
                    stream.sendall(_frame({
                        "version": 1, "nonce": nonce,
                        "status": "VERIFIED",
                        "source_epoch_id": source_epoch_id,
                        "position_id": position_id, "trading_account_id": account,
                        "window": window_to_wire(window),
                    }))
                except Exception:
                    # No DB, signed Exchange, path or source exception detail.
                    continue
