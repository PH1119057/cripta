"""Fail-closed confirmation of account-private Bybit V5 topic subscriptions.

Bybit's private subscribe acknowledgement may omit req_id and topic names.
Send exactly one request at a time, attributing each success to the sole
outstanding topic. Topic frames during the handshake are never discarded.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Protocol

from websocket import WebSocketTimeoutException

PRIVATE_TOPICS = ("order.linear", "execution.linear", "position.linear", "wallet")


class PrivateSubscriptionUnverified(RuntimeError):
    """A required private topic has no verifiable successful acknowledgement."""


class PrivateSubscriptionSocket(Protocol):
    def send(self, payload: str) -> object: ...

    def recv(self) -> str | bytes: ...


def confirm_private_subscriptions(
    socket: PrivateSubscriptionSocket,
    on_topic: Callable[[dict[str, object]], None],
    *,
    timeout_seconds: float = 8.0,
    monotonic: Callable[[], float] = time.monotonic,
    is_running: Callable[[], bool] = lambda: True,
) -> tuple[str, ...]:
    """Verify four private subscriptions; no WS connected state before success.

    Bybit documents private acknowledgements without req_id or topic names.
    Sequential single-topic requests allow those responses without pretending
    that an unconfirmed subscription is active. Echoed IDs must match.
    """
    if timeout_seconds <= 0:
        raise ValueError("PRIVATE_SUBSCRIBE_TIMEOUT_INVALID")
    confirmed: list[str] = []
    for topic in PRIVATE_TOPICS:
        req_id = f"cripta-private-{topic}"
        socket.send(json.dumps(
            {"op": "subscribe", "req_id": req_id, "args": [topic]},
            separators=(",", ":"),
        ))
        deadline = monotonic() + timeout_seconds
        while is_running() and monotonic() < deadline:
            try:
                frame = socket.recv()
            except WebSocketTimeoutException:
                continue
            if not isinstance(frame, (str, bytes)) or not frame:
                raise PrivateSubscriptionUnverified("PRIVATE_SUBSCRIBE_EMPTY_FRAME")
            try:
                message = json.loads(frame)
            except (ValueError, TypeError) as exc:
                raise PrivateSubscriptionUnverified("PRIVATE_SUBSCRIBE_INVALID_JSON") from exc
            if not isinstance(message, dict):
                raise PrivateSubscriptionUnverified("PRIVATE_SUBSCRIBE_NOT_OBJECT")
            if message.get("op") == "subscribe":
                received_id = message.get("req_id")
                if received_id not in (None, "", req_id):
                    raise PrivateSubscriptionUnverified("PRIVATE_SUBSCRIBE_WRONG_REQUEST")
                if message.get("success") is not True:
                    raise PrivateSubscriptionUnverified("PRIVATE_SUBSCRIBE_REJECTED")
                topic_data = message.get("data")
                if topic_data is not None:
                    if not isinstance(topic_data, dict):
                        raise PrivateSubscriptionUnverified("PRIVATE_SUBSCRIBE_BAD_TOPIC_RESULT")
                    success = topic_data.get("successTopics")
                    failures = topic_data.get("failTopics")
                    if (
                        not isinstance(success, list)
                        or topic not in success
                        or (isinstance(failures, list) and bool(failures))
                    ):
                        raise PrivateSubscriptionUnverified("PRIVATE_SUBSCRIBE_TOPIC_NOT_CONFIRMED")
                confirmed.append(topic)
                break
            if isinstance(message.get("topic"), str) and message["topic"]:
                # The existing private event handler remains the only DB writer.
                on_topic(message)
            elif message.get("op") not in ("ping", "pong"):
                raise PrivateSubscriptionUnverified("PRIVATE_SUBSCRIBE_UNEXPECTED_CONTROL")
        else:
            raise PrivateSubscriptionUnverified("PRIVATE_SUBSCRIBE_ACK_TIMEOUT")
    return tuple(confirmed)
