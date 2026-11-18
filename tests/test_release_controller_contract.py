"""Static safety contract for the Release Controller implementation (#288)."""
import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "operations/infrastructure/cripta-release-controller"
UNIT = ROOT / "operations/systemd/cripta-release-controller.service"
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"


def test_controller_source_is_valid_python_and_has_no_cli_parser():
    source = CONTROLLER.read_text(encoding="utf-8")
    ast.parse(source)
    assert "argparse" not in source
    assert "len(sys.argv) != 1" in source
    assert "OWNER_ARM=UNCHANGED" in source
    assert "DASHBOARD_PURPOSE" in source
    assert "/usr/local/sbin/cripta-deploy-dashboard-ui" in source
    assert "CRIPTA_DASHBOARD_UI_VERIFY_ONLY=1" in source
    assert "INSTALLED_COMMIT_UNCHANGED" in source
    assert "TRADING_RUNTIME_CHANGED=NO" in source
    assert '"/usr/bin/systemd-run"' in source
    assert '"--property=User={user}"' in source
    assert '"--property=Group={user}"' in source
    assert '"--property=NoNewPrivileges=yes"' in source
    assert "/usr/bin/setpriv" not in source
    assert "/usr/sbin/runuser" not in source
    assert '"/usr/bin/systemctl", "start", "cripta-source-sync.service"' in source
    assert '"/usr/bin/systemd-run"' in source
    assert '"--wait"' in source
    assert '"--pipe"' in source
    assert '"--collect"' in source
    assert '"--property=TimeoutStartSec=1800"' in source


def test_controller_service_is_inert_and_sandboxed():
    unit = UNIT.read_text(encoding="utf-8")
    assert "Type=oneshot" in unit
    assert "User=root" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/var/lib/cripta/release-controller" in unit
    assert "ExecStart=/usr/bin/python3 /usr/local/libexec/cripta-release-controller/" in unit
    assert "ReadWritePaths=/usr/local/libexec/cripta-release-controller" in unit


def test_installer_installs_controller_but_does_not_manage_or_start_it():
    installer = INSTALLER.read_text(encoding="utf-8")
    spec = (
        '"operations/systemd/cripta-release-controller.service|'
        'cripta-release-controller.service|runtime"'
    )
    assert spec in installer
    managed = installer.split("managed_services=(", 1)[1].split(")", 1)[0]
    assert "cripta-release-controller.service" not in managed
    assert "enable --now cripta-release-controller" not in installer
    assert "systemctl disable cripta-release-controller.service" in installer
    assert "/usr/local/libexec/cripta-release-controller" in installer
    assert "-o root -g root -m 0755" in installer


def test_dashboard_controller_mode_uses_existing_ui_deployer_not_full_installer():
    source = CONTROLLER.read_text(encoding="utf-8")
    dashboard_block = source.split('if purpose == DASHBOARD_PURPOSE:', 1)[1].split(
        '_deploy(target=target', 1
    )[0]
    assert "_deploy_dashboard(target=target, nonce=nonce)" in dashboard_block
    assert "CRIPTA_DASHBOARD_UI_VERIFY_ONLY=1" in source
    assert "DASHBOARD_DEPLOY_CHANGED_RUNTIME_IDENTITY" in source
    assert "OWNER_ARM=UNCHANGED" in source


def test_dashboard_controller_spawns_bounded_transient_units_for_privilege_boundary():
    source = CONTROLLER.read_text(encoding="utf-8")
    start = source.index("def _run_dashboard_unit(")
    end = source.index("\n\ndef _deploy_dashboard(", start)
    block = source[start:end]
    assert '"/usr/bin/systemd-run"' in block
    assert '"--wait"' in block
    assert '"--pipe"' in block
    assert '"--collect"' in block
    assert "cripta-controller-dashboard-{stage}-{unit_suffix}" in block
    assert '"--property=Type=oneshot"' in block
    assert '"--property=TimeoutStartSec=300"' in block
    assert "CRIPTA_DASHBOARD_UI_VERIFY_ONLY=1" in block
    assert "/usr/local/sbin/cripta-deploy-dashboard-ui" in block
    assert "runuser" not in block


def test_native_source_code_cutover_is_separate_authenticated_controller_scope():
    src = CONTROLLER.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "NATIVE_CUTOVER_PURPOSE" in src
    assert "return NATIVE_CUTOVER_PURPOSE, True" in src
    assert "verify_management_release(" in src
    assert "approval_candidates(" not in src
    assert "OWNER_APPROVAL_MISSING" not in src
    assert "native_source_cutover=purpose == NATIVE_CUTOVER_PURPOSE" in src
    assert '"CRIPTA_NATIVE_SOURCE_CUTOVER="' in src
    assert "def _postcheck_native_source(target: str)" in src
    assert "NATIVE_CUTOVER_LOADED_SOURCE_UNVERIFIED" in src
    assert "NATIVE_SOURCE_PRODUCER_CODE_CHANGED_SEPARATE_CUTOVER_REQUIRED" in installer
    assert "NATIVE_SOURCE_OFFLINE_CUTOVER_PREPARED=PASS" in installer
    assert "NATIVE_SOURCE_INSTALLED_BASELINE_ABI_UNVERIFIED" in installer
    assert "NATIVE_SOURCE_OLD_SESSION_NOT_EXPLICIT_GAP" in installer
    assert "NATIVE_SOURCE_NEW_SESSION_IDENTITY_UNVERIFIED" in installer
    assert "NATIVE_SOURCE_OLD_GAP_LOST" in installer
    assert "NATIVE_SOURCE_EXACT_CODE_CUTOVER=PASS" in installer
    assert "NATIVE_SOURCE_CUTOVER_CANNOT_COMBINE_STAGED_OFFLINE" in installer
    assert "OWNER_ARM=UNCHANGED" in src
    assert "SELECT enabled::int FROM control.execution_gates" in installer


def test_old_native_writer_is_stopped_only_after_db_backup_and_migrations():
    installer = INSTALLER.read_text(encoding="utf-8")
    before = installer.index('pg_dump -Fc -d cripta')
    migration = installer.index(
        'for migration in "${migration_files[@]}"', before,
    )
    stop = installer.index(
        "systemctl stop cripta-native-public-trade-source.service",
        installer.index('if [[ "$native_code_changed" == "1" ]]; then', migration),
    )
    switch = installer.index('mv -Tf "$next_runtime" "$RUNTIME_ROOT/current"')
    new = installer.index(
        "systemctl start cripta-native-public-trade-source.service", switch,
    )
    assert before < migration < stop < switch < new
    assert installer.index("native_source_stopped_for_cutover=1") < switch
    assert "NATIVE_SOURCE_PRE_SWITCH_RECOVERY=STARTED_OLD_CODE" in installer

def test_inactive_native_source_only_blocks_real_producer_abi_delta():
    source = CONTROLLER.read_text(encoding="utf-8")
    start = source.index("def _select_scoped_purpose(")
    end = source.index("\ndef main()", start)
    block = source[start:end]
    assert "installed_source_delta = _git(" in block
    assert '"diff", "--name-only", runtime_baseline, target, "--"' in block
    assert "if installed_source_delta:" in block
    assert "NATIVE_SOURCE_BASELINE_NOT_PROVEN" in block
    assert block.index("installed_source_delta = _git(") < block.index(
        'Path("/var/lib/cripta/native_public_trade_source/status.json")'
    )
    assert block.index("if installed_source_delta:") < block.index(
        "NATIVE_SOURCE_BASELINE_NOT_PROVEN"
    )
    assert "return NATIVE_CUTOVER_PURPOSE, True" in block


def test_p90_minimal_release_installs_exact_manifest_without_erasing_r11() -> None:
    """The same reviewed source unit must select the reviewed physical feed file."""
    installer = INSTALLER.read_text(encoding="utf-8")
    unit = (
        ROOT / "operations/systemd/cripta-native-public-trade-source.service"
    ).read_text(encoding="utf-8")
    minimal = json.loads((
        ROOT / "operations/monitoring/p90_minimal_public_feeds.json"
    ).read_text(encoding="utf-8"))
    r11 = json.loads((
        ROOT / "operations/monitoring/r11_seven_public_feeds.json"
    ).read_text(encoding="utf-8"))
    assert len(minimal) == 55
    assert len({
        (x["product"], x["symbol"], x["dataset"], x["timeframe"])
        for x in minimal
    }) == 35
    assert {x["symbol"] for x in minimal} == {
        "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    }
    assert {x["product"] for x in minimal} == {"LINEAR"}
    assert {x["consumer_id"] for x in minimal} == {
        "ENTRY_P90", "EXIT_P90", "EXECUTION_P90", "POSITION",
    }
    # R11 is a preserved alternate mode, not data erasure or silent rollback.
    assert len(r11) == 138
    assert sum(x["consumer_id"] == "MAYAK_REFERENCE" for x in r11) == 18
    assert "Environment=CRIPTA_SOURCE_PROFILE=P90_MINIMAL" in unit
    assert "Environment=CRIPTA_R11_MAYAK_ANCHORS=0" in unit
    assert "Environment=CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES=0" in unit
    assert 'p90_minimal_public_feeds.json' in installer
    assert 'r11_seven_public_feeds.json' in installer
    assert "P90_MINIMAL_ANCHORS_MUST_BE_OFF" in installer
    assert "P90_MINIMAL_ORIGINALS_MUST_BE_OFF" in installer
    assert "SOURCE_EXACT_MANIFEST_INSTALL_BYTE_MISMATCH" in installer


def test_offline_p90_minimal_native_cutover_never_restarts_legacy_writer() -> None:
    """The deliberate stopped GAP producer is an exact alternative preflight."""
    source = INSTALLER.read_text(encoding="utf-8")
    assert "native_source_offline_cutover=0" in source
    assert "NATIVE_SOURCE_OFFLINE_CUTOVER_PREPARED=PASS" in source
    assert 'Environment=CRIPTA_SOURCE_PROFILE=P90_MINIMAL' in source
    assert 'Environment=CRIPTA_R11_MAYAK_ANCHORS=0' in source
    assert 'Environment=CRIPTA_R11_OPTIONAL_ORIGINAL_FRAMES=0' in source
    for reject in (
        "NATIVE_OFFLINE_SOURCE_NOT_INACTIVE",
        "NATIVE_OFFLINE_SOURCE_PID_NOT_ZERO",
        "NATIVE_OFFLINE_FINANCIAL_GATE_NOT_CLOSED",
        "NATIVE_OFFLINE_HOT_EXPOSURE_NOT_FLAT",
        "NATIVE_OFFLINE_POSITION_OWNERSHIP_NOT_FLAT",
        "NATIVE_OFFLINE_SOURCE_STATUS_OR_COMMIT_INVALID",
        "NATIVE_OFFLINE_ACTIVE_SOURCE_SESSION_PRESENT",
        "NATIVE_OFFLINE_LAST_SESSION_MISSING",
        "NATIVE_OFFLINE_OLD_SESSION_NOT_OWNER_STOPPED_GAP",
        "NATIVE_OFFLINE_NO_PRODUCER_ABI_DELTA",
        "NATIVE_OFFLINE_SOURCE_STARTED_DURING_RELEASE",
        "NATIVE_OFFLINE_SOURCE_EPOCH_STARTED_DURING_RELEASE",
        "NATIVE_OFFLINE_OLD_GAP_CHANGED",
    ):
        assert reject in source
    before_cutover = source.index(
        "NATIVE_SOURCE_OFFLINE_CUTOVER_PREPARED=PASS"
    )
    new_lock = source.index(
        "NATIVE_SOURCE_OLD_PRODUCER=ALREADY_INACTIVE_WITH_EXPLICIT_GAP"
    )
    switch = source.index('mv -Tf "$next_runtime" "$RUNTIME_ROOT/current"')
    fresh_start = source.index(
        "systemctl start cripta-native-public-trade-source.service", switch
    )
    assert before_cutover < new_lock < switch < fresh_start
    offline_block = source[
        source.index('if [[ "$native_source_offline_cutover" == "1" ]]; then'):
        source.index('  else\n    [[ "$native_source_was_active" == "1" ]]')
    ]
    assert "systemctl start cripta-native-public-trade-source.service" not in offline_block
    assert "systemctl stop cripta-native-public-trade-source.service" not in offline_block
    assert "native_source_stopped_for_cutover=1" not in offline_block
    assert "NATIVE_OFFLINE_SOURCE_EPOCH_STARTED_DURING_RELEASE" in offline_block
    assert "NATIVE_OFFLINE_OLD_GAP_CHANGED" in offline_block


def test_offline_native_source_bash_is_syntactically_valid() -> None:
    """No production execution; CLI parsing only on Unix CI runners."""
    if shutil.which("bash") is None:
        pytest.skip("bash unavailable on this platform")
    outcome = subprocess.run(
        ["bash", "-n", str(INSTALLER)],
        check=False, text=True, capture_output=True,
    )
    assert outcome.returncode == 0, outcome.stderr
