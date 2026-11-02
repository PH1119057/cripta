from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "operations/infrastructure/repair_dashboard_ui_identity.py"
INSTALLER = (
    ROOT / "operations/infrastructure/install_verified_release.sh"
).read_text(encoding="utf-8")


def test_repair_utility_is_atomic_git_and_filesystem_identity_only() -> None:
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
        "os.fsync",
        "os.replace",
        "os.chmod(tmp, 0o640)",
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


def test_repair_utility_requires_explicit_expected_identities() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert 'parser.add_argument("--mode", choices=("check", "repair"), required=True)' in source
    assert 'parser.add_argument("--expected-current", required=True)' in source
    assert 'parser.add_argument("--expected-state", required=True)' in source
    assert 'parser.add_argument("--expected-runtime", required=True)' in source
    assert 'state_root / "DASHBOARD_UI_COMMIT"' in source
    assert 'ui_root / "releases" / expected_current' in source


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
