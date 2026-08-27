from __future__ import annotations

import io
import json
import urllib.error
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from bybit_workbench.research import research_http
from bybit_workbench.research.orderbook_cache_utils import find_local_orderbook_archive


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_json_retry_recovers_from_transient_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_request(url: str, *, timeout: float) -> _Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise urllib.error.URLError("temporary offline")
        return _Response({"retCode": 0, "result": {}})

    monkeypatch.setattr(research_http, "_http_request", fake_request)
    monkeypatch.setattr(research_http.time, "sleep", lambda _seconds: None)

    payload = research_http.read_json_with_retry(
        "https://example.invalid/test",
        label="test request",
        attempts=4,
    )

    assert payload["retCode"] == 0
    assert calls == 3


def test_json_retry_does_not_retry_non_retryable_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_request(url: str, *, timeout: float) -> _Response:
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            url,
            404,
            "not found",
            {},
            io.BytesIO(b""),
        )

    monkeypatch.setattr(research_http, "_http_request", fake_request)

    with pytest.raises(RuntimeError, match="failed after"):
        research_http.read_json_with_retry(
            "https://example.invalid/missing",
            label="missing request",
            attempts=4,
        )
    assert calls == 1


def test_local_orderbook_archive_prefers_deeper_snapshot(tmp_path: Path) -> None:
    shallow = tmp_path / "2026-08-01_BTCUSDT_ob50.data.zip"
    deep = tmp_path / "2026-08-01_BTCUSDT_ob200.data.zip"
    shallow.write_bytes(b"x")
    deep.write_bytes(b"xx")

    result = find_local_orderbook_archive(
        tmp_path,
        symbol="BTCUSDT",
        day=date(2026, 8, 1),
    )

    assert result == (deep, 200)


def test_local_orderbook_archive_returns_none_when_missing(tmp_path: Path) -> None:
    assert (
        find_local_orderbook_archive(
            tmp_path,
            symbol="ETHUSDT",
            day=date(2026, 8, 1),
        )
        is None
    )
