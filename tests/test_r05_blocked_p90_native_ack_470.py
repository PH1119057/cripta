"""R05 #470: native technical ACK survives a blocked private P90 preparation.

The ONLY allowed effect in this path is validation and atomic technical native
transport ACK. It never simulates signed MAIN/SUB or changes Strategy/Entry/Exit.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.monitoring import native_public_trade_observer_delivery as delivery
from operations.monitoring import universal_entry_shadow as observer


_BLOCKED = {
    "state": "BLOCKED",
    "reason": "P90_REAL_FLAT_ACCOUNT_PRIVATE_PROOF_RETRY_BUDGET_EXPIRED",
}
_NOW = datetime(2026, 11, 11, 17, 20, tzinfo=UTC)
_SESSION = "a" * 32
_SHA = "b" * 40
_FIVE = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")


def test_blocked_private_proof_does_not_suppress_native_transport_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def ack(connection: object, **kwargs: object) -> object:
        calls.append((connection, kwargs))
        return SimpleNamespace(source_session_id=_SESSION, events=(1, 2))

    connection = object()
    monkeypatch.setattr(observer, "EXTERNAL_NATIVE_SOURCE", True)
    monkeypatch.setattr(observer, "LOADED_COMMIT", _SHA)
    monkeypatch.setattr(
        delivery, "acknowledge_native_technical_batch", ack,
    )
    outcome = observer._blocked_p90_native_transport_ack(
        connection, _BLOCKED,
    )
    assert outcome == ("TECHNICAL_CAUGHT_UP", _SESSION)
    assert len(calls) == 1
    assert calls[0][0] is connection
    assert calls[0][1]["symbols"] == _FIVE
    assert calls[0][1]["source_checkout"] == Path(
        "/srv/cripta/source_checkout"
    )
    assert calls[0][1]["loaded_consumer_commit"] == _SHA
    assert calls[0][1]["limit"] == 256


@pytest.mark.parametrize(
    "detail,external",
    [
        ({"state": "BLOCKED", "reason": "OTHER_SOURCE_FAILURE"}, True),
        (_BLOCKED, False),
        ({"state": "PREPARING", "reason": _BLOCKED["reason"]}, True),
    ],
)
def test_unrelated_blocks_never_enable_technical_ack(
    monkeypatch: pytest.MonkeyPatch,
    detail: dict[str, str],
    external: bool,
) -> None:
    monkeypatch.setattr(observer, "EXTERNAL_NATIVE_SOURCE", external)
    monkeypatch.setattr(
        delivery, "acknowledge_native_technical_batch",
        lambda *_a, **_k: pytest.fail("unexpected native source ACK"),
    )
    assert observer._blocked_p90_native_transport_ack(object(), detail) == (
        "NOT_ENABLED", None,
    )


def test_native_proof_failure_remains_blocked_without_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observer, "EXTERNAL_NATIVE_SOURCE", True)

    def fail(*_args: object, **_kwargs: object) -> object:
        raise delivery.NativeObserverDeliveryBlocked(
            "NATIVE_OBSERVER_SOURCE_PROOF_UNAVAILABLE"
        )

    monkeypatch.setattr(delivery, "acknowledge_native_technical_batch", fail)
    state, epoch = observer._blocked_p90_native_transport_ack(
        object(), _BLOCKED,
    )
    assert state == "BLOCKED:NATIVE_OBSERVER_SOURCE_PROOF_UNAVAILABLE"
    assert epoch is None


def test_fatal_pg_integrity_errors_are_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observer, "EXTERNAL_NATIVE_SOURCE", True)

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("DATABASE_INTEGRITY_UNKNOWN")

    monkeypatch.setattr(delivery, "acknowledge_native_technical_batch", fail)
    with pytest.raises(RuntimeError, match="DATABASE_INTEGRITY_UNKNOWN"):
        observer._blocked_p90_native_transport_ack(object(), _BLOCKED)


def test_status_remains_financially_blocked_while_technical_ack_is_reported() -> None:
    runtime = Path(
        "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    module = runtime.split("def wait_blocked(", 1)[1].split(
        "with psycopg.connect(", 1
    )[0]
    assert "_blocked_p90_native_transport_ack(" in module
    assert "connection, detail," in module
    assert 'state="BLOCKED"' in module
    assert '"native_pg_delivery"' in module
    assert '"native_pg_session_id"' in module
    assert '"BLOCKED_P90_TECHNICAL_TRANSPORT_ONLY"' in module
    assert "preparation_budget.reset()" in module
    assert "preflight_p90_real_private_flat_source" not in module
    assert "OWNER_ARM" not in module
    assert "send_order" not in module
