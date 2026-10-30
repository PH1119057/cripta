"""Negative contract tests for Release Controller approval gate (#288)."""
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).resolve().parents[1]
    / "operations/infrastructure/release_controller_gate.py"
)
spec = importlib.util.spec_from_file_location("release_controller_gate", MODULE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

TARGET = "a" * 40
BASELINE = "b" * 40
NOW = datetime(2026, 10, 31, 0, 0, tzinfo=UTC)
CREATED = datetime(2026, 10, 30, 23, 59, tzinfo=UTC)
NONCE = "nonce_00000000000000000001"


def valid():
    request = {
        "repository": "PH1119057/cripta",
        "purpose": "EXACT_VERIFIED_SOURCE_RELEASE",
        "target_commit": TARGET,
        "expected_baseline": BASELINE,
        "nonce": NONCE,
        "expires_at": int(CREATED.timestamp()) + 600,
    }
    return {
        "request": request,
        "approval_actor": "PH1119057",
        "approval_created_at": CREATED,
        "approval_unedited": True,
        "now": NOW,
        "remote_main": TARGET,
        "source_head": BASELINE,
        "installed_runtime": BASELINE,
        "installed_tooling": BASELINE,
        "required_checks": {"scope": "success", "quality": "success"},
        "source_clean": True,
        "actor_paths_ok": True,
        "rollback_ready": True,
        "release_lock_available": True,
        "nonce_unused": True,
    }


def test_gate_accepts_exact_owner_approved_release():
    assert gate.verify_request(**valid()) == (TARGET, BASELINE, NONCE)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"approval_actor": "someone-else"}, "OWNER_APPROVAL_INVALID"),
        ({"approval_unedited": False}, "OWNER_APPROVAL_INVALID"),
        ({"remote_main": "c" * 40}, "REMOTE_MAIN_MISMATCH"),
        ({"source_head": "c" * 40}, "SOURCE_HEAD_UNEXPECTED"),
        ({"installed_runtime": "c" * 40}, "BASELINE_MISMATCH"),
        ({"installed_tooling": "c" * 40}, "BASELINE_MISMATCH"),
        ({"required_checks": {"scope": "failure", "quality": "success"}}, "SCOPE_NOT_PASS"),
        ({"required_checks": {"scope": "success"}}, "QUALITY_NOT_PASS"),
        ({"source_clean": False}, "SOURCE_DIRTY"),
        ({"actor_paths_ok": False}, "ACTOR_PATH_DENIED"),
        ({"rollback_ready": False}, "ROLLBACK_NOT_READY"),
        ({"release_lock_available": False}, "RELEASE_LOCKED"),
        ({"nonce_unused": False}, "REQUEST_REPLAY"),
    ],
)
def test_gate_rejects_invalid_external_evidence(mutation, expected):
    params = valid()
    params.update(mutation)
    with pytest.raises(gate.ReleaseDenied, match=expected):
        gate.verify_request(**params)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("target_commit", "main", "RELEASE_SHA_INVALID"),
        ("expected_baseline", "main", "RELEASE_SHA_INVALID"),
        ("nonce", "short", "NONCE_INVALID"),
        ("repository", "other/repo", "REQUEST_SCOPE_INVALID"),
        ("purpose", "OWNER_ARM", "REQUEST_SCOPE_INVALID"),
        ("expires_at", int(NOW.timestamp()) - 1, "REQUEST_NOT_CURRENT"),
        ("expires_at", int(CREATED.timestamp()) + 901, "REQUEST_TTL_TOO_LONG"),
    ],
)
def test_gate_rejects_disallowed_request_fields(field, value, expected):
    params = valid()
    params["request"][field] = value
    with pytest.raises(gate.ReleaseDenied, match=expected):
        gate.verify_request(**params)


def test_gate_accepts_source_already_synced_to_target():
    params = valid()
    params["source_head"] = TARGET
    assert gate.verify_request(**params) == (TARGET, BASELINE, NONCE)


def test_gate_rejects_unexpected_fields():
    params = valid()
    params["request"]["command"] = "bash"
    with pytest.raises(gate.ReleaseDenied, match="REQUEST_FIELDS_INVALID"):
        gate.verify_request(**params)
