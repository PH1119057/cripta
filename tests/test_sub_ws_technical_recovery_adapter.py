"""Offline behavior regression for exact SUB WS technical recovery adapter.

All sources here are fakes: no Bybit, production SQL, systemd, gate or ARM.
"""
from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.monitoring import sub_ws_technical_recovery_adapter as adapter


def _sample() -> tuple[datetime, datetime, datetime]:
    now = datetime.now(UTC)
    return now, now - timedelta(minutes=1), now - timedelta(seconds=3)


def test_missing_release_stops_before_any_account_or_signed_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter, "_release", lambda: "")
    monkeypatch.setattr(
        adapter, "_latch_and_reconnect",
        lambda: pytest.fail("should not query blocked account"),
    )
    assert adapter.run_once(now=datetime.now(UTC))["reason"] == (
        "RELEASE_IDENTITY_UNVERIFIED"
    )


def test_no_latch_does_not_call_exchange_or_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter, "_release", lambda: "a" * 40)
    monkeypatch.setattr(adapter, "_latch_and_reconnect", lambda: (None, None))
    monkeypatch.setattr(
        adapter, "P90PrivateProofClient",
        lambda **_kwargs: pytest.fail("not needed"),
    )
    assert adapter.run_once(now=datetime.now(UTC))["state"] == "NO_LATCH"


@pytest.mark.parametrize(
    ("reason", "journal", "expected"),
    [
        ("PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED", None,
         "NONTRANSIENT_ACCOUNT_SAFETY_BLOCK"),
        ("PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN",
         {"attempts": 1, "phase": "PENDING"},
         "PRIOR_RECOVERY_OUTCOME_UNCERTAIN"),
        ("PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN",
         {"attempts": 2, "phase": "IDLE"},
         "RECOVERY_ATTEMPT_BUDGET_EXHAUSTED"),
    ],
)
def test_hard_latch_and_uncertain_operation_never_request_signed_proof(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    journal: dict[str, object] | None,
    expected: str,
) -> None:
    now, block, event = _sample()
    monkeypatch.setattr(adapter, "_release", lambda: "a" * 40)
    monkeypatch.setattr(
        adapter, "_latch_and_reconnect",
        lambda: ((block, reason), (12, event)),
    )
    monkeypatch.setattr(
        adapter, "_journal", lambda *_args: journal or {"attempts": 0, "phase": "IDLE"},
    )
    monkeypatch.setattr(
        adapter, "P90PrivateProofClient",
        lambda **_kwargs: pytest.fail("signed proof should not be called"),
    )
    assert adapter.run_once(now=now)["reason"] == expected


def test_global_critical_or_unknown_exposure_blocks_before_signed_ipc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now, blocked, event = _sample()
    monkeypatch.setattr(adapter, "_release", lambda: "a" * 40)
    monkeypatch.setattr(
        adapter, "_latch_and_reconnect",
        lambda: ((blocked, "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN"), (3, event)),
    )
    monkeypatch.setattr(
        adapter, "_journal", lambda *_args: {"attempts": 0, "phase": "IDLE"},
    )
    monkeypatch.setattr(adapter, "_independent_lifecycle", lambda *_args: (False, False))
    monkeypatch.setattr(
        adapter, "P90PrivateProofClient",
        lambda **_kwargs: pytest.fail("unknown exposure forbids IPC read"),
    )
    assert adapter.run_once(now=now)["reason"] == (
        "CRITICAL_OR_EXIT_HEALTH_UNVERIFIED"
    )


def test_verified_flat_recovery_requests_only_scoped_database_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now, blocked, reconnect = _sample()
    source = "bybit:signed-flat-linear-usdt:exact-account"
    proof = SimpleNamespace(
        trading_account_id=adapter.ACCOUNT,
        private_generation_id="generation-fixture",
        signed_uid="123456",
        source_ref=source,
        observed_at=now,
        pending_commands=0,
    )
    saves: list[tuple[int, str]] = []
    calls: list[tuple[str, object]] = []

    class FakeCursor:
        def __init__(self, answer: object) -> None:
            self.answer = answer

        def fetchone(self) -> object:
            return self.answer

    class FakeDB:
        def __enter__(self) -> FakeDB:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, params: object = ()) -> FakeCursor:
            calls.append((sql, params))
            assert "ISOLATION LEVEL SERIALIZABLE" in sql or (
                "control.clear_sub_ws_transient_latch" in sql
            )
            return FakeCursor((adapter.ACCOUNT,))

        def commit(self) -> None:
            calls.append(("COMMIT", None))

    class FakeProofClient:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["account"] == adapter.ACCOUNT
            assert kwargs["connector"] == adapter.CONNECTOR

        def read_flat_account(self) -> SimpleNamespace:
            return proof

    monkeypatch.setattr(adapter, "_release", lambda: "a" * 40)
    monkeypatch.setattr(
        adapter, "_latch_and_reconnect",
        lambda: ((blocked, "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN"),
                 (41, reconnect)),
    )
    monkeypatch.setattr(
        adapter, "_journal", lambda *_args: {"attempts": 0, "phase": "IDLE"},
    )
    monkeypatch.setattr(adapter, "_independent_lifecycle", lambda _now: (True, True))
    monkeypatch.setattr(
        adapter, "_ws_heartbeat", lambda **_kwargs: now,
    )
    monkeypatch.setattr(
        adapter, "_generation_and_uid",
        lambda *_args: (
            "123456", "generation-fixture", "COMPLETE",
            now - timedelta(seconds=1), 0, 0,
        ),
    )
    monkeypatch.setattr(
        adapter, "_save",
        lambda **kwargs: saves.append((kwargs["attempts"], kwargs["phase"])),
    )
    monkeypatch.setattr(adapter, "P90PrivateProofClient", FakeProofClient)
    monkeypatch.setattr(adapter.psycopg, "connect", lambda *_args, **_kwargs: FakeDB())
    result = adapter.run_once(now=now)
    assert result["state"] == "RECOVERED"
    assert result["owner_arm_change"] is False
    assert result["gate_change"] is False
    assert result["exchange_mutation"] is False
    assert saves == [(1, "PENDING"), (1, "COMPLETE")]
    assert sum("control.clear_sub_ws_transient_latch" in sql for sql, _ in calls) == 1
    assert calls[-1] == ("COMMIT", None)


def test_connection_status_file_alone_is_not_signed_exchange_evidence() -> None:
    source = Path(
        "operations/monitoring/sub_ws_technical_recovery_adapter.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "P90PrivateProofClient(" in source
    assert "read_flat_account()" in source
    assert "control.clear_sub_ws_transient_latch" in source
    assert "SET TRANSACTION READ ONLY" in source
    assert "ISOLATION LEVEL SERIALIZABLE" in source
    assert "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN" in source
    assert "active_fault_codes" in source
    assert "unknown_open_count" in source
    assert "_save(" in source
    assert "phase=\"PENDING\"" in source
    assert "os.system" not in source
    assert "subprocess" not in source
    assert "DELETE FROM runtime.account_entry_safety_blocks" not in source
    assert "UPDATE control.execution_gates" not in source
    assert "INSERT INTO control.live_arm_sessions" not in source
    assert len(list(ast.walk(tree))) > 10
