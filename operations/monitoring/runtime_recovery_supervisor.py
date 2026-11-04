#!/usr/bin/env python3
"""Inert-until-installed Runtime Recovery Supervisor with durable observations.

Default OBSERVE_ONLY. This phase cannot issue systemd mutations or LIVE rights.
The broker activation/cutover requires a separate verified release.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bybit_workbench.runtime_recovery_policy import (  # noqa: E402
    ServiceRecoveryObservation,
    UnitState,
    p90_real_v3_recovery_policies,
)
from bybit_workbench.runtime_recovery_supervisor import (  # noqa: E402
    OperationStage,
    RecoveryEvidence,
    RecoveryRecord,
    plan_recovery,
)

ACCOUNT = "ta-853ac5fcd58185697396d33cd57459fa"
STATE_ROOT = Path("/var/lib/cripta-runtime-recovery")
INSTALLED = Path("/var/lib/cripta/release/INSTALLED_COMMIT")
CURRENT = Path("/srv/cripta/runtime/current")
SHA = re.compile(r"^[0-9a-f]{40}$")
POLL_SECONDS = 15
RUNNING = True
STATUS_PATHS: dict[str, tuple[Path, str, int]] = {
    "cripta-universal-entry-observer.service": (
        Path("/var/lib/cripta/universal_entry_observer/status.json"), "RUNNING", 15
    ),
    "cripta-lifecycle-supervisor.service": (
        Path("/var/lib/cripta/lifecycle_supervisor/status.json"), "RUNNING", 15
    ),
    "cripta-position-supervisor.service": (
        Path("/run/cripta-position-supervisor/status.json"), "RUNNING", 15
    ),
    f"cripta-lifecycle-account@{ACCOUNT}.service": (
        Path(f"/var/lib/cripta/lifecycle_account/account-{ACCOUNT}/status.json"),
        "VERIFIED_ACCOUNT_SCAN", 45
    ),
    f"cripta-account-position@{ACCOUNT}.service": (
        Path(f"/var/lib/cripta/account_position/account-{ACCOUNT}/status.json"),
        "SAMPLED_PARTIAL", 45
    ),
}


def _stop(_signal: int, _frame: object) -> None:
    global RUNNING
    RUNNING = False


def _release() -> str:
    try:
        installed = INSTALLED.read_text(encoding="ascii").strip().lower()
        loaded = CURRENT.resolve(strict=True).name
    except OSError:
        return ""
    if installed == loaded and SHA.fullmatch(installed):
        return installed
    return ""


def _unit_state(unit: str) -> UnitState:
    """Trusted constant-argv inspection: no shell and no OS mutations."""
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "show", unit, "-p", "Id",
             "-p", "LoadState", "-p", "ActiveState"],
            capture_output=True, text=True, timeout=6, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return UnitState.UNKNOWN
    if result.returncode:
        return UnitState.UNKNOWN
    values = dict(line.split("=", 1) for line in result.stdout.splitlines()
                  if "=" in line)
    if values.get("Id") != unit or values.get("LoadState") != "loaded":
        return UnitState.UNKNOWN
    try:
        return UnitState(values.get("ActiveState", "unknown"))
    except ValueError:
        return UnitState.UNKNOWN


def _healthy_status(
    *,
    path: Path,
    state: str,
    max_age: int,
    now: datetime,
    release_commit: str,
) -> bool | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        raw = data.get("observed_at") or data.get("updated_at")
        observed = datetime.fromisoformat(str(raw))
        if observed.tzinfo is None or observed.utcoffset() is None:
            return None
        age = (now - observed.astimezone(UTC)).total_seconds()
        if not 0 <= age <= max_age:
            return False
        if data.get("state") != state:
            return False
        if "source_commit" in data and data["source_commit"] != release_commit:
            return False
        if state == "RUNNING" and "observer_ready" in data:
            return data["observer_ready"] is True
        if state == "RUNNING" and "active_fault_codes" in data:
            codes = data["active_fault_codes"]
            return isinstance(codes, list) and not codes
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _durable_record(unit: str, release: str) -> RecoveryRecord:
    """Old/ambiguous journal never silently transfers across a release."""
    path = STATE_ROOT / "operations.json"
    if not path.is_file():
        return RecoveryRecord(unit=unit, release_commit=release)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("release") != release:
        raise RuntimeError("RECOVERY_JOURNAL_RELEASE_MISMATCH")
    entry = data.get("units", {}).get(unit)
    if entry is None:
        return RecoveryRecord(unit=unit, release_commit=release)
    if not isinstance(entry, dict):
        raise RuntimeError("RECOVERY_JOURNAL_INVALID")
    record = RecoveryRecord(
        unit=str(entry["unit"]),
        release_commit=str(entry["release_commit"]),
        attempts=int(entry["attempts"]),
        stage=OperationStage(str(entry["stage"])),
        last_attempt_epoch=int(entry["last_attempt_epoch"]),
        request_id=str(entry["request_id"]),
        reason=str(entry["reason"]),
    )
    if record.unit != unit or record.release_commit != release:
        raise RuntimeError("RECOVERY_JOURNAL_IDENTITY_MISMATCH")
    return record


def _store_observation(release: str, evidence: dict[str, object]) -> None:
    """Write only into systemd StateDirectory; atomic create+rename."""
    if not STATE_ROOT.is_dir() or STATE_ROOT.is_symlink():
        raise RuntimeError("RECOVERY_STATE_DIRECTORY_NOT_PREPARED")
    target = STATE_ROOT / "observations.json"
    temporary = STATE_ROOT / "observations.tmp"
    temporary.write_text(
        json.dumps({"release": release, "evidence": evidence}, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(target)


def collect_once(*, now: datetime) -> dict[str, object]:
    release = _release()
    if not release:
        return {"status": "BLOCKED", "reason": "RELEASE_IDENTITY_UNVERIFIED"}
    output: dict[str, object] = {}
    for policy in p90_real_v3_recovery_policies(sub_account_id=ACCOUNT):
        state = _unit_state(policy.unit)
        if policy.unit in STATUS_PATHS:
            path, expected, ttl = STATUS_PATHS[policy.unit]
            behavior = _healthy_status(
                path=path, state=expected, max_age=ttl,
                now=now, release_commit=release,
            )
        else:
            behavior = None
        try:
            prior = _durable_record(policy.unit, release)
        except (OSError, KeyError, ValueError, TypeError) as exc:
            output[policy.unit] = {
                "action": "BLOCKED", "reason": type(exc).__name__,
            }
            continue
        observation = ServiceRecoveryObservation(
            unit=policy.unit,
            unit_state=state,
            behavior_ready=behavior,
            loaded_commit=release,
            expected_commit=release,
            attempts_used=prior.attempts,
        )
        # There is no independent full Exit/protection proof in this adapter.
        # Never invent it from process liveness or a status file.
        evidence = RecoveryEvidence(
            scope_verified=bool(release),
            exit_continuity_verified=False,
            reconciliation_path_verified=False,
        )
        decision = plan_recovery(
            policy=policy, observation=observation, prior=prior,
            evidence=evidence, now_epoch=int(now.timestamp()),
            execution_enabled=False,
        )
        output[policy.unit] = {
            "state": state.value,
            "behavior_ready": behavior,
            "action": decision.action.value,
            "dispatch": decision.dispatch,
            "reason": decision.reason,
        }
    return {"status": "OBSERVE_ONLY", "release": release, "units": output}


def main() -> int:
    if os.environ.get("CRIPTA_RUNTIME_RECOVERY_MODE", "OBSERVE_ONLY") != "OBSERVE_ONLY":
        raise SystemExit("RECOVERY_EXECUTION_NOT_WIRED")
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while RUNNING:
        now = datetime.now(UTC)
        result = collect_once(now=now)
        if result.get("release"):
            _store_observation(str(result["release"]), result)
        time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
