"""Negative contract tests for read-only Release Controller gate (#288)."""
import hashlib
import hmac
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

MODULE = (Path(__file__).resolve().parents[1]
          / "operations/infrastructure/release_controller_gate.py")
spec = importlib.util.spec_from_file_location("release_controller_gate", MODULE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
SHA = "a" * 40
BASELINE = "b" * 40
KEY = b"k" * 32
NOW = datetime(2026, 10, 31, 0, 0, tzinfo=UTC)
NOW_TS = int(NOW.timestamp())


def valid():
    request = {"repository": "PH1119057/cripta", "purpose": "EXACT_VERIFIED_SOURCE_RELEASE",
               "target_commit": SHA, "expected_baseline": BASELINE,
               "nonce": "nonce_00000000000000000001", "issued_at": NOW_TS - 60,
               "expires_at": NOW_TS + 300}
    return {"request": request,
            "signature": hmac.new(KEY, json.dumps(request, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True).encode("ascii"), hashlib.sha256).hexdigest(),
            "key": KEY, "now": NOW, "remote_main": SHA,
            "installed_runtime": BASELINE, "installed_tooling": BASELINE,
            "required_checks": {"scope": "success", "quality": "success"},
            "source_clean": True, "actor_paths_ok": True, "rollback_ready": True,
            "release_lock_available": True, "nonce_unused": True}


def test_gate_accepts_independently_attested_request():
    assert gate.verify_request(**valid()) == (SHA, BASELINE, "nonce_00000000000000000001")


@pytest.mark.parametrize("mutation,expected", [
    ({"signature": "0" * 64}, "APPROVAL_SIGNATURE_MISMATCH"),
    ({"remote_main": "c" * 40}, "REMOTE_MAIN_MISMATCH"),
    ({"installed_runtime": "c" * 40}, "BASELINE_MISMATCH"),
    ({"installed_tooling": "c" * 40}, "BASELINE_MISMATCH"),
    ({"required_checks": {"scope": "failure", "quality": "success"}}, "CI_NOT_PASS"),
    ({"required_checks": {"quality": "success"}}, "CI_NOT_PASS"),
    ({"required_checks": {}}, "CI_NOT_PASS"),
    ({"source_clean": False}, "SOURCE_DIRTY"),
    ({"actor_paths_ok": False}, "ACTOR_PATH_DENIED"),
    ({"rollback_ready": False}, "ROLLBACK_NOT_READY"),
    ({"release_lock_available": False}, "RELEASE_LOCKED"),
    ({"nonce_unused": False}, "REQUEST_REPLAY"),
])
def test_gate_rejects_invalid_external_evidence(mutation, expected):
    params = valid()
    params.update(mutation)
    with pytest.raises(gate.ReleaseDenied, match=expected):
        gate.verify_request(**params)


@pytest.mark.parametrize("field,value,expected", [
    ("target_commit", "main", "RELEASE_SHA_INVALID"),
    ("nonce", "short", "NONCE_INVALID"),
    ("repository", "other/repo", "REQUEST_SCOPE_INVALID"),
    ("purpose", "OWNER_ARM", "REQUEST_SCOPE_INVALID"),
    ("issued_at", NOW_TS + 10, "REQUEST_NOT_CURRENT"),
    ("expires_at", NOW_TS - 1, "REQUEST_NOT_CURRENT"),
    ("expires_at", NOW_TS + 901, "REQUEST_NOT_CURRENT"),
])
def test_gate_rejects_signed_but_disallowed_requests(field, value, expected):
    params = valid()
    params["request"][field] = value
    params["signature"] = hmac.new(KEY, json.dumps(params["request"], sort_keys=True,
        separators=(",", ":"), ensure_ascii=True).encode("ascii"), hashlib.sha256).hexdigest()
    with pytest.raises(gate.ReleaseDenied, match=expected):
        gate.verify_request(**params)


def test_gate_rejects_unexpected_fields():
    params = valid()
    params["request"]["command"] = "bash"
    with pytest.raises(gate.ReleaseDenied, match="REQUEST_FIELDS_INVALID"):
        gate.verify_request(**params)
