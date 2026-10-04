from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "release" / "build_server_release.py"
RUNNER = ROOT / "operations" / "infrastructure" / "cripta-apply-incoming"
INSTALLER = ROOT / "operations" / "infrastructure" / "install_verified_release.sh"
RUNTIME_REQUIREMENTS = ROOT / "operations" / "runtime" / "runtime_requirements.lock"


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def test_server_release_builder_binds_zip_to_exact_remote_commit(tmp_path: Path) -> None:
    remote = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    _run(tmp_path, "git", "init", "--bare", str(remote))
    _run(tmp_path, "git", "init", "-b", "main", str(repo))
    _run(repo, "git", "config", "user.name", "Cripta Test")
    _run(repo, "git", "config", "user.email", "cripta-test@example.invalid")
    _run(repo, "git", "remote", "add", "origin", str(remote))

    installer = repo / "operations" / "infrastructure" / "install_verified_release.sh"
    installer.parent.mkdir(parents=True)
    installer.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho install\n")
    installer.chmod(0o755)
    (repo / "base.txt").write_text("baseline\n")
    _run(
        repo,
        "git",
        "add",
        "--",
        "operations/infrastructure/install_verified_release.sh",
        "base.txt",
    )
    _run(repo, "git", "commit", "-m", "baseline")
    baseline = _run(repo, "git", "rev-parse", "HEAD")
    _run(repo, "git", "push", "-u", "origin", "main")

    source = repo / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n")
    _run(repo, "git", "add", "--", "src/example.py")
    _run(repo, "git", "commit", "-m", "release")
    release = _run(repo, "git", "rev-parse", "HEAD")
    tree = _run(repo, "git", "rev-parse", "HEAD^{tree}")
    _run(repo, "git", "push", "origin", "main")

    output = tmp_path / "release.zip"
    _run(
        ROOT,
        sys.executable,
        str(BUILDER),
        "--repo",
        str(repo),
        "--release-commit",
        release,
        "--expected-baseline-commit",
        baseline,
        "--output",
        str(output),
        "--targeted-test",
        "pytest",
    )

    assert output.is_file()
    companion = Path(str(output) + ".sha256")
    assert companion.is_file()
    expected_zip_sha = companion.read_text(encoding="ascii").split()[0]
    assert hashlib.sha256(output.read_bytes()).hexdigest() == expected_zip_sha

    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert {
            "MANIFEST.json",
            "install.sh",
            "SHA256SUMS.txt",
            "README_RU.md",
            "payload/src/example.py",
        } <= names
        manifest = json.loads(archive.read("MANIFEST.json"))
        assert manifest["source_repository"] == "PH1119057/cripta"
        assert manifest["release_ref"] == "refs/heads/main"
        assert manifest["release_commit"] == release
        assert manifest["release_tree_sha"] == tree
        assert manifest["expected_baseline_commit"] == baseline
        assert manifest["baseline_policy"] == "EXACT_COMMIT"
        assert archive.read("install.sh") == installer.read_bytes()
        mode = archive.getinfo("install.sh").external_attr >> 16
        assert mode & stat.S_IXUSR


def test_persistent_runner_enforces_git_identity_before_installer() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for token in (
        "INSTALLED_COMMIT",
        "expected_baseline_commit",
        "release_commit",
        "release_tree_sha",
        "ls-remote origin",
        "REMOTE_RELEASE_COMMIT_VERIFIED=PASS",
        "PAYLOAD_MATCHES_RELEASE_COMMIT=PASS",
        "INSTALLER_MATCHES_RELEASE_COMMIT=PASS",
        "PERSISTENT_RUNNER_SELF_UPDATE=BLOCKED_BY_CONTRACT",
    ):
        assert token in source
    assert source.index("REMOTE_RELEASE_COMMIT_VERIFIED=PASS") < source.index(
        "=== PACKAGE INSTALLER START ==="
    )
    assert source.index("PAYLOAD_MATCHES_RELEASE_COMMIT=PASS") < source.index(
        "=== PACKAGE INSTALLER START ==="
    )


def test_verified_installer_is_fail_closed_and_preserves_disarmed_state() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    for token in (
        "mainnet gate must be disarmed before deploy",
        "real Strategy execution permissions must be zero before deploy",
        "open/reconciliation StrategyPosition exists",
        "Exchange hot position exists",
        "pending runtime trade command exists",
        "pending Exchange order exists",
        "pg_dump -Fc",
        "flock -n 9",
        "/srv/cripta/dashboard/universal_entry_source",
        "< \"$RUNTIME_ROOT/current/operations/sql/20260920_slot_admission_v1.sql\"",
        "< \"$RUNTIME_ROOT/current/operations/sql/20261003_market_observation_alert_v1.sql\"",
        "< \"$RUNTIME_ROOT/current/operations/sql/20261004_r1_reverse_transitions.sql\"",
        "CRIPTA_RELEASE_COMMIT",
        "INSTALLED_COMMIT",
        "GATE=DISARMED",
    ):
        assert token in source
    assert "production/src/bybit_workbench/dispatcher_v2" in source
    assert 'PYTHONPATH="$runtime_release/src"' in source
    assert 'PYTHONPATH="$runtime_release/production/src"' in source
    assert "import sqlalchemy" in source
    assert "import bybit_workbench.dispatcher_v2" in source
    assert "systemctl start cripta-universal-entry-consumer.service" not in source
    assert '"$RUNTIME_ROOT/current/operations/connectivity/runtime_schema.py" migrate' in source
    assert '"$RUNTIME_ROOT/current/operations/connectivity/runtime_schema.py" validate' in source
    assert source.index('"$RUNTIME_ROOT/current/operations/connectivity/runtime_schema.py" migrate') < source.index("systemctl daemon-reload")
    assert (
        "-f /srv/cripta/runtime/current/operations/sql/"
        "20260920_slot_admission_v1.sql"
        not in source
    )


def test_observer_uses_common_exact_release_identity() -> None:
    source = (
        ROOT / "operations" / "monitoring" / "universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    assert 'os.environ.get("CRIPTA_RELEASE_COMMIT"' in source
    assert "CRIPTA_U5_LOADED_COMMIT" not in source


def test_runtime_units_load_common_exact_release_identity() -> None:
    units = (
        "cripta-universal-entry-observer.service",
        "cripta-universal-entry-consumer.service",
        "cripta-lifecycle-supervisor.service",
        "cripta-universal-exit-shadow.service",
        "cripta-dashboard.service",
    )
    for unit in units:
        source = (ROOT / "operations" / "systemd" / unit).read_text(encoding="utf-8")
        assert "EnvironmentFile=-/etc/cripta/release.env" in source


def test_runtime_dependency_lock_covers_universal_entry_database_imports() -> None:
    lock = RUNTIME_REQUIREMENTS.read_text(encoding="utf-8")
    assert "greenlet==3.5.5" in lock
    assert "sqlalchemy==2.0.52" in lock


def test_dispatcher_unit_uses_packaged_dispatcher_source_root() -> None:
    unit = (
        ROOT / "operations" / "dispatcher_v2" / "cripta-dispatcher-v2.service"
    ).read_text(encoding="utf-8")
    assert "Environment=PYTHONPATH=/srv/cripta/runtime/current/production/src" in unit


def test_verified_installer_prunes_release_backups_to_one_previous_with_short_control_checkpoint() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    for token in (
        'CONTROL_CHECKPOINT="${CRIPTA_RELEASE_CONTROL_CHECKPOINT:-0}"',
        'CONTROL_REASON="${CRIPTA_RELEASE_CONTROL_REASON:-}"',
        "CONTROL_MAX_AGE_SECONDS=604800",
        "prune_release_backups()",
        'reason=latest',
        'reason=control_checkpoint',
        'CONTROL_CHECKPOINT',
        'prune_release_backups',
    ):
        assert token in source

    rules = (ROOT / "docs" / "DEVELOPMENT_RELEASE_RULES_RU.md").read_text(
        encoding="utf-8"
    )
    assert "exactly 1 latest previous deploy rollback backup" in rules
    assert "maximum lifetime = 7 * 24h" in rules
    assert "/data/cripta/backups/system/<timestamp>" in rules

def test_verified_installer_manages_download_expansion_unit() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    assert (
        "research/server/cripta-download-expansion.service"
        "|cripta-download-expansion.service|tooling"
    ) in source
    assert "  cripta-download-expansion.service\n" in source



def test_verified_installer_manages_public_trade_archive_units() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    assert (
        "research/server/dataset/cripta-public-trade-archive.service"
        "|cripta-public-trade-archive.service|tooling"
    ) in source
    assert (
        "research/server/dataset/cripta-public-trade-archive.timer"
        "|cripta-public-trade-archive.timer|tooling"
    ) in source
    assert "  cripta-public-trade-archive.timer\n" in source
    assert "/data/cripta/datasets/raw/bybit_public_trades_daily_v1" in source


def test_verified_installer_validates_release_units_before_mutation() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    assert "research/server/cripta-download-expansion.service" in source
    assert (
        "research/server/jobs research/server/dataset "
        "research/server/cripta-download-expansion.service"
    ) in source
    preflight = source.index("UNIT_SOURCE_PREFLIGHT=PASS")
    backup = source.index("pg_dump -Fc -d cripta")
    stop_loop = source.index('for service in "${managed_services[@]}"; do')
    switch = source.index('mv -Tf "$next_runtime" "$RUNTIME_ROOT/current"')
    assert preflight < backup < stop_loop < switch
    assert "unit source missing before mutation" in source[:backup]


def test_verified_installer_checks_actual_runtime_and_tooling_baseline() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    runtime_guard = source.index("runtime baseline mismatch")
    tooling_guard = source.index("tooling baseline mismatch")
    backup = source.index("pg_dump -Fc -d cripta")
    switch = source.index('mv -Tf "$next_runtime" "$RUNTIME_ROOT/current"')
    assert runtime_guard < backup < switch
    assert tooling_guard < backup < switch
    assert 'current_runtime="$(readlink -f "$RUNTIME_ROOT/current"' in source
    assert 'current_tooling="$(readlink -f "$RESEARCH_TOOLING_ROOT/current"' in source
