"""Unprivileged client for the local runtime-recovery broker."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

DEFAULT_SOCKET = Path("/run/cripta-runtime-recovery/broker.sock")
MAX_RESPONSE_BYTES = 65536


class RecoveryBrokerClientError(RuntimeError):
    """Recovery broker unavailable or request rejected."""

    def __init__(
        self,
        message: str,
        *,
        response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.response = response


def request_recovery(
    *,
    action: str,
    request_id: str,
    release_commit: str,
    unit: str,
    expected_active_state: str,
    expected_main_pid: int,
    socket_path: Path = DEFAULT_SOCKET,
    timeout_seconds: float = 55.0,
) -> dict[str, Any]:
    payload = {
        "action": action,
        "request_id": request_id,
        "release_commit": release_commit,
        "unit": unit,
        "expected_active_state": expected_active_state,
        "expected_main_pid": expected_main_pid,
    }
    data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    response = b""
    unix_family: int = int(getattr(socket, "AF_UNIX", 1))
    try:
        with socket.socket(unix_family, socket.SOCK_STREAM) as client:
            client.settimeout(timeout_seconds)
            client.connect(str(socket_path))
            client.sendall(data)
            while b"\n" not in response and len(response) <= MAX_RESPONSE_BYTES:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response += chunk
    except OSError as exc:
        raise RecoveryBrokerClientError("RECOVERY_BROKER_UNAVAILABLE") from exc

    if len(response) > MAX_RESPONSE_BYTES or not response:
        raise RecoveryBrokerClientError("RECOVERY_BROKER_RESPONSE_INVALID")
    try:
        decoded = json.loads(response.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryBrokerClientError("RECOVERY_BROKER_RESPONSE_INVALID") from exc
    if not isinstance(decoded, dict):
        raise RecoveryBrokerClientError("RECOVERY_BROKER_RESPONSE_INVALID")
    if decoded.get("ok") is not True:
        reason = str(decoded.get("error") or "RECOVERY_BROKER_REJECTED")
        if decoded.get("detail"):
            reason += ":" + str(decoded["detail"])[:300]
        raise RecoveryBrokerClientError(reason, response=decoded)

    expected = {
        "request_id": request_id,
        "release_commit": release_commit,
        "unit": unit,
        "action": action,
    }
    for key, value in expected.items():
        if str(decoded.get(key) or "") != value:
            raise RecoveryBrokerClientError(
                "RECOVERY_BROKER_RESPONSE_IDENTITY_MISMATCH"
            )
    if not isinstance(decoded.get("reconciliation_required"), bool):
        raise RecoveryBrokerClientError("RECOVERY_BROKER_RESPONSE_INVALID")
    return decoded
