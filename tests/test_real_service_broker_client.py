from __future__ import annotations

import json

import pytest

from bybit_workbench import real_service_broker_client as client


class _FakeSocket:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = (json.dumps(response) + "\n").encode()
        self.sent = b""

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def connect(self, _path: str) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, _size: int) -> bytes:
        response, self._response = self._response, b""
        return response


def _patch_socket(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
) -> _FakeSocket:
    fake = _FakeSocket(response)
    monkeypatch.setattr(client.socket, "socket", lambda *_args, **_kwargs: fake)
    return fake


def test_broker_client_accepts_exact_success(monkeypatch: pytest.MonkeyPatch) -> None:
    request_id = "owner-ready-1234"
    release = "a" * 40
    fake = _patch_socket(
        monkeypatch,
        {
            "ok": True,
            "request_id": request_id,
            "release_commit": release,
            "units": [],
        },
    )
    result = client.start_services(
        request_id=request_id,
        release_commit=release,
        units=("cripta-private-runtime.service",),
    )
    assert result["ok"] is True
    sent = json.loads(fake.sent.decode().strip())
    assert sent == {
        "action": "START",
        "request_id": request_id,
        "release_commit": release,
        "units": ["cripta-private-runtime.service"],
    }


def test_broker_client_preserves_bounded_failure_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "ok": False,
        "request_id": "owner-ready-1234",
        "release_commit": "a" * 40,
        "error": "SERVICE_START_FAILED:cripta-private-runtime.service",
        "units": [
            {
                "unit": "cripta-private-runtime.service",
                "before": "inactive",
                "after": "failed",
                "started": False,
                "detail": "unit entered failed state",
            }
        ],
    }
    _patch_socket(monkeypatch, response)
    with pytest.raises(
        client.ServiceBrokerClientError,
        match="SERVICE_START_FAILED.*unit entered failed state",
    ) as caught:
        client.start_services(
            request_id="owner-ready-1234",
            release_commit="a" * 40,
            units=("cripta-private-runtime.service",),
        )
    assert caught.value.response == response


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            {
                "ok": True,
                "request_id": "wrong-request",
                "release_commit": "a" * 40,
                "units": [],
            },
            "SERVICE_BROKER_REQUEST_ID_MISMATCH",
        ),
        (
            {
                "ok": True,
                "request_id": "owner-ready-1234",
                "release_commit": "b" * 40,
                "units": [],
            },
            "SERVICE_BROKER_RELEASE_MISMATCH",
        ),
    ],
)
def test_broker_client_rejects_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
    expected: str,
) -> None:
    _patch_socket(monkeypatch, response)
    with pytest.raises(client.ServiceBrokerClientError, match=expected):
        client.start_services(
            request_id="owner-ready-1234",
            release_commit="a" * 40,
            units=("cripta-private-runtime.service",),
        )
