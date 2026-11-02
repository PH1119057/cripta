SCRIPT_PATH = "operations/infrastructure/repair_dashboard_ui_identity.py"
INSTALLER_PATH = "operations/infrastructure/install_verified_release.sh"


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_repair_utility_is_atomic_git_and_filesystem_identity_only() -> None:
    source = read_text(SCRIPT_PATH)
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
    source = read_text(SCRIPT_PATH)
    assert 'parser.add_argument("--mode", choices=("check", "repair"), required=True)' in source
    assert 'parser.add_argument("--expected-current", required=True)' in source
    assert 'parser.add_argument("--expected-state", required=True)' in source
    assert 'parser.add_argument("--expected-runtime", required=True)' in source
    assert 'state_root / "DASHBOARD_UI_COMMIT"' in source
    assert 'ui_root / "releases" / expected_current' in source


def test_full_release_persists_dashboard_identity_at_physical_cutover() -> None:
    installer = read_text(INSTALLER_PATH)
    cutover = installer.index(
        'mv -Tf "$next_dashboard" "$DASHBOARD_UI_ROOT/current"'
    )
    verified = installer.index(
        'die "Dashboard UI cutover identity mismatch"',
        cutover,
    )
    state = installer.index(
        '> "$STATE_ROOT/DASHBOARD_UI_COMMIT"',
        verified,
    )
    unit_install = installer.index('for spec in "${unit_specs[@]}"; do', state)
    assert cutover < verified < state < unit_install
    assert 'chown root:cripta "$STATE_ROOT/DASHBOARD_UI_COMMIT"' in installer
    assert 'chmod 0640 "$STATE_ROOT/DASHBOARD_UI_COMMIT"' in installer
