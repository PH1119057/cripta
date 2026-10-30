"""Private WS must prove each topic, not merely authenticate a socket."""
from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from websocket import WebSocketTimeoutException

from bybit_workbench.private_ws_subscriptions import (
    PRIVATE_TOPICS,
    PrivateSubscriptionUnverified,
    confirm_private_subscriptions,
)


class _Socket:
    def __init__(self, answer: Callable[[dict[str, object]], list[object]]) -> None:
        self.requests: list[dict[str, object]] = []
        self.frames: list[object] = []
        self.answer = answer

    def send(self, payload: str) -> None:
        request = json.loads(payload)
        self.requests.append(request)
        self.frames.extend(self.answer(request))

    def recv(self) -> str:
        if not self.frames:
            raise WebSocketTimeoutException("no frame")
        frame = self.frames.pop(0)
        if isinstance(frame, Exception):
            raise frame
        return json.dumps(frame)


def _ack(request: dict[str, object], *, with_id: bool = False) -> dict[str, object]:
    response: dict[str, object] = {"op": "subscribe", "success": True, "ret_msg": ""}
    if with_id:
        response["req_id"] = request["req_id"]
    return response


@pytest.mark.parametrize("with_id", [False, True])
def test_exact_four_private_topics_sequentially_confirmed(with_id: bool) -> None:
    ws = _Socket(lambda req: [_ack(req, with_id=with_id)])
    assert confirm_private_subscriptions(ws, lambda _: None) == PRIVATE_TOPICS
    assert [req["args"] for req in ws.requests] == [[topic] for topic in PRIVATE_TOPICS]
    assert len({str(req["req_id"]) for req in ws.requests}) == 4


def test_topic_data_during_handshake_reaches_normal_handler() -> None:
    seen: list[dict[str, object]] = []
    ws = _Socket(lambda req: [
        {"topic": "position.linear", "data": [{"symbol": "LTCUSDT"}]},
        {"op": "pong"},
        _ack(req),
    ])
    confirm_private_subscriptions(ws, seen.append)
    assert len(seen) == 4
    assert all(item["topic"] == "position.linear" for item in seen)


def test_rejected_one_topic_cannot_claim_connected() -> None:
    def answer(req: dict[str, object]) -> list[object]:
        ack = _ack(req)
        if req["args"] == ["execution.linear"]:
            ack["success"] = False
        return [ack]

    ws = _Socket(answer)
    with pytest.raises(PrivateSubscriptionUnverified, match="PRIVATE_SUBSCRIBE_REJECTED"):
        confirm_private_subscriptions(ws, lambda _: None)
    assert len(ws.requests) == 2


@pytest.mark.parametrize("bad_response,reason", [
    ({"op": "subscribe", "success": True, "req_id": "foreign"}, "WRONG_REQUEST"),
    ({"op": "subscribe", "success": "true"}, "REJECTED"),
    ({"op": "subscribe", "success": True, "data": {"successTopics": []}}, "TOPIC_NOT_CONFIRMED"),
    ({"op": "auth", "success": True}, "UNEXPECTED_CONTROL"),
])
def test_ambiguous_or_bad_ack_fails_closed(
    bad_response: dict[str, object], reason: str,
) -> None:
    ws = _Socket(lambda _req: [bad_response])
    with pytest.raises(PrivateSubscriptionUnverified, match=reason):
        confirm_private_subscriptions(ws, lambda _: None)


def test_missing_ack_deadline_blocks_without_second_topic() -> None:
    ws = _Socket(lambda _: [])
    tick = [0.0]

    def monotonic() -> float:
        tick[0] += 1.0
        return tick[0]

    with pytest.raises(PrivateSubscriptionUnverified, match="ACK_TIMEOUT"):
        confirm_private_subscriptions(ws, lambda _: None, timeout_seconds=3, monotonic=monotonic)
    assert len(ws.requests) == 1


def test_shutdown_aborts_handshake() -> None:
    ws = _Socket(lambda _: [])
    with pytest.raises(PrivateSubscriptionUnverified, match="ACK_TIMEOUT"):
        confirm_private_subscriptions(ws, lambda _: None, is_running=lambda: False)
