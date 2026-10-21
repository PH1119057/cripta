"""No production market access: test bounded REST trade-audit recovery in isolation."""
from __future__ import annotations

import ast
import http.client
import json
import socket
import types
import urllib.error
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


def _audit_function(response_plan: list[object]) -> tuple[Any, list[float]]:
    source = Path("operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_fetch_recent_trade_snapshot"
    )

    class NotProvable(Exception):
        pass

    class Response:
        def __init__(self, *, incomplete: bool = False) -> None:
            self.incomplete = incomplete

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self) -> bytes:
            if self.incomplete:
                raise http.client.IncompleteRead(b"truncated", 99)
            body = {"retCode": 0, "time": 1791840000000, "result": {"list": []}}
            return json.dumps(body).encode()

    timeouts: list[float] = []

    def fake_open(_request: object, *, timeout: float, context: object) -> Response:
        timeouts.append(timeout)
        next_response = response_plan.pop(0)
        if isinstance(next_response, BaseException):
            raise next_response
        return Response(incomplete=next_response == "incomplete_body")

    namespace: dict[str, object] = {
        "urllib": types.SimpleNamespace(
            parse=urllib.parse,
            error=urllib.error,
            request=types.SimpleNamespace(
                Request=lambda *_args, **_kwargs: None,
                urlopen=fake_open,
            ),
        ),
        "PUBLIC_REST": "https://example.invalid",
        "TRADE_AUDIT_REQUEST_TIMEOUT_SECONDS": 4.0,
        "_HTTPS_CONTEXT": object(),
        "ContinuityNotProvable": NotProvable,
        "socket": socket,
        "http": types.SimpleNamespace(client=http.client),
        "json": json,
        "datetime": datetime,
        "UTC": UTC,
        "Mapping": __import__("collections").abc.Mapping,
        "_result_list": lambda payload: payload["result"]["list"],
        "cast": lambda _type, value: value,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "test", "exec"), namespace)
    return namespace["_fetch_recent_trade_snapshot"], timeouts


def test_transient_timeout_retries_without_raising_red_fault() -> None:
    fetch, timeouts = _audit_function([TimeoutError("slow"), "ok"])
    rows, timestamp = fetch("INJUSDT")
    assert rows == []
    assert timestamp.tzinfo is not None
    assert timeouts == [2.0, 2.0]


def test_persistent_timeout_fails_closed_after_bounded_retries() -> None:
    fetch, timeouts = _audit_function([TimeoutError("slow"), TimeoutError("still slow")])
    with pytest.raises(Exception, match="PUBLIC_TRADE silence audit unavailable for LTCUSDT"):
        fetch("LTCUSDT")
    assert timeouts == [2.0, 2.0]


def test_non_network_error_does_not_retry() -> None:
    fetch, timeouts = _audit_function([ValueError("invalid public data")])
    with pytest.raises(Exception, match="invalid public data"):
        fetch("APTUSDT")
    assert timeouts == [2.0]


def test_partial_http_response_body_retries_and_proves_exact_rest_snapshot() -> None:
    fetch, timeouts = _audit_function(["incomplete_body", "ok"])
    rows, response_time = fetch("APTUSDT")
    assert rows == []
    assert response_time.tzinfo is not None
    assert timeouts == [2.0, 2.0]


def test_repeat_partial_response_body_fails_closed_after_two_attempts() -> None:
    fetch, timeouts = _audit_function(["incomplete_body", "incomplete_body"])
    with pytest.raises(Exception, match="PUBLIC_TRADE silence audit unavailable for DOTUSDT"):
        fetch("DOTUSDT")
    assert timeouts == [2.0, 2.0]


def test_remote_disconnected_retries_without_silent_continuity_assumption() -> None:
    fetch, timeouts = _audit_function([
        http.client.RemoteDisconnected("close before reply"),
        "ok",
    ])
    rows, _ = fetch("LTCUSDT")
    assert rows == []
    assert timeouts == [2.0, 2.0]
