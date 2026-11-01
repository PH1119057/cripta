"""Unprivileged client for the local REAL service-start broker."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

DEFAULT_SOCKET = Path("/run/cripta-real-readiness/broker.sock")
MAX_RESPONSE_BYTES = 65536


class ServiceBrokerClientError(RuntimeError):
    """Broker unavailable or request rejected."""

    def __init__(
        self,
        message: str,
        *,
        response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.response = response


def start_services(
    *,
    request_id: str,
    release_commit: str,
    units: tuple[str, ...],
    socket_path: Path = DEFAULT_SOCKET,
    timeout_seconds: float = 75.0,
) -> dict[str, Any]:
    if not units:
        return {
            "ok": True,
            "request_id": request_id,
            "release_commit": release_commit,
            "units": [],
        }
    payload = {
        "action": "START",
        "request_id": request_id,
        "release_commit": release_commit,
        "units": list(units),
    }
    data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    response = b""
    # Production is POSIX-only. The numeric fallback lets cross-platform unit
    # tests exercise the protocol through a fake socket without weakening the
    # real Linux failure path: an unsupported real socket still raises OSError.
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
        raise ServiceBrokerClientError("SERVICE_BROKER_UNAVAILABLE") from exc
    if len(response) > MAX_RESPONSE_BYTES or not response:
        raise ServiceBrokerClientError("SERVICE_BROKER_RESPONSE_INVALID")
    try:
        decoded = json.loads(response.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ServiceBrokerClientError("SERVICE_BROKER_RESPONSE_INVALID") from exc
    if not isinstance(decoded, dict):
        raise ServiceBrokerClientError("SERVICE_BROKER_RESPONSE_INVALID")
    if decoded.get("ok") is not True:
        reason = str(decoded.get("error") or "SERVICE_BROKER_REJECTED")
        response_units = decoded.get("units")
        if isinstance(response_units, list) and response_units:
            last = response_units[-1]
            if isinstance(last, dict) and last.get("detail"):
                reason += ":" + str(last["detail"])[:300]
        raise ServiceBrokerClientError(reason, response=decoded)
    if str(decoded.get("request_id") or "") != request_id:
        raise ServiceBrokerClientError("SERVICE_BROKER_REQUEST_ID_MISMATCH")
    if str(decoded.get("release_commit") or "") != release_commit:
        raise ServiceBrokerClientError("SERVICE_BROKER_RELEASE_MISMATCH")
    return decoded
