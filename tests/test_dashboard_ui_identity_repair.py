import importlib.util
import os
import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "operations/infrastructure/repair_dashboard_ui_identity.py"
INSTALLER = (
    ROOT / "operations/infrastructure/install_verified_release.sh"
).read_text(encoding="utf-8")


def load_module():
    spec = importlib.util.spec_from_file_location(
        "repair_dashboard_ui_identity", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repair_utility_validates_exact_sha_and_atomic_mode(tmp_path: Path) -> None:
    module = load_module()
    sha = "a" * 40
    assert module.require_sha(sha, "TEST") == sha

    target = tmp_path / "DASHBOARD_UI_COMMIT"
    module.atomic_write_identity(
        target,
        sha,
        uid=os.getuid(),
        gid=os.getgid(),
    )
    assert target.read_text(encoding="utf-8") == sha + "\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_repair_utility_is_git_and_filesystem_identity_only() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    for token in (
        "SOURCE_REMOTE_MAIN_DRIFT",
        "SOURCE_DIRTY",
        "merge-base",
        "--is-ancestor",
        "CURRENT_UI_RELEASE_MISMATCH",
        "EMBEDDED_UI_COMMIT_MISMATCH",
        "RUNTIME_COMMIT_MISMATCH",
        "UI_GIT_BYTES_MISMATCH",
        "STATE_UI_COMMIT_UNEXPECTED",
        "TRADING_STATE_MUTATION=NO",
        "SERVICE_RESTART=NO",
    ):
        assert token in source
    for forbidden in (
        "psycopg",
        "execution_gates",
        "execution_permissions",
        "live_arm_sessions",
        "systemctl",
        "requests.",
    ):
        assert forbidden not in source


def test_full_release_persists_dashboard_identity_at_physical_cutover() -> None:
    cutover = INSTALLER.index(
        'mv -Tf "$next_dashboard" "$DASHBOARD_UI_ROOT/current"'
    )
    verified = INSTALLER.index(
        'die "Dashboard UI cutover identity mismatch"',
        cutover,
    )
    state = INSTALLER.index(
        '> "$STATE_ROOT/DASHBOARD_UI_COMMIT"',
        verified,
    )
    unit_install = INSTALLER.index('for spec in "${unit_specs[@]}"; do', state)
    assert cutover < verified < state < unit_install
    assert 'chown root:cripta "$STATE_ROOT/DASHBOARD_UI_COMMIT"' in INSTALLER
    assert 'chmod 0640 "$STATE_ROOT/DASHBOARD_UI_COMMIT"' in INSTALLER
