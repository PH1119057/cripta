"""Pure fail-closed Release Controller request verifier (Issue #288).

Authentication evidence is supplied from GitHub's immutable comment metadata.
This module performs no network or filesystem mutation.
"""
import re
from datetime import UTC, datetime

OWNER_LOGIN = "PH1119057"
REPOSITORY = "PH1119057/cripta"
PURPOSE = "EXACT_VERIFIED_SOURCE_RELEASE"
FIELDS = {
    "repository",
    "purpose",
    "target_commit",
    "expected_baseline",
    "nonce",
    "expires_at",
}
SHA = re.compile(r"[0-9a-f]{40}\Z")
NONCE = re.compile(r"[A-Za-z0-9_-]{24,128}\Z")
MAX_APPROVAL_TTL_SECONDS = 15 * 60


class ReleaseDenied(ValueError):
    """Explicit fail-closed release rejection."""


def verify_request(
    *,
    request,
    approval_actor,
    approval_created_at,
    approval_unedited,
    now,
    remote_main,
    source_head,
    source_fast_forward_ok,
    installed_runtime,
    installed_tooling,
    required_checks,
    source_clean,
    actor_paths_ok,
    rollback_ready,
    release_lock_available,
    nonce_unused,
):
    """Validate independently collected facts and return exact release identity."""
    if not isinstance(request, dict) or set(request) != FIELDS:
        raise ReleaseDenied("REQUEST_FIELDS_INVALID")
    if approval_actor != OWNER_LOGIN or approval_unedited is not True:
        raise ReleaseDenied("OWNER_APPROVAL_INVALID")
    if not isinstance(approval_created_at, datetime) or approval_created_at.tzinfo is None:
        raise ReleaseDenied("APPROVAL_TIME_INVALID")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ReleaseDenied("NOW_INVALID")

    if request["repository"] != REPOSITORY or request["purpose"] != PURPOSE:
        raise ReleaseDenied("REQUEST_SCOPE_INVALID")
    target = request["target_commit"]
    baseline = request["expected_baseline"]
    nonce = request["nonce"]
    expires = request["expires_at"]
    if any(not isinstance(value, str) or not SHA.fullmatch(value) for value in (target, baseline)):
        raise ReleaseDenied("RELEASE_SHA_INVALID")
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        raise ReleaseDenied("NONCE_INVALID")
    if type(expires) is not int:
        raise ReleaseDenied("REQUEST_TIME_INVALID")

    created_ts = int(approval_created_at.astimezone(UTC).timestamp())
    now_ts = int(now.astimezone(UTC).timestamp())
    if created_ts > now_ts + 30:
        raise ReleaseDenied("APPROVAL_FROM_FUTURE")
    if expires <= now_ts or expires <= created_ts:
        raise ReleaseDenied("REQUEST_NOT_CURRENT")
    if expires - created_ts > MAX_APPROVAL_TTL_SECONDS:
        raise ReleaseDenied("REQUEST_TTL_TOO_LONG")

    if remote_main != target:
        raise ReleaseDenied("REMOTE_MAIN_MISMATCH")
    if not isinstance(source_head, str) or not SHA.fullmatch(source_head):
        raise ReleaseDenied("SOURCE_HEAD_INVALID")
    if source_fast_forward_ok is not True:
        raise ReleaseDenied("SOURCE_NOT_FAST_FORWARD")
    if installed_runtime != baseline or installed_tooling != baseline:
        raise ReleaseDenied("BASELINE_MISMATCH")

    if not isinstance(required_checks, dict):
        raise ReleaseDenied("CI_EVIDENCE_INVALID")
    if required_checks.get("scope") != "success":
        raise ReleaseDenied("SCOPE_NOT_PASS")
    if required_checks.get("quality") != "success":
        raise ReleaseDenied("QUALITY_NOT_PASS")
    if any(status != "success" for status in required_checks.values()):
        raise ReleaseDenied("REQUIRED_CI_NOT_PASS")

    for passed, label in (
        (source_clean, "SOURCE_DIRTY"),
        (actor_paths_ok, "ACTOR_PATH_DENIED"),
        (rollback_ready, "ROLLBACK_NOT_READY"),
        (release_lock_available, "RELEASE_LOCKED"),
        (nonce_unused, "REQUEST_REPLAY"),
    ):
        if passed is not True:
            raise ReleaseDenied(label)
    return target, baseline, nonce
