"""No production market access: test bounded REST trade-audit recovery in isolation."""
from __future__ import annotations

import ast
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
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def read(self) -> bytes:
            return json.dumps({"retCode": 0, "time": 1791840000000, "result": {"list": []}}).encode()

    timeouts: list[float] = []

    def fake_open(_request: object, *, timeout: float, context: object) -> Response:
        timeouts.append(timeout)
        next_response = response_plan.pop(0)
        if isinstance(next_response, BaseException):
            raise next_response
        return Response()

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
