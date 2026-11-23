"""Pure fail-closed proposed Exit-only pinned unit identity (#849).

This is *not* a systemd installer, source compatibility registration, or
release authorization. The legacy unit is untouched. A separate reviewed
no-argument controller/rollback and exact actor/path contract must be
implemented and tested before any candidate is activated.
"""
from __future__ import annotations

import hashlib
import re

_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_UNIT = "cripta-universal-exit-consumer.service"
_COMPONENT_PARENT = "/srv/cripta/components/exit/releases"
# Do not place Exit under /srv/cripta/runtime: it is owned by the
# unprivileged service actor and can rename/unlink any root-owned child.
# The sibling path is a *proposal*, not an installed/mounted release root.
_PROTECTED_PARENT_CHAIN = (
    "/srv",
    "/srv/cripta",
    "/srv/cripta/components",
    "/srv/cripta/components/exit",
    _COMPONENT_PARENT,
)


def check_protected_exit_parent_plan(
    snapshots: dict[str, dict[str, object]],
) -> dict[str, object]:
    """Check synthetic root-ancestor metadata, NEVER confer deploy rights.

    The actual host collector must later recheck each path and actor ACL
    just before a privileged install. This function neither reads filesystem
    state nor creates or changes any path.
    """
    if not isinstance(snapshots, dict) or set(snapshots) != set(
        _PROTECTED_PARENT_CHAIN
    ):
        raise ValueError("EXIT_PARENT_CHAIN_INCOMPLETE")
    missing = False
    for index, path in enumerate(_PROTECTED_PARENT_CHAIN):
        row = snapshots[path]
        if not isinstance(row, dict):
            raise ValueError("EXIT_PARENT_METADATA_INVALID")
        expected_keys = {
            "exists", "owner_uid", "mode", "is_symlink",
            "runtime_actor_can_write", "runtime_actor_can_traverse",
        }
        if set(row) != expected_keys or type(row["exists"]) is not bool:
            raise ValueError("EXIT_PARENT_METADATA_INVALID")
        if row["is_symlink"] is not False:
            raise ValueError("EXIT_PARENT_SYMLINK_UNTRUSTED")
        if row["exists"] is False:
            if index < 2:
                raise ValueError("EXIT_PARENT_TRUST_ANCHOR_MISSING")
            if (
                row["owner_uid"] is not None or row["mode"] is not None
                or row["runtime_actor_can_write"] is not False
                or row["runtime_actor_can_traverse"] is not False
            ):
                raise ValueError("EXIT_PARENT_ABSENT_METADATA_INVALID")
            missing = True
            continue
        if missing:
            raise ValueError("EXIT_PARENT_UNVERIFIED_GAP")
        mode = row["mode"]
        if (
            type(row["owner_uid"]) is not int or row["owner_uid"] != 0
            or type(mode) is not int or mode < 0 or mode > 0o7777
            or mode & 0o022
            or row["runtime_actor_can_write"] is not False
            or row["runtime_actor_can_traverse"] is not True
        ):
            raise ValueError("EXIT_PARENT_MUTABLE_OR_UNREADABLE")
    return {
        "state": (
            "ROOT_CHAIN_AWAITING_ROOT_OWNER_CREATION" if missing
            else "ROOT_CHAIN_CONSISTENCY_ONLY"
        ),
        "root_parent": _COMPONENT_PARENT,
        "actual_host_preflight_attested": False,
        "install_authorized": False,
        "rollback_attested": False,
    }


# Exact Git-main reviewed template for the legacy Exit unit. If its bytes
# change, the trust/compatibility contract must be independently reviewed.
_REVIEWED_LEGACY_UNIT_SHA256 = (
    "c4a21004e637df794670d7d096c6ffa443c23a881c662b65bebfc11459433248"
)
_OLD_ROOT = "/srv/cripta/runtime/current"
_OLD_ENV = "EnvironmentFile=-/etc/cripta/release.env"
_OLD_WORKDIR = "WorkingDirectory=" + _OLD_ROOT
_OLD_PYTHONPATH = (
    "Environment=PYTHONPATH=" + _OLD_ROOT + "/src:"
    + _OLD_ROOT + "/.venv/lib/python3.12/site-packages"
)
_OLD_EXEC = (
    "ExecStart=" + _OLD_ROOT + "/.venv/bin/python "
    + _OLD_ROOT + "/operations/connectivity/universal_exit_consumer.py"
)
_REQUIRED_SOURCE_LINES = (
    "User=cripta",
    "Group=cripta",
    "NoNewPrivileges=true",
    "PrivateTmp=true",
    "ProtectHome=true",
    "ProtectSystem=strict",
    "StateDirectory=cripta-p90-r06-exit",
    "StateDirectoryMode=0750",
    "EnvironmentFile=-/etc/cripta/universal_exit_consumer.env",
    _OLD_ENV, _OLD_WORKDIR, _OLD_PYTHONPATH, _OLD_EXEC,
)


def build_exit_unit_candidate(
    *, source_unit: str, component_commit: str, component_lock_sha256: str,
    source_compatibility_commit: str,
) -> dict[str, object]:
    """Return a deterministic, unsigned PREVIEW; never touch systemd or disk."""
    if not isinstance(component_commit, str) or not _SHA40.fullmatch(
        component_commit
    ):
        raise ValueError("EXIT_COMPONENT_COMMIT_INVALID")
    if not isinstance(component_lock_sha256, str) or not _DIGEST.fullmatch(
        component_lock_sha256
    ):
        raise ValueError("EXIT_COMPONENT_LOCK_DIGEST_INVALID")
    if (
        not isinstance(source_compatibility_commit, str)
        or not _SHA40.fullmatch(source_compatibility_commit)
    ):
        raise ValueError("EXIT_SOURCE_COMPATIBILITY_COMMIT_INVALID")
    if not isinstance(source_unit, str) or hashlib.sha256(
        source_unit.encode("utf-8")
    ).hexdigest() != _REVIEWED_LEGACY_UNIT_SHA256:
        raise ValueError("EXIT_UNIT_TEMPLATE_UNAPPROVED")

    lines = source_unit.splitlines()
    if any(lines.count(required) != 1 for required in _REQUIRED_SOURCE_LINES):
        raise ValueError("EXIT_UNIT_TEMPLATE_REQUIRED_LINE_UNVERIFIED")

    root = _COMPONENT_PARENT + "/" + component_commit
    replacement = {
        _OLD_ENV: (
            # P90 source epoch is governed by the previously registered
            # runtime consumer identity, NOT by the new Exit binary SHA.
            # Independent process identity is separately protected below.
            "Environment=CRIPTA_RELEASE_COMMIT=" + source_compatibility_commit
            + "\nEnvironment=CRIPTA_EXIT_COMPONENT_COMMIT=" + component_commit
            + "\nEnvironment=CRIPTA_RUNTIME_ROOT=" + root
        ),
        _OLD_WORKDIR: "WorkingDirectory=" + root,
        _OLD_PYTHONPATH: (
            "Environment=PYTHONPATH=" + root + "/src:"
            + root + "/operations/connectivity"
        ),
        _OLD_EXEC: (
            "ExecStart=" + root + "/.venv/bin/python "
            + root + "/operations/connectivity/universal_exit_consumer.py"
        ),
    }
    prepared = "\n".join(replacement.get(line, line) for line in lines) + "\n"
    if _OLD_ROOT in prepared or _OLD_ENV in prepared:
        raise ValueError("EXIT_UNIT_LEGACY_PATH_REMAINING")
    return {
        "component_id": "EXIT",
        "unit_instance": _UNIT,
        "source_commit": component_commit,
        "source_compatibility_commit": source_compatibility_commit,
        "component_release_root": root,
        "component_lock_sha256": component_lock_sha256,
        "unit_text": prepared,
        "unit_sha256": hashlib.sha256(prepared.encode("utf-8")).hexdigest(),
        "status": "STAGED_ONLY_NOT_DEPLOYABLE",
        "install_authorized": False,
        "unit_installed": False,
        "compatibility_attested": False,
        "source_compatibility_registered": False,
        "external_environment_attested": False,
        "runtime_identity_attested": False,
        "rollback_attested": False,
        "shared_abi_attested": False,
        "source_unit_sha256": _REVIEWED_LEGACY_UNIT_SHA256,
    }
