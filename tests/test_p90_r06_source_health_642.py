"""R06 source-only and account-scoped financial Entry are distinct owners.

The release ARM guard must accept the proven no-order R06 source without
granting R06 any financial capability or weakening consumer/release freshness.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from operations.monitoring import lifecycle_supervisor_runtime as lifecycle

COMMIT = "7" * 40
AT = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)


def _health(payload: dict[str, object]) -> tuple[bool, str]:
    with patch.object(lifecycle.Path, "read_text", return_value=json.dumps(payload)):
        return lifecycle._entry_source_health(loaded_commit=COMMIT, now=AT)


def _r06_status() -> dict[str, object]:
    return {
        "updated_at": (AT - timedelta(seconds=2)).isoformat(),
        "state": "R06_PG_NO_ORDER_SOURCE_ONLY",
        "reason": "verified PG-only; NO financial Entry/Exit permissions",
        "observer_ready": True,
        "trading_effect": "NONE",
        "source_commit": COMMIT,
        "sensor_status": {"financial_ready": False},
    }


def test_exact_r06_no_order_source_is_ready_for_separate_account_consumer() -> None:
    assert _health(_r06_status()) == (True, "READY")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trading_effect", "ENTRY_REQUEST"),
        ("sensor_status", {"financial_ready": True}),
        ("sensor_status", {}),
        ("observer_ready", False),
        ("source_commit", "8" * 40),
        ("state", "WARMUP"),
    ],
)
def test_r06_drift_never_qualifies_as_financial_source(
    field: str, value: object,
) -> None:
    status = _r06_status()
    status[field] = value
    ready, reason = _health(status)
    assert ready is False
    assert reason.startswith("OBSERVER_NOT_READY:")


def test_r06_stale_status_still_fails_closed() -> None:
    status = _r06_status()
    status["updated_at"] = (AT - timedelta(seconds=16)).isoformat()
    assert _health(status)[0] is False


def test_regular_running_observer_contract_is_preserved() -> None:
    status = _r06_status()
    status["state"] = "RUNNING"
    status["sensor_status"] = {}
    assert _health(status) == (True, "READY")
