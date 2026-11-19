"""Pre-send R10 rejection is distinct from an uncertain sent mutation.

This is a mocked private worker test: no Exchange, production DB or LIVE rights.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research/server/connectivity"))
sys.path.insert(0, str(ROOT / "operations/connectivity"))

import private_runtime  # noqa: E402

SOURCE = (ROOT / "operations/connectivity/private_runtime.py").read_text(
    encoding="utf-8"
)


class DB:
    def __init__(self) -> None:
        self.statements: list[tuple[str, object]] = []
        self.rollbacks = 0
        self.commits = 0

    def execute(self, sql: str, params: object = ()) -> None:
        self.statements.append((sql, params))

    def rollback(self) -> None:
        self.rollbacks += 1

    def commit(self) -> None:
        self.commits += 1


def exercise(exception: Exception) -> tuple[DB, list[str], list[dict[str, object]]]:
    db = DB()
    latches: list[str] = []
    events: list[dict[str, object]] = []

    def record_event(_connection: object, _channel: str, name: str,
                     **values: object) -> None:
        events.append({"name": name, **values})

    with (
        patch.object(private_runtime, "finalize_failed_entry_command_reservation"),
        patch.object(private_runtime, "disarm_new_entries",
                     side_effect=lambda _db, reason: latches.append(reason)),
        patch.object(private_runtime, "connection_event", side_effect=record_event),
        patch.object(private_runtime, "atomic_status"),
    ):
        private_runtime.handle_pre_mutation_safety_block(db, "cmd-511", exception)
    return db, latches, events


def test_p90_r10_price_failure_does_not_install_unknown_mutation_latch() -> None:
    exception = private_runtime.PreMutationSafetyBlock(
        "R10_P90_ORIGINAL_PUBLICATION_INVALID:R10_EXECUTION_TICKER_FIELDS_NOT_FRESH"
    )
    db, latches, events = exercise(exception)
    assert not latches
    assert db.commits >= 1
    assert any("SET state='failed'" in sql for sql, _params in db.statements)
    assert events == [{
        "name": "pre_send_market_publication_blocked",
        "error": "R10_P90_ORIGINAL_PUBLICATION_INVALID:R10_EXECUTION_TICKER_FIELDS_NOT_FRESH",
        "mutation_sent": False,
    }]


def test_sub_r10_safe_original_reason_survives_without_disarming_unrelated_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(private_runtime, "_is_sub_private_worker", lambda: True)
    exception = private_runtime.PreMutationSafetyBlock(
        "R10_P90_ORIGINAL_PUBLICATION_INVALID:"
        "R10_EXECUTION_TICKER_SOURCE_INVALID:R08_ONE_ACTIVE_SOURCE_EPOCH_REQUIRED"
    )
    db, latches, events = exercise(exception)
    assert not latches
    command_error = [
        params[1] for sql, params in db.statements
        if "SET state='failed'" in sql
    ][0]
    assert "R08_ONE_ACTIVE_SOURCE_EPOCH_REQUIRED" in command_error
    assert events[0]["error"].endswith("R08_ONE_ACTIVE_SOURCE_EPOCH_REQUIRED")
    assert events[0]["mutation_sent"] is False


def test_untrusted_r10_exception_payload_must_never_be_persisted_on_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(private_runtime, "_is_sub_private_worker", lambda: True)
    exception = private_runtime.PreMutationSafetyBlock(
        "R10_P90_ORIGINAL_PUBLICATION_INVALID:RAW SECRET=never"
    )
    db, latches, events = exercise(exception)
    assert not latches
    command_error = [
        params[1] for sql, params in db.statements
        if "SET state='failed'" in sql
    ][0]
    assert "RAW SECRET" not in command_error
    assert events[0]["error"] == "R10_P90_ORIGINAL_PUBLICATION_INVALID"



@pytest.mark.parametrize("reason", [
    "CRITICAL_LIFECYCLE_FAULT:POSITION_WITHOUT_EXIT_OWNER",
    "IDENTITY_MISMATCH",
    "UNCLASSIFIED",
])
def test_unclassified_pre_send_block_remains_durably_fail_closed(reason: str) -> None:
    _db, latches, _events = exercise(
        private_runtime.PreMutationSafetyBlock(reason)
    )
    assert len(latches) == 1


def test_post_sent_unknown_still_has_separate_durable_barrier() -> None:
    handler = SOURCE.split("def handle_exchange_mutation_barrier(", 1)[1].split(
        "def ", 1
    )[0]
    assert "mutation_ambiguous=True" in handler
    assert "disarm_new_entries" in handler
    assert "os._exit(75)" in handler


def test_pre_send_failure_requires_fresh_validation_next_attempt() -> None:
    executor = SOURCE.split("def execute_command(", 1)[1].split(
        "def ", 1
    )[0]
    assert "R10_P90_ORIGINAL_PUBLICATION_INVALID" in executor
    assert "validate_real_v3_attached_protection_at_original_quote" in executor
    assert "authenticated_entry_connector_gate(connection, key, secret)" in executor
    assert "account_entry_safety_ready(" in executor
