"""Exact development-delegated P90 REAL v3 OWNER ARM/DISARM rail.

This is not a generic operator CLI. The exact systemd cgroup and owner-decision
marker authenticate the temporary development delegation. Trading scope/risk
cannot be passed as arguments.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

_DECISION = "P90_DEVELOPMENT_DELEGATION_20261116_V1"
_RELEASE = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME = Path("/srv/cripta/runtime/current")
_RELEASE_STATE = Path("/var/lib/cripta/release/INSTALLED_COMMIT")
_ACTION_UNITS = {
    "ARM": "cripta-p90-development-owner-arm.service",
    "DISARM": "cripta-p90-development-owner-disarm.service",
}
_OPERATOR = "owner-development-delegation-20261116"


class P90DevelopmentOwnerControlBlocked(RuntimeError):
    """Delegated control rail is not exact/current/authenticated."""


def _read_exact_release() -> str:
    release = os.environ.get("CRIPTA_RELEASE_COMMIT", "").strip().lower()
    if not _RELEASE.fullmatch(release):
        raise P90DevelopmentOwnerControlBlocked(
            "P90_DELEGATED_OWNER_RELEASE_ENV_INVALID"
        )
    runtime = (_RUNTIME / "INSTALLED_COMMIT").read_text(encoding="utf-8").strip()
    state = _RELEASE_STATE.read_text(encoding="utf-8").strip()
    if runtime != release or state != release:
        raise P90DevelopmentOwnerControlBlocked(
            "P90_DELEGATED_OWNER_RELEASE_IDENTITY_DRIFT"
        )
    resolved = _RUNTIME.resolve()
    if resolved.name != release or resolved.parent.name != "releases":
        raise P90DevelopmentOwnerControlBlocked(
            "P90_DELEGATED_OWNER_RUNTIME_LINK_INVALID"
        )
    return release


def _verify_systemd_invocation(action: str) -> None:
    expected = _ACTION_UNITS.get(action)
    if expected is None:
        raise P90DevelopmentOwnerControlBlocked(
            "P90_DELEGATED_OWNER_ACTION_INVALID"
        )
    account = pwd.getpwnam("cripta")
    if os.getuid() != account.pw_uid or os.getgid() != account.pw_gid:
        raise P90DevelopmentOwnerControlBlocked(
            "P90_DELEGATED_OWNER_ACTOR_INVALID"
        )
    if os.environ.get("CRIPTA_P90_OWNER_DECISION") != _DECISION:
        raise P90DevelopmentOwnerControlBlocked(
            "P90_DELEGATED_OWNER_DECISION_INVALID"
        )
    cgroup = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    if f"/system.slice/{expected}" not in cgroup:
        raise P90DevelopmentOwnerControlBlocked(
            "P90_DELEGATED_OWNER_SYSTEMD_SCOPE_INVALID"
        )


def _request_id(action: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (
        "p90-development-owner-"
        + action.lower()
        + "-"
        + stamp
        + "-"
        + secrets.token_hex(6)
    )


def main() -> int:
    if len(sys.argv) != 1:
        raise P90DevelopmentOwnerControlBlocked(
            "P90_DELEGATED_OWNER_ARGUMENTS_FORBIDDEN"
        )
    action = os.environ.get("CRIPTA_P90_DELEGATED_ACTION", "").strip().upper()
    _verify_systemd_invocation(action)
    release = _read_exact_release()

    # Import only after exact actor/systemd/release authentication.
    from operations.dashboard.app import (
        _execute_p90_real_v3_owner_arm,
        _execute_p90_real_v3_owner_disarm,
    )

    request_id = _request_id(action)
    if action == "ARM":
        result = _execute_p90_real_v3_owner_arm(
            operator=_OPERATOR,
            request_id=request_id,
        )
    else:
        result = _execute_p90_real_v3_owner_disarm(
            operator=_OPERATOR,
            request_id=request_id,
        )
    print(json.dumps(
        {
            "action": action,
            "release_commit": release,
            "request_id": request_id,
            "result": result,
        },
        default=str,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
