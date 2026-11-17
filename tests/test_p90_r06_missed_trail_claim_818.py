from __future__ import annotations

import inspect
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


def test_r06_refreshes_stable_logical_exit_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from operations.connectivity import p90_r06_exit_owner as owner

    seen = []
    monkeypatch.setattr(
        owner,
        "claim_exit_position",
        lambda *args, **kwargs: seen.append((args, kwargs)) or "claim",
    )
    position = SimpleNamespace(strategy_position_id="POS-exact")
    at = datetime(2026, 11, 17, 8, 20, tzinfo=UTC)
    db = object()

    owner._refresh_exit_claim(db, position=position, claimed_at=at)

    assert len(seen) == 1
    args, kwargs = seen[0]
    assert args == (db,)
    assert kwargs["strategy_position_id"] == "POS-exact"
    assert kwargs["consumer_instance_id"] == "universal-exit-shadow-v1"
    assert kwargs["claimed_at"] == at
    assert kwargs["payload"] == {
        "runtime": "p90_r06_exit_owner",
        "mode": "P90_R06_INDEPENDENT_EXIT",
    }


def test_r06_refreshes_claim_before_processing_open_position() -> None:
    from operations.connectivity import p90_r06_exit_owner as owner

    body = inspect.getsource(owner.run_p90_r06_exit_once)
    assert body.index("_refresh_exit_claim(") < body.index("_latest_mfe(")


def test_status_preserves_safe_nested_policy_reason_only() -> None:
    from operations.connectivity import universal_exit_consumer as consumer

    exact = RuntimeError(
        "P90_REAL_CLOSED_5M_EXIT_POLICY_DENIED:"
        "P90_REAL_CLOSED_5M_BYBIT_TRAILING_NOT_ARMED"
    )
    assert consumer._safe_r06_error(exact) == str(exact)
    assert consumer._safe_r06_error(
        RuntimeError("secret token=https://unsafe.example")
    ) == "RuntimeError"
    assert consumer._safe_r06_error(
        RuntimeError("P90_BAD:provider payload with spaces")
    ) == "P90_BAD"