"""Pure fail-closed request verifier for Release Controller, Issue #288.

Not an executable release rail. External evidence, authenticated approval issuer,
nonce claim, permission preflight and installer binding remain separate gates.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone

SHA = re.compile(r"[0-9a-f]{40}\Z")
NONCE = re.compile(r"[A-Za-z0-9_-]{24,128}\Z")
FIELDS = {"repository", "purpose", "target_commit", "expected_baseline", "nonce", "issued_at", "expires_at"}


class ReleaseDenied(ValueError):
    pass


def verify_request(*, request, signature, key, now, remote_main,
                   installed_runtime, installed_tooling, required_checks,
                   source_clean, actor_paths_ok, rollback_ready,
                   release_lock_available, nonce_unused):
    """Validate externally obtained facts without performing any side effects."""
    if not isinstance(request, dict) or set(request) != FIELDS:
        raise ReleaseDenied("REQUEST_FIELDS_INVALID")
    if request["repository"] != "PH1119057/cripta" or request["purpose"] != "EXACT_VERIFIED_SOURCE_RELEASE":
        raise ReleaseDenied("REQUEST_SCOPE_INVALID")
    target, baseline, nonce = (request[k] for k in ("target_commit", "expected_baseline", "nonce"))
    if any(not isinstance(v, str) or not SHA.fullmatch(v) for v in (target, baseline)):
        raise ReleaseDenied("RELEASE_SHA_INVALID")
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        raise ReleaseDenied("NONCE_INVALID")
    issued, expires = request["issued_at"], request["expires_at"]
    if any(type(v) is not int for v in (issued, expires)):
        raise ReleaseDenied("REQUEST_TIME_INVALID")
    ts = int(now.astimezone(timezone.utc).timestamp())
    if issued > ts or expires <= ts or expires <= issued or expires - issued > 900:
        raise ReleaseDenied("REQUEST_NOT_CURRENT")
    if not isinstance(key, bytes) or len(key) < 32:
        raise ReleaseDenied("APPROVAL_KEY_INVALID")
    if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise ReleaseDenied("APPROVAL_SIGNATURE_INVALID")
    message = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ReleaseDenied("APPROVAL_SIGNATURE_MISMATCH")
    if target != remote_main:
        raise ReleaseDenied("REMOTE_MAIN_MISMATCH")
    if baseline != installed_runtime or baseline != installed_tooling:
        raise ReleaseDenied("BASELINE_MISMATCH")
    if not required_checks or required_checks.get("scope") != "success" or any(v != "success" for v in required_checks.values()):
        raise ReleaseDenied("CI_NOT_PASS")
    for passed, label in ((source_clean, "SOURCE_DIRTY"), (actor_paths_ok, "ACTOR_PATH_DENIED"),
                          (rollback_ready, "ROLLBACK_NOT_READY"), (release_lock_available, "RELEASE_LOCKED"),
                          (nonce_unused, "REQUEST_REPLAY")):
        if passed is not True:
            raise ReleaseDenied(label)
    return (target, baseline, nonce)
