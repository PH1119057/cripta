"""Pure fail-closed Exit-only release/rollback PREVIEW (#849).

ARCH §50.1 / DEV §50.6. Caller-provided snapshots are *untrusted* and
MUST NOT authorize an installation even when internally consistent.
Only the future reviewed no-argument root management controller may
collect independent GitHub/OS/DB/signed-Bybit evidence, secure one lock,
mutate one Exit unit, reconcile the real outcome, and rollback.
There are NO filesystem/systemd/network/DB/Exchange operations here.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_UNIT = "cripta-universal-exit-consumer.service"
_ROOT = "/srv/cripta/components/exit/releases/"
_ROLLBACK = "/srv/cripta/components/exit/rollback/"
_NEIGHBORS = frozenset({"ENTRY", "PRIVATE_RUNTIME", "MARKET_SOURCE", "DASHBOARD"})
_EXCHANGE_ACCOUNTS = frozenset({"MAIN", "SUB"})
_SHARED_RELEASE_LOCK = "/run/lock/cripta-install-verified-release.lock"

# Tokens describe an ordered review plan, NOT shell commands.
_REVIEW_STEPS = (
    "RECHECK_GITHUB_MAIN_AND_SCOPE_QUALITY",
    "ACQUIRE_SHARED_RELEASE_LOCK",
    "READBACK_ROOT_OWNED_CANDIDATE_AND_ABI",
    "READBACK_PROTECTED_PREVIOUS_EXIT_AND_UNIT",
    "RECHECK_SOURCE_COMPATIBILITY_AND_SIGNED_PROTECTION",
    "RECHECK_UNTOUCHED_NEIGHBOR_SNAPSHOTS",
    "STOP_EXIT_ONLY",
    "PIN_EXIT_ONLY_UNIT_AND_DAEMON_RELOAD",
    "START_EXIT_ONLY",
    "VERIFY_COMPONENT_INSTALLED_AND_LOADED_IDENTITY",
    "VERIFY_EXIT_CONSUMER_RECOVERY_AND_EXCHANGE_PROTECTION",
    "VERIFY_UNTOUCHED_NEIGHBORS_AND_STABILITY",
    "FINALIZE_IF_EVERY_POSTCHECK_PASS",
    "ROLLBACK_EXIT_ONLY_ON_FAILED_POSTCHECK",
)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("EXIT_CUTOVER_" + code)


def _sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _neighbors(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != _NEIGHBORS:
        return False
    return all(
        isinstance(row, (tuple, list))
        and len(row) == 2
        and type(row[0]) is int
        and row[0] > 0
        and type(row[1]) is int
        and row[1] >= 0
        for row in value.values()
    )


def build_exit_cutover_preview(
    *,
    candidate: Mapping[str, Any],
    rollback: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> dict[str, object]:
    """Validate a synthetic source/rollback/operation chain, not reality.

    This intentionally does NOT reject OPEN positions. It requires the
    future owner to prove signed MAIN/SUB protection and existing-position
    continuity independently before *actual* Exit-unit mutation.
    """
    _require(
        isinstance(candidate, Mapping)
        and isinstance(rollback, Mapping)
        and isinstance(observed, Mapping),
        "REQUIRED_SNAPSHOTS_MISSING",
    )
    sha = candidate.get("source_commit")
    _require(
        _sha(sha)
        and candidate.get("component_id") == "EXIT"
        and candidate.get("unit_instance") == _UNIT
        and candidate.get("github_main_commit") == sha
        and candidate.get("merged_scoped_pr") is True
        and candidate.get("scope_check") == "PASS"
        and candidate.get("quality_check") == "PASS",
        "GITHUB_SCOPE_OR_SOURCE_NOT_VERIFIED",
    )
    assert isinstance(sha, str)
    _require(
        candidate.get("component_root") == _ROOT + sha
        and _digest(candidate.get("component_lock_sha256"))
        and _digest(candidate.get("bundle_sha256"))
        and candidate.get("runtime_dependency_abi_attested") is True
        and candidate.get("protected_parent_host_attested") is True
        and candidate.get("source_commit_registered") is True,
        "COMPONENT_IMMUTABLE_IDENTITY_OR_COMPATIBILITY_UNVERIFIED",
    )
    previous = rollback.get("previous_commit")
    _require(
        _sha(previous)
        and previous != sha
        and rollback.get("backup_root") == _ROLLBACK + str(previous)
        and _digest(rollback.get("backup_bundle_sha256"))
        and _digest(rollback.get("backup_unit_sha256"))
        and rollback.get("backup_root_host_attested") is True
        and rollback.get("previous_unit_bytes_verified") is True
        and rollback.get("previous_runtime_recoverable") is True,
        "PROTECTED_ROLLBACK_NOT_VERIFIED",
    )
    _require(
        type(observed.get("old_exit_pid")) is int
        and observed["old_exit_pid"] > 0
        and type(observed.get("old_exit_start_ticks")) is int
        and observed["old_exit_start_ticks"] > 0
        and observed.get("release_lock_available") is True
        and observed.get("prior_mutation_outcome") == "KNOWN_IDLE"
        and type(observed.get("open_position_count")) is int
        and observed["open_position_count"] >= 0,
        "EXISTING_EXIT_OR_RELEASE_LOCK_UNVERIFIED",
    )
    signed = observed.get("exchange_protection")
    _require(
        isinstance(signed, Mapping)
        and set(signed) == _EXCHANGE_ACCOUNTS
        and all(signed[account] == "PASS" for account in _EXCHANGE_ACCOUNTS)
        and observed.get("source_compatibility") == "PASS",
        "SIGNED_EXCHANGE_PROTECTION_OR_SOURCE_COMPATIBILITY_MISSING",
    )
    _require(
        _neighbors(observed.get("neighbor_before")),
        "UNTOUCHED_NEIGHBOR_BASELINE_UNVERIFIED",
    )
    return {
        "state": "OFFLINE_EXIT_CUTOVER_CONSISTENCY_ONLY",
        "component_id": "EXIT",
        "source_commit": sha,
        "rollback_commit": previous,
        "release_lock": _SHARED_RELEASE_LOCK,
        "affected_units": [_UNIT],
        "unaffected_owners": sorted(_NEIGHBORS),
        "planned_open_positions": observed["open_position_count"],
        "steps": list(_REVIEW_STEPS),
        # Synthetic input never becomes authenticated current proof.
        "install_authorized": False,
        "runtime_behavior_verified": False,
        "exchange_protection_independently_verified": False,
        "actual_actor_authorized": False,
        "rollback_executed": False,
        "financial_mutation_authorized": False,
    }
