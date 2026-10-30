#!/usr/bin/env python3
"""Exact post-migration/pre-cutover recovery for failed release 5be6a7eb.

This is a one-shot recovery rail for the 2026-10-30 bootstrap incident.
It performs no SQL migration and no Exchange mutation. It resumes from the
proved checkpoint: target DB schema contract PASS, filesystem cutover not done.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

RELEASE = "5be6a7eb48024a554f4c69ec8f71ea777bbd98a7"
RELEASE_TREE = "8156f34a464966d9acbe06fc9819e2c6c22b3948"
BASELINE = "db50682d372c382ed77da9635a21d5e3a39da549"
FAILED_BACKUP = Path(
    "/data/cripta/script_archive/release_backups/"
    "20261030_200318_db50682d372c382ed77da9635a21d5e3a39da549"
    "_to_5be6a7eb48024a554f4c69ec8f71ea777bbd98a7"
)
FAILED_UNIT = "cripta-release-5be6a7eb.service"
SOURCE = Path("/srv/cripta/source_checkout")
RUNTIME_ROOT = Path("/srv/cripta/runtime")
TOOLING_ROOT = Path("/data/cripta/research/tooling")
DASHBOARD_ROOT = Path("/srv/cripta/dashboard-ui")
STATE_ROOT = Path("/var/lib/cripta/release")
CONTROLLER_STATE_ROOT = Path("/var/lib/cripta/release-controller")
LOCK_PATH = Path("/run/lock/cripta-install-verified-release.lock")
EXPECTED_SCHEMA_VERSION = "runtime-schema-2026-10-17-compact-analytics-r8c"

ALLOWED_SOURCE_DELTA = {
    "docs/DOCUMENTATION_INDEX_RU.md",
    "docs/RESEARCH_COMPUTE_RULES_RU.md",
    "operations/infrastructure/recover_5be6a7eb_post_migration.py",
    "tests/test_recover_5be6a7eb_post_migration.py",
}

RUNTIME_ARCHIVE_PATHS = (
    "src",
    "production/src/bybit_workbench/dispatcher_v2",
    "operations",
    "config",
    "research/server/backup",
    "research/server/connectivity",
    "research/server/monitoring",
    "research/server/control",
    "pyproject.toml",
)
TOOLING_ARCHIVE_PATHS = (
    "research/server/jobs",
    "research/server/dataset",
    "research/server/cripta-download-expansion.service",
)
RUNTIME_COMPARE_EXCLUDES = {
    "operations/dashboard/index.html",
    "operations/dashboard/app.py",
}

UNIT_SPECS = (
    ("research/server/connectivity/cripta-bybit-latency.service", "cripta-bybit-latency.service"),
    ("research/server/backup/cripta-backup.service", "cripta-backup.service"),
    ("research/server/backup/cripta-backup.timer", "cripta-backup.timer"),
    ("operations/systemd/cripta-dashboard.service", "cripta-dashboard.service"),
    ("operations/systemd/cripta-dns-override.service", "cripta-dns-override.service"),
    (
        "operations/dispatcher_v2/cripta-dispatcher-v2-context-correlator.service",
        "cripta-dispatcher-v2-context-correlator.service",
    ),
    ("operations/dispatcher_v2/cripta-dispatcher-v2.service", "cripta-dispatcher-v2.service"),
    ("operations/monitoring/cripta-exit-runtime.service", "cripta-exit-runtime.service"),
    ("research/server/monitoring/cripta-health-monitor.service", "cripta-health-monitor.service"),
    ("research/server/jobs/cripta-job-intake.service", "cripta-job-intake.service"),
    ("research/server/jobs/cripta-job-runner.service", "cripta-job-runner.service"),
    ("research/server/dataset/cripta-dataset-manifest.service", "cripta-dataset-manifest.service"),
    (
        "research/server/dataset/cripta-public-trade-archive.service",
        "cripta-public-trade-archive.service",
    ),
    (
        "research/server/dataset/cripta-public-trade-archive.timer",
        "cripta-public-trade-archive.timer",
    ),
    (
        "research/server/cripta-download-expansion.service",
        "cripta-download-expansion.service",
    ),
    (
        "operations/systemd/cripta-lifecycle-supervisor.service",
        "cripta-lifecycle-supervisor.service",
    ),
    ("operations/systemd/cripta-m3-trade-analyst.service", "cripta-m3-trade-analyst.service"),
    ("operations/systemd/cripta-mayak-v2.service", "cripta-mayak-v2.service"),
    ("operations/systemd/cripta-mayak-v2-report.service", "cripta-mayak-v2-report.service"),
    (
        "operations/systemd/cripta-mayak-v2-weekly-report.service",
        "cripta-mayak-v2-weekly-report.service",
    ),
    (
        "research/server/monitoring/cripta-opportunity-tracker.service",
        "cripta-opportunity-tracker.service",
    ),
    (
        "operations/monitoring/cripta-position-supervisor.service",
        "cripta-position-supervisor.service",
    ),
    (
        "research/server/connectivity/cripta-private-runtime.service",
        "cripta-private-runtime.service",
    ),
    (
        "operations/systemd/cripta-private-runtime@.service",
        "cripta-private-runtime@.service",
    ),
    (
        "operations/systemd/cripta-account-enrollment-probe@.service",
        "cripta-account-enrollment-probe@.service",
    ),
    (
        "operations/systemd/cripta-release-controller.service",
        "cripta-release-controller.service",
    ),
    (
        "operations/systemd/cripta-lifecycle-account@.service",
        "cripta-lifecycle-account@.service",
    ),
    (
        "operations/systemd/cripta-account-position@.service",
        "cripta-account-position@.service",
    ),
    (
        "operations/systemd/cripta-universal-entry-consumer@.service",
        "cripta-universal-entry-consumer@.service",
    ),
    (
        "research/server/connectivity/cripta-safety-observer.service",
        "cripta-safety-observer.service",
    ),
    (
        "research/server/control/cripta-shadow-command-worker.service",
        "cripta-shadow-command-worker.service",
    ),
    (
        "operations/systemd/cripta-u5-oi30s-source-soak.service",
        "cripta-u5-oi30s-source-soak.service",
    ),
    (
        "operations/systemd/cripta-universal-entry-consumer.service",
        "cripta-universal-entry-consumer.service",
    ),
    (
        "operations/systemd/cripta-universal-entry-observer.service",
        "cripta-universal-entry-observer.service",
    ),
    (
        "operations/systemd/cripta-universal-entry-shadow.service",
        "cripta-universal-entry-shadow.service",
    ),
    (
        "operations/systemd/cripta-universal-exit-shadow.service",
        "cripta-universal-exit-shadow.service",
    ),
    (
        "operations/systemd/cripta-universal-exit-consumer.service",
        "cripta-universal-exit-consumer.service",
    ),
    ("operations/systemd/cripta-r1-reverse-worker.service", "cripta-r1-reverse-worker.service"),
    (
        "operations/monitoring/cripta-causal-context-correlator.service",
        "cripta-causal-context-correlator.service",
    ),
    (
        "operations/strategy_dispatcher/cripta-strategy-dispatcher.service",
        "cripta-strategy-dispatcher.service",
    ),
)

MANAGED_SERVICES = (
    "cripta-universal-entry-shadow.service",
    "cripta-strategy-dispatcher.service",
    "cripta-causal-context-correlator.service",
    "cripta-u5-oi30s-source-soak.service",
    "cripta-mayak-v2-weekly-report.service",
    "cripta-mayak-v2-report.service",
    "cripta-bybit-latency.service",
    "cripta-public-trade-archive.timer",
    "cripta-dashboard.service",
    "cripta-download-expansion.service",
    "cripta-dispatcher-v2-context-correlator.service",
    "cripta-dispatcher-v2.service",
    "cripta-exit-runtime.service",
    "cripta-health-monitor.service",
    "cripta-job-intake.service",
    "cripta-job-runner.service",
    "cripta-lifecycle-supervisor.service",
    "cripta-m3-trade-analyst.service",
    "cripta-mayak-v2.service",
    "cripta-opportunity-tracker.service",
    "cripta-position-supervisor.service",
    "cripta-private-runtime.service",
    "cripta-safety-observer.service",
    "cripta-shadow-command-worker.service",
    "cripta-universal-entry-consumer.service",
    "cripta-universal-entry-observer.service",
    "cripta-universal-exit-shadow.service",
    "cripta-universal-exit-consumer.service",
)

ACTIVE_SERVICES = {
    "cripta-bybit-latency.service",
    "cripta-dashboard.service",
    "cripta-dispatcher-v2-context-correlator.service",
    "cripta-dispatcher-v2.service",
    "cripta-health-monitor.service",
    "cripta-job-intake.service",
    "cripta-job-runner.service",
    "cripta-lifecycle-supervisor.service",
    "cripta-m3-trade-analyst.service",
    "cripta-mayak-v2.service",
    "cripta-opportunity-tracker.service",
    "cripta-position-supervisor.service",
    "cripta-private-runtime.service",
    "cripta-safety-observer.service",
    "cripta-shadow-command-worker.service",
    "cripta-universal-entry-consumer.service",
    "cripta-universal-entry-observer.service",
    "cripta-universal-exit-shadow.service",
    "cripta-universal-exit-consumer.service",
}

SUB_UNITS = (
    "cripta-private-runtime@ta-853ac5fcd58185697396d33cd57459fa.service",
    "cripta-universal-entry-consumer@ta-853ac5fcd58185697396d33cd57459fa.service",
    "cripta-lifecycle-account@ta-853ac5fcd58185697396d33cd57459fa.service",
    "cripta-account-position@ta-853ac5fcd58185697396d33cd57459fa.service",
)

STOP_WINDOW_START = datetime(2026, 10, 30, 20, 3, 0, tzinfo=UTC)
STOP_WINDOW_END = datetime(2026, 10, 30, 20, 4, 0, tzinfo=UTC)
SCHEMA_APPLIED_NOT_BEFORE = datetime(2026, 10, 30, 20, 11, 52, tzinfo=UTC)
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


class RecoveryError(RuntimeError):
    pass


def run(args: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        args,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env or SAFE_ENV,
    )
    if result.returncode != 0:
        tail = result.stdout[-3000:].replace("\n", " | ")
        raise RecoveryError(
            f"COMMAND_FAILED:{Path(args[0]).name}:{result.returncode}:{tail}"
        )
    return result.stdout.strip()


def git(*args: str) -> str:
    return run(
        [
            "/usr/sbin/runuser",
            "-u",
            "cripta",
            "--",
            "/usr/bin/env",
            "GIT_OPTIONAL_LOCKS=0",
            "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
            "/usr/bin/git",
            "-C",
            str(SOURCE),
            *args,
        ]
    )


def git_rc(*args: str) -> int:
    result = subprocess.run(
        [
            "/usr/sbin/runuser",
            "-u",
            "cripta",
            "--",
            "/usr/bin/env",
            "GIT_OPTIONAL_LOCKS=0",
            "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
            "/usr/bin/git",
            "-C",
            str(SOURCE),
            *args,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=SAFE_ENV,
    )
    return result.returncode


def git_bytes(relative: str) -> bytes:
    result = subprocess.run(
        [
            "/usr/sbin/runuser",
            "-u",
            "cripta",
            "--",
            "/usr/bin/env",
            "GIT_OPTIONAL_LOCKS=0",
            "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
            "/usr/bin/git",
            "-C",
            str(SOURCE),
            "show",
            f"{RELEASE}:{relative}",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=SAFE_ENV,
    )
    if result.returncode != 0:
        raise RecoveryError(f"GIT_OBJECT_READ_FAILED:{relative}")
    return result.stdout


def sql_scalar(query: str) -> str:
    return run(
        [
            "/usr/sbin/runuser",
            "-u",
            "postgres",
            "--",
            "/usr/bin/psql",
            "-X",
            "-Atqc",
            query,
            "cripta",
        ]
    )


def installed_commit(root: Path) -> str:
    current = root / "current"
    target = current.resolve(strict=True)
    commit = (target / "INSTALLED_COMMIT").read_text(encoding="utf-8").strip()
    if target.parent != (root / "releases").resolve(strict=True):
        raise RecoveryError(f"CURRENT_PATH_INVALID:{root}")
    if target.name != commit:
        raise RecoveryError(f"CURRENT_IDENTITY_INVALID:{root}")
    return commit


def service_active(unit: str) -> bool:
    result = subprocess.run(
        ["/usr/bin/systemctl", "is-active", "--quiet", unit],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=SAFE_ENV,
    )
    return result.returncode == 0


def service_property(unit: str, prop: str) -> str:
    output = run(["/usr/bin/systemctl", "show", unit, f"--property={prop}"])
    key, sep, value = output.partition("=")
    if sep != "=" or key != prop:
        raise RecoveryError(f"SYSTEMD_PROPERTY_INVALID:{unit}:{prop}")
    return value


def parse_systemd_time(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)


def require_recovered_active_set() -> None:
    if ACTIVE_SERVICES - set(MANAGED_SERVICES):
        raise RecoveryError("ACTIVE_SET_NOT_MANAGED")
    for unit in MANAGED_SERVICES:
        if service_active(unit):
            raise RecoveryError(f"MANAGED_SERVICE_ALREADY_ACTIVE:{unit}")
        stopped = parse_systemd_time(service_property(unit, "InactiveEnterTimestamp"))
        if unit in ACTIVE_SERVICES:
            if stopped is None or not (STOP_WINDOW_START <= stopped <= STOP_WINDOW_END):
                raise RecoveryError(f"ACTIVE_SET_EVIDENCE_MISMATCH:{unit}")
        elif stopped is not None and STOP_WINDOW_START <= stopped <= STOP_WINDOW_END:
            raise RecoveryError(f"UNEXPECTED_STOP_WINDOW_UNIT:{unit}")
    for unit in SUB_UNITS:
        if service_active(unit):
            raise RecoveryError(f"SUB_UNIT_UNEXPECTEDLY_ACTIVE:{unit}")


def require_failed_checkpoint() -> None:
    if not FAILED_BACKUP.is_dir():
        raise RecoveryError("FAILED_BACKUP_MISSING")
    identity = {}
    for line in (FAILED_BACKUP / "release_identity.txt").read_text(
        encoding="utf-8"
    ).splitlines():
        key, sep, value = line.partition("=")
        if sep:
            identity[key] = value
    expected = {
        "release_commit": RELEASE,
        "expected_baseline_commit": BASELINE,
        "release_tree_sha": RELEASE_TREE,
        "runtime_release": str(RUNTIME_ROOT / "releases" / RELEASE),
        "research_tooling_release": str(TOOLING_ROOT / "releases" / RELEASE),
    }
    for key, value in expected.items():
        if identity.get(key) != value:
            raise RecoveryError(f"FAILED_BACKUP_IDENTITY_MISMATCH:{key}")
    dump = FAILED_BACKUP / "cripta_before.dump"
    if not dump.is_file() or dump.stat().st_size <= 0:
        raise RecoveryError("FAILED_BACKUP_DUMP_INVALID")
    if service_property(FAILED_UNIT, "Result") != "exit-code":
        raise RecoveryError("FAILED_UNIT_RESULT_MISMATCH")
    if service_property(FAILED_UNIT, "ExecMainStatus") != "1":
        raise RecoveryError("FAILED_UNIT_EXIT_MISMATCH")


def require_source_identity() -> None:
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        raise RecoveryError("SOURCE_DIRTY")
    head = git("rev-parse", "HEAD")
    remote_line = git("ls-remote", "origin", "refs/heads/main")
    remote = remote_line.split()[0] if remote_line else ""
    if head != remote:
        raise RecoveryError("SOURCE_NOT_REMOTE_MAIN")
    if git_rc("merge-base", "--is-ancestor", RELEASE, head) != 0:
        raise RecoveryError("FAILED_RELEASE_NOT_ANCESTOR_OF_MAIN")
    if git("rev-parse", f"{RELEASE}^{{tree}}") != RELEASE_TREE:
        raise RecoveryError("FAILED_RELEASE_TREE_MISMATCH")
    changed = {
        line.strip()
        for line in git("diff", "--name-only", f"{RELEASE}..{head}").splitlines()
        if line.strip()
    }
    unexpected = changed - ALLOWED_SOURCE_DELTA
    if unexpected:
        raise RecoveryError(
            "SOURCE_DELTA_OUTSIDE_RECOVERY_SCOPE:" + ",".join(sorted(unexpected))
        )


def require_candidate_identity() -> None:
    runtime = RUNTIME_ROOT / "releases" / RELEASE
    tooling = TOOLING_ROOT / "releases" / RELEASE
    for root in (runtime, tooling):
        commit_file = root / "INSTALLED_COMMIT"
        if not commit_file.is_file():
            raise RecoveryError(f"CANDIDATE_IDENTITY_MISSING:{root}")
        if commit_file.read_text(encoding="utf-8").strip() != RELEASE:
            raise RecoveryError(f"CANDIDATE_IDENTITY_MISMATCH:{root}")
    ui = DASHBOARD_ROOT / "releases" / RELEASE
    if (ui / "DASHBOARD_UI_COMMIT").read_text(encoding="utf-8").strip() != RELEASE:
        raise RecoveryError("DASHBOARD_CANDIDATE_IDENTITY_MISMATCH")
    for relative in ("operations/dashboard/index.html", "operations/dashboard/app.py"):
        if (ui / Path(relative).name).read_bytes() != git_bytes(relative):
            raise RecoveryError(f"DASHBOARD_CANDIDATE_BYTES_MISMATCH:{relative}")


def compare_archive(
    *,
    archive_paths: tuple[str, ...],
    candidate: Path,
    excludes: set[str],
) -> None:
    fd, tar_name = tempfile.mkstemp(prefix="cripta-recovery-archive.", dir="/run/lock")
    os.close(fd)
    tar_path = Path(tar_name)
    try:
        with tar_path.open("wb") as stream:
            result = subprocess.run(
                [
                    "/usr/sbin/runuser",
                    "-u",
                    "cripta",
                    "--",
                    "/usr/bin/env",
                    "GIT_OPTIONAL_LOCKS=0",
                    "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                    "/usr/bin/git",
                    "-C",
                    str(SOURCE),
                    "archive",
                    "--format=tar",
                    RELEASE,
                    "--",
                    *archive_paths,
                ],
                check=False,
                stdout=stream,
                stderr=subprocess.PIPE,
                env=SAFE_ENV,
            )
        if result.returncode != 0:
            raise RecoveryError("CANDIDATE_ARCHIVE_READ_FAILED")
        expected: set[str] = set()
        with tarfile.open(tar_path, "r") as archive:
            for member in archive.getmembers():
                relative = member.name
                if member.isdir():
                    continue
                expected.add(relative)
                if relative in excludes:
                    continue
                actual = candidate / relative
                if member.isfile():
                    source = archive.extractfile(member)
                    if source is None or not actual.is_file():
                        raise RecoveryError(f"CANDIDATE_FILE_MISSING:{relative}")
                    if actual.read_bytes() != source.read():
                        raise RecoveryError(f"CANDIDATE_BYTES_MISMATCH:{relative}")
                elif member.issym():
                    if not actual.is_symlink() or os.readlink(actual) != member.linkname:
                        raise RecoveryError(f"CANDIDATE_SYMLINK_MISMATCH:{relative}")
                else:
                    raise RecoveryError(f"CANDIDATE_MEMBER_UNSUPPORTED:{relative}")

        actual_paths: set[str] = set()
        for path in candidate.rglob("*"):
            relative = path.relative_to(candidate).as_posix()
            if relative == "INSTALLED_COMMIT" or relative.startswith(".venv/"):
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.is_dir():
                continue
            actual_paths.add(relative)
        extras = actual_paths - expected - excludes
        if extras:
            raise RecoveryError(
                "CANDIDATE_UNEXPECTED_FILES:" + ",".join(sorted(extras)[:20])
            )
    finally:
        tar_path.unlink(missing_ok=True)


def require_db_checkpoint() -> None:
    version = sql_scalar(
        "SELECT coalesce((SELECT version FROM runtime.schema_contract "
        "WHERE component='private_runtime'),'MISSING')"
    )
    if version != EXPECTED_SCHEMA_VERSION:
        raise RecoveryError("SCHEMA_VERSION_MISMATCH")
    applied = sql_scalar(
        "SELECT extract(epoch FROM applied_at)::bigint "
        "FROM runtime.schema_contract WHERE component='private_runtime'"
    )
    if not applied.isdigit() or int(applied) < int(SCHEMA_APPLIED_NOT_BEFORE.timestamp()):
        raise RecoveryError("SCHEMA_APPLIED_AT_MISMATCH")
    runtime = RUNTIME_ROOT / "releases" / RELEASE
    env = {
        **SAFE_ENV,
        "PYTHONPATH": (
            f"{runtime}/src:{runtime}/operations/connectivity:"
            f"{runtime}/research/server/connectivity:"
            f"{runtime}/.venv/lib/python3.12/site-packages"
        ),
    }
    result = subprocess.run(
        [
            "/usr/sbin/runuser",
            "-u",
            "cripta",
            "--",
            "/usr/bin/env",
            *(f"{key}={value}" for key, value in env.items()),
            str(runtime / ".venv/bin/python"),
            str(runtime / "operations/connectivity/runtime_schema.py"),
            "validate",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0 or "RUNTIME_SCHEMA_CONTRACT=PASS" not in result.stdout:
        raise RecoveryError("RUNTIME_SCHEMA_VALIDATE_FAILED")


def trading_state() -> tuple[str, str, str]:
    gate = sql_scalar(
        "SELECT enabled::int FROM control.execution_gates WHERE mode='mainnet'"
    )
    permissions = sql_scalar(
        "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true"
    )
    sessions = sql_scalar(
        "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
    )
    return gate, permissions, sessions


def atomic_symlink(target: Path, link: Path) -> None:
    temporary = link.parent / f".recovery-{link.name}-{os.getpid()}"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, link)


def install_bytes(relative: str, destination: Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(git_bytes(relative))
            stream.flush()
            os.fsync(stream.fileno())
        os.close(fd)
        fd = -1
        os.chown(temp_name, 0, 0)
        os.replace(temp_name, destination)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def write_root_file(path: Path, text: str, mode: int, group: str = "root") -> None:
    import grp

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.close(fd)
        fd = -1
        os.chown(temp_name, 0, grp.getgrnam(group).gr_gid)
        os.replace(temp_name, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def write_recovery_status(status: str, stage: str, detail: str = "") -> None:
    payload = {
        "status": status,
        "stage": stage,
        "release": RELEASE,
        "baseline": BASELINE,
        "time": datetime.now(UTC).isoformat(),
        "detail": detail[:1000],
    }
    write_root_file(
        FAILED_BACKUP / "post_migration_recovery.json",
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        0o600,
    )


def preflight() -> tuple[str, str, str]:
    if len(sys.argv) != 1:
        raise RecoveryError("NO_ARGUMENTS_ALLOWED")
    if os.geteuid() != 0:
        raise RecoveryError("ROOT_REQUIRED")

    require_source_identity()
    if installed_commit(RUNTIME_ROOT) != BASELINE:
        raise RecoveryError("RUNTIME_BASELINE_CHANGED")
    if installed_commit(TOOLING_ROOT) != BASELINE:
        raise RecoveryError("TOOLING_BASELINE_CHANGED")
    state_commit = (STATE_ROOT / "INSTALLED_COMMIT").read_text(encoding="utf-8").strip()
    if state_commit != BASELINE:
        raise RecoveryError("RELEASE_STATE_BASELINE_CHANGED")

    require_failed_checkpoint()
    require_recovered_active_set()
    require_candidate_identity()
    compare_archive(
        archive_paths=RUNTIME_ARCHIVE_PATHS,
        candidate=RUNTIME_ROOT / "releases" / RELEASE,
        excludes=RUNTIME_COMPARE_EXCLUDES,
    )
    compare_archive(
        archive_paths=TOOLING_ARCHIVE_PATHS,
        candidate=TOOLING_ROOT / "releases" / RELEASE,
        excludes=set(),
    )
    require_db_checkpoint()

    gate, permissions, sessions = trading_state()
    if (gate, permissions, sessions) != ("0", "0", "0"):
        raise RecoveryError(
            f"TRADING_NOT_DISARMED:{gate}/{permissions}/{sessions}"
        )
    return gate, permissions, sessions


def cutover() -> None:
    runtime = RUNTIME_ROOT / "releases" / RELEASE
    tooling = TOOLING_ROOT / "releases" / RELEASE
    ui = DASHBOARD_ROOT / "releases" / RELEASE

    atomic_symlink(runtime, RUNTIME_ROOT / "current")
    atomic_symlink(tooling, TOOLING_ROOT / "current")
    atomic_symlink(ui, DASHBOARD_ROOT / "current")

    dashboard_app = runtime / "operations/dashboard/app.py"
    shutil.copyfile(ui / "app.py", dashboard_app)
    shutil.chown(dashboard_app, user="cripta", group="cripta")
    dashboard_app.chmod(0o644)

    for relative, unit in UNIT_SPECS:
        install_bytes(relative, Path("/etc/systemd/system") / unit, 0o644)

    install_bytes(
        "operations/systemd/cripta-private-runtime.service.d/10-pythonpath.conf",
        Path("/etc/systemd/system/cripta-private-runtime.service.d/10-pythonpath.conf"),
        0o644,
    )
    install_bytes(
        "operations/infrastructure/cripta-apply-incoming",
        Path("/usr/local/sbin/cripta-apply-incoming"),
        0o755,
    )
    install_bytes(
        "operations/infrastructure/deploy_dashboard_ui.sh",
        Path("/usr/local/sbin/cripta-deploy-dashboard-ui"),
        0o755,
    )

    controller_root = Path("/usr/local/libexec/cripta-release-controller")
    controller_root.mkdir(parents=True, exist_ok=True)
    os.chown(controller_root, 0, 0)
    controller_root.chmod(0o755)
    install_bytes(
        "operations/infrastructure/cripta-release-controller",
        controller_root / "cripta-release-controller",
        0o755,
    )
    install_bytes(
        "operations/infrastructure/release_controller_gate.py",
        controller_root / "release_controller_gate.py",
        0o644,
    )
    install_bytes(
        "operations/infrastructure/release_controller_github.py",
        controller_root / "release_controller_github.py",
        0o644,
    )

    CONTROLLER_STATE_ROOT.mkdir(parents=True, exist_ok=True)
    os.chown(CONTROLLER_STATE_ROOT, 0, 0)
    CONTROLLER_STATE_ROOT.chmod(0o700)
    used = CONTROLLER_STATE_ROOT / "used"
    used.mkdir(parents=True, exist_ok=True)
    os.chown(used, 0, 0)
    used.chmod(0o700)

    write_root_file(
        Path("/etc/cripta/release.env"),
        (
            f"CRIPTA_RELEASE_COMMIT={RELEASE}\n"
            f"CRIPTA_RUNTIME_ROOT={RUNTIME_ROOT}/current\n"
            f"CRIPTA_DASHBOARD_UI_ROOT={DASHBOARD_ROOT}\n"
            f"CRIPTA_RESEARCH_TOOLING_ROOT={TOOLING_ROOT}/current\n"
        ),
        0o640,
        group="cripta",
    )

    subprocess.run(
        ["/usr/bin/systemctl", "disable", "--now", "cripta-entry-shadow-scanner.service"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=SAFE_ENV,
    )
    Path("/etc/systemd/system/cripta-entry-shadow-scanner.service").unlink(
        missing_ok=True
    )

    run(["/usr/bin/systemctl", "daemon-reload"])
    subprocess.run(
        ["/usr/bin/systemctl", "disable", "cripta-release-controller.service"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=SAFE_ENV,
    )
    if service_property("cripta-release-controller.service", "UnitFileState") != "disabled":
        raise RecoveryError("CONTROLLER_NOT_DISABLED")

    for service in MANAGED_SERVICES:
        if service in ACTIVE_SERVICES:
            run(["/usr/bin/systemctl", "start", service])

    for service in MANAGED_SERVICES:
        active = service_active(service)
        if service in ACTIVE_SERVICES and not active:
            raise RecoveryError(f"SERVICE_FAILED_AFTER_RECOVERY:{service}")
        if service not in ACTIVE_SERVICES and active:
            raise RecoveryError(f"INACTIVE_SERVICE_BECAME_ACTIVE:{service}")
    for unit in SUB_UNITS:
        if service_active(unit):
            raise RecoveryError(f"SUB_UNIT_BECAME_ACTIVE:{unit}")

    write_root_file(
        STATE_ROOT / "INSTALLED_COMMIT",
        RELEASE + "\n",
        0o640,
        group="cripta",
    )
    write_root_file(
        STATE_ROOT / "LAST_BACKUP",
        str(FAILED_BACKUP) + "\n",
        0o640,
        group="cripta",
    )


def postcheck(pre_state: tuple[str, str, str]) -> bool:
    if installed_commit(RUNTIME_ROOT) != RELEASE:
        raise RecoveryError("RUNTIME_POST_IDENTITY_MISMATCH")
    if installed_commit(TOOLING_ROOT) != RELEASE:
        raise RecoveryError("TOOLING_POST_IDENTITY_MISMATCH")
    if (DASHBOARD_ROOT / "current").resolve(strict=True) != (
        DASHBOARD_ROOT / "releases" / RELEASE
    ).resolve(strict=True):
        raise RecoveryError("DASHBOARD_POST_IDENTITY_MISMATCH")
    if (STATE_ROOT / "INSTALLED_COMMIT").read_text(encoding="utf-8").strip() != RELEASE:
        raise RecoveryError("RELEASE_STATE_POST_IDENTITY_MISMATCH")
    if trading_state() != pre_state:
        raise RecoveryError("TRADING_CONTROL_CHANGED_DURING_RECOVERY")

    sync_started_ms = int(time.time() * 1000) - 120_000
    reconciliation_ok = False
    for _ in range(30):
        value = sql_scalar(
            "SELECT coalesce((SELECT finished_at_epoch_ms FROM "
            "runtime.reconciliation_runs WHERE ok=1 ORDER BY id DESC LIMIT 1),0)"
        )
        if value.isdigit() and int(value) >= sync_started_ms:
            reconciliation_ok = True
            break
        time.sleep(2)
    return reconciliation_ok


def main() -> int:
    stage = "PRECHECK"
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryError("RELEASE_LOCKED") from exc

        pre_state = preflight()
        write_recovery_status("RUNNING", stage)

        stage = "CUTOVER"
        write_recovery_status("RUNNING", stage)
        cutover()

        stage = "POSTCHECK"
        write_recovery_status("RUNNING", stage)
        reconciliation_ok = postcheck(pre_state)

        write_recovery_status("PASS", "COMPLETE")
        print("POST_MIGRATION_RECOVERY=PASS")
        print(f"INSTALLED_COMMIT={RELEASE}")
        print("RUNTIME_LIVENESS_VERIFIED=YES")
        print(
            "RUNTIME_BEHAVIOR_VERIFIED="
            + ("YES" if reconciliation_ok else "NO")
        )
        print("OWNER_ARM=UNCHANGED")
        return 0
    except Exception as exc:
        try:
            write_recovery_status("FAILED", stage, str(exc))
        except Exception:
            pass
        print("HARD_STOP=YES", file=sys.stderr)
        print(f"POST_MIGRATION_RECOVERY=FAILED stage={stage}", file=sys.stderr)
        print(f"reason={str(exc)[:1000]}", file=sys.stderr)
        return 20
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
