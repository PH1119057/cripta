"""Fail-closed, secret-safe restart position proof error diagnostics.

Synthetic only: no Bybit/PG/credentials/IPC. The signed proof requirement
is never skipped, and unreviewed exception messages must never escape.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bybit_workbench.universal_entry.p90l90_real_open_restart import (
    P90RealOpenRestartUnverified,
    _raise_position_signed_evidence_failure,
)
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateIpcUnverified,
    P90PrivateRecoveryBlocked,
)


@pytest.mark.parametrize(("source_code", "expected"), [
    ("P90_PRIVATE_IPC_TRANSPORT_UNAVAILABLE", "IPC:P90_PRIVATE_IPC_TRANSPORT_UNAVAILABLE"),
    ("P90_PRIVATE_IPC_PEER_NOT_VERIFIED", "IPC:P90_PRIVATE_IPC_PEER_NOT_VERIFIED"),
    ("P90_PRIVATE_IPC_EVIDENCE_STALE", "IPC:P90_PRIVATE_IPC_EVIDENCE_STALE"),
    ("P90_PRIVATE_IPC_RECOVERY_POSITION_INVALID", "IPC:P90_PRIVATE_IPC_RECOVERY_POSITION_INVALID"),
])
def test_exact_whitelisted_ipc_failure_is_diagnostic_and_fail_closed(
    source_code: str, expected: str,
) -> None:
    with pytest.raises(P90RealOpenRestartUnverified) as raised:
        _raise_position_signed_evidence_failure(P90PrivateIpcUnverified(source_code))
    assert str(raised.value) == "P90_REAL_RESTART_POSITION_SIGNED_" + expected


def test_exact_producer_recovery_block_remains_a_safe_category() -> None:
    with pytest.raises(P90RealOpenRestartUnverified) as raised:
        _raise_position_signed_evidence_failure(
            P90PrivateRecoveryBlocked("P90_REAL_RECOVERY_PRIVATE_WS_UNVERIFIED")
        )
    assert str(raised.value) == (
        "P90_REAL_RESTART_POSITION_SIGNED_RECOVERY:"
        "P90_REAL_RECOVERY_PRIVATE_WS_UNVERIFIED"
    )


@pytest.mark.parametrize("exception", [
    RuntimeError("api_secret=do-not-leak"),
    P90PrivateIpcUnverified("P90_PRIVATE_IPC_unapproved id / hidden details"),
    ValueError("signed payload=do-not-log"),
])
def test_unknown_or_untrusted_exception_is_still_fail_closed_and_redacted(
    exception: Exception,
) -> None:
    with pytest.raises(P90RealOpenRestartUnverified) as raised:
        _raise_position_signed_evidence_failure(exception)
    assert str(raised.value) == "P90_REAL_RESTART_POSITION_SIGNED_EVIDENCE_MISSING"
    assert "do-not" not in str(raised.value)
    assert "hidden" not in str(raised.value)


def test_real_position_proof_catch_invokes_error_classifier() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src/bybit_workbench/universal_entry/p90l90_real_open_restart.py"
    ).read_text(encoding="utf-8")
    assert "except Exception as exc:" in source
    assert "_raise_position_signed_evidence_failure(exc)" in source
