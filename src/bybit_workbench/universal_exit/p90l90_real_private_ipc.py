"""Credential-local, read-only P90 REAL private evidence over Linux AF_UNIX.

No Exchange secret, POST, filesystem socket/path or remote network endpoint is
available to the Observer. Producer remains the account's private_runtime.
Both ends require Linux kernel SO_PEERCRED (not request-supplied identity);
account/connector/binding, nonce and strict freshness are checked separately.
This transports signed-GET evidence, never a signature of Bybit's response.
It does NOT replace exact native fill-forward continuity or authorize LIVE.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import struct
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

from .p90l90_real_observation import ScopedPrivatePositionEvidence

_ACCOUNT = re.compile(r"ta-[a-f0-9]{32}\Z")
_NONCE = re.compile(r"[a-f0-9]{32}\Z")
_MAX_BYTES = 32_768
_VERSION = 1
_AF_UNIX = getattr(socket, "AF_UNIX", -1)


class P90PrivateIpcUnverified(RuntimeError):
    """Fail closed if producer, credential proof or source lineage is absent."""


def private_ipc_address(trading_account_id: str) -> str:
    if not _ACCOUNT.fullmatch(trading_account_id):
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_ACCOUNT_INVALID")
    # Linux abstract socket: no mkdir, chmod, bind pathname, or stale socket
    # deletion. Private workers and Observer use the same system namespace.
    return "\0cripta-p90-private-v1-" + trading_account_id


def peer_same_runtime_uid(connection: socket.socket) -> bool:
    if (
        sys.platform != "linux"
        or not hasattr(socket, "SO_PEERCRED")
        or connection.family != _AF_UNIX
    ):
        return False
    try:
        raw = connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        pid, uid, _gid = struct.unpack("3i", raw)
    except (OSError, ValueError, struct.error):
        return False
    return int(pid) > 1 and int(uid) == os.geteuid()


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_TIMESTAMP_INVALID")
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_TIMESTAMP_INVALID") from None
    if at.tzinfo is None or at.utcoffset() is None:
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_TIMESTAMP_INVALID")
    return at.astimezone(UTC)


def _frame(value: Mapping[str, object]) -> bytes:
    try:
        result = json.dumps(
            dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_SERIALIZATION_INVALID") from None
    if not result or len(result) >= _MAX_BYTES:
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_FRAME_TOO_LARGE")
    return result + b"\n"


def _receive(stream: socket.socket) -> Mapping[str, object]:
    data = bytearray()
    while len(data) < _MAX_BYTES:
        block = stream.recv(min(4096, _MAX_BYTES - len(data)))
        if not block:
            raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_INCOMPLETE_FRAME")
        data.extend(block)
        if b"\n" in block:
            break
    if not data.endswith(b"\n") or len(data) >= _MAX_BYTES:
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_FRAME_INVALID")
    try:
        payload = json.loads(bytes(data[:-1]))
    except (ValueError, UnicodeDecodeError):
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_JSON_INVALID") from None
    if not isinstance(payload, dict):
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_PAYLOAD_INVALID")
    return payload


def checked_request(
    raw: Mapping[str, object], *,
    expected_account: str,
    expected_connector: str,
) -> tuple[str, str, str, datetime, int | None, str | None]:
    """Prove complete request shape; producer must reload DB position itself."""
    nonce = raw.get("nonce")
    position_id = raw.get("position_id")
    if (
        raw.get("version") != _VERSION
        or raw.get("account") != expected_account
        or raw.get("connector") != expected_connector
        or not isinstance(nonce, str)
        or _NONCE.fullmatch(nonce) is None
        or not isinstance(position_id, str)
        or not position_id
        or len(position_id) > 128
        or raw.get("kind") not in ("TRADE", "CLOSED")
        or not isinstance(raw.get("symbol"), str)
        or not raw["symbol"]
    ):
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_REQUEST_IDENTITY_INVALID")
    at = _timestamp(raw.get("event_at"))
    now = datetime.now(UTC)
    if not 0 <= (now - at).total_seconds() <= 15:
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_EVENT_STALE")
    kind = str(raw["kind"])
    seq, exec_id = raw.get("seq"), raw.get("exec_id")
    if kind == "TRADE":
        if type(seq) is not int or seq < 0 or not isinstance(exec_id, str) or not exec_id:
            raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_CURSOR_INVALID")
    elif seq is not None or exec_id is not None:
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_CLOSED_CURSOR_INVALID")
    return (
        str(raw["position_id"]), str(raw["symbol"]), kind, at,
        seq if isinstance(seq, int) else None,
        exec_id if isinstance(exec_id, str) else None,
    )


def evidence_to_wire(evidence: ScopedPrivatePositionEvidence) -> dict[str, object]:
    """Only minimum required signed inventory; never keys or response headers."""
    return {
        "account": evidence.trading_account_id,
        "binding": evidence.strategy_execution_binding_id,
        "connector": evidence.connector_id,
        "uid": evidence.signed_provider_uid,
        "generation": evidence.generation_id,
        "position_key": evidence.exchange_position_key,
        "symbol": evidence.symbol,
        "position_idx": evidence.position_idx,
        "observed_at": evidence.observed_at.isoformat(),
        "source_ref": evidence.source_ref,
        "position": dict(evidence.position),
        "tick_size": str(evidence.tick_size),
        "pending_exit_mutation": evidence.pending_exit_mutation,
    }


def _checked_evidence(
    data: object, *,
    position: StrategyPosition,
    expected_connector: str,
    received_at: datetime,
) -> ScopedPrivatePositionEvidence:
    if not isinstance(data, dict):
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_EVIDENCE_MISSING")
    if (
        data.get("account") != position.trading_account_id
        or data.get("binding") != position.strategy_execution_binding_id
        or data.get("connector") != expected_connector
        or data.get("position_key") != position.exchange_position_key
        or data.get("symbol") != position.symbol
        or data.get("position_idx") != position.position_idx
        or not isinstance(data.get("uid"), str)
        or not data["uid"].isdigit()
        or not isinstance(data.get("generation"), str)
        or not data["generation"]
        or data.get("source_ref") != "bybit:signed-position-list:exact-account"
        or data.get("pending_exit_mutation") is not False
        or not isinstance(data.get("position"), dict)
    ):
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_EVIDENCE_IDENTITY_INVALID")
    observed = _timestamp(data.get("observed_at"))
    if not 0 <= (received_at - observed).total_seconds() <= 15:
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_EVIDENCE_STALE")
    try:
        tick = Decimal(str(data.get("tick_size")))
    except (ValueError, TypeError, InvalidOperation):
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_TICK_INVALID") from None
    if not tick.is_finite() or tick <= 0:
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_TICK_INVALID")
    return ScopedPrivatePositionEvidence(
        trading_account_id=str(data["account"]),
        strategy_execution_binding_id=str(data["binding"]),
        connector_id=str(data["connector"]),
        signed_provider_uid=str(data["uid"]),
        generation_id=str(data["generation"]),
        exchange_position_key=str(data["position_key"]),
        symbol=str(data["symbol"]),
        position_idx=position.position_idx,
        observed_at=observed,
        source_ref=str(data["source_ref"]),
        position=data["position"],
        tick_size=tick,
        pending_exit_mutation=False,
    )


class P90PrivateProofClient:
    """Only call from the native Observer owner, never from Strategy policy."""

    def __init__(
        self, *, account: str, connector: str, timeout_seconds: float = 10.0,
    ) -> None:
        self._address = private_ipc_address(account)
        if not connector or not 0 < timeout_seconds <= 10:
            raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_CLIENT_INVALID")
        self._account = account
        self._connector = connector
        self._timeout = timeout_seconds

    def __call__(
        self, position: StrategyPosition, cursor: TradeCursor,
    ) -> ScopedPrivatePositionEvidence:
        if cursor.symbol != position.symbol or cursor.traded_at < position.fill_at:
            raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_NATIVE_LINEAGE_INVALID")
        return self._request(
            position=position, kind="TRADE", at=cursor.traded_at,
            seq=cursor.seq, exec_id=cursor.exec_id,
        )

    def for_closed_boundary(
        self, position: StrategyPosition, closed_at: datetime,
    ) -> ScopedPrivatePositionEvidence:
        if closed_at < position.fill_at:
            raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_CLOSED_BEFORE_FILL")
        return self._request(
            position=position, kind="CLOSED", at=closed_at,
            seq=None, exec_id=None,
        )

    def _request(
        self, *, position: StrategyPosition, kind: str,
        at: datetime, seq: int | None, exec_id: str | None,
    ) -> ScopedPrivatePositionEvidence:
        if position.trading_account_id != self._account:
            raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_FOREIGN_ACCOUNT")
        nonce = secrets.token_hex(16)
        message: dict[str, object] = {
            "version": _VERSION, "account": self._account,
            "connector": self._connector, "position_id": position.strategy_position_id,
            "symbol": position.symbol, "kind": kind, "event_at": at.isoformat(),
            "seq": seq, "exec_id": exec_id, "nonce": nonce,
        }
        try:
            with socket.socket(_AF_UNIX, socket.SOCK_STREAM) as stream:
                stream.settimeout(self._timeout)
                stream.connect(self._address)
                if not peer_same_runtime_uid(stream):
                    raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_PEER_NOT_VERIFIED")
                stream.sendall(_frame(message))
                raw = _receive(stream)
        except OSError:
            raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_TRANSPORT_UNAVAILABLE") from None
        if (
            raw.get("version") != _VERSION
            or raw.get("nonce") != nonce
            or raw.get("account") != self._account
            or raw.get("connector") != self._connector
            or raw.get("status") != "VERIFIED"
        ):
            raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_RESPONSE_UNVERIFIED")
        return _checked_evidence(
            raw.get("evidence"), position=position,
            expected_connector=self._connector, received_at=datetime.now(UTC),
        )


def serve_private_proof(
    *,
    account: str,
    connector: str,
    handler: Callable[[Mapping[str, object]], ScopedPrivatePositionEvidence],
    running: Callable[[], bool],
    ready: Callable[[], None] | None = None,
) -> None:
    """Opt-in account-owned broker loop; only kernel-authenticated local UID.

    No filesystem path, implicit activation, Exchange mutation or child actor.
    The account-local handler must load position+plan from DB, read signed
    Bybit facts with mounted credentials and fail closed for any uncertainty.
    """
    if sys.platform != "linux":
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_LINUX_REQUIRED")
    address = private_ipc_address(account)
    with socket.socket(_AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(address)
        server.listen(4)
        server.settimeout(1.0)
        if ready is not None:
            ready()
        while running():
            try:
                stream, _ = server.accept()
            except TimeoutError:
                continue
            with stream:
                stream.settimeout(10.0)
                if not peer_same_runtime_uid(stream):
                    continue
                try:
                    raw = _receive(stream)
                    checked_request(
                        raw, expected_account=account,
                        expected_connector=connector,
                    )
                    evidence = handler(raw)
                    if (
                        evidence.trading_account_id != account
                        or evidence.connector_id != connector
                    ):
                        raise P90PrivateIpcUnverified(
                            "P90_PRIVATE_IPC_PRODUCER_SCOPE_INVALID"
                        )
                    stream.sendall(_frame({
                        "version": _VERSION, "nonce": raw["nonce"],
                        "status": "VERIFIED", "account": account,
                        "connector": connector,
                        "evidence": evidence_to_wire(evidence),
                    }))
                except Exception:
                    # Fail closed without reflecting a malformed nonce or
                    # revealing credential/Bybit/DB exception data.
                    continue
