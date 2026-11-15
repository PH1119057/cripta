"""#470 fixed staged physical rollout contract."""
from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAIL = ROOT / "operations/infrastructure/cripta-roadmap470-staged-release"
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"
UNIT = ROOT / "operations/systemd/cripta-roadmap470-staged-release.service"
PREFLIGHT_UNIT = ROOT / (
    "operations/systemd/cripta-roadmap470-staged-release-preflight.service"
)

EXPECTED = (
    ("R07", "99a1ba4c16edbe5cb32022addf6c52888574716d",
     "bbc21196fc7233d199f1ff016ea8553450e55532"),
    ("R08", "bbc21196fc7233d199f1ff016ea8553450e55532",
     "a18a78b849ea0c6a18dfb6dce422e757466f5ab8"),
    ("R09", "a18a78b849ea0c6a18dfb6dce422e757466f5ab8",
     "1e29f9d5105ffc99a91af01c3c500cad062534ea"),
    ("R09_REPAIR", "1e29f9d5105ffc99a91af01c3c500cad062534ea",
     "c65b385fdd3365f404b4ccfce3583b51b3e3476d"),
    ("R09_REPAIR2", "c65b385fdd3365f404b4ccfce3583b51b3e3476d",
     "0e0a661695ab920a2b64ad11d5bea56bd6163302"),
    ("R10", "0e0a661695ab920a2b64ad11d5bea56bd6163302",
     "14a4fc65857d2a3158bdb3fda73578dd238057d2"),
    ("R10_ACTIVATE", "14a4fc65857d2a3158bdb3fda73578dd238057d2",
     "0cdf4ce0ca80ee4dcc5c7cc5c2595223fd4208d4"),
    ("R11", "0cdf4ce0ca80ee4dcc5c7cc5c2595223fd4208d4",
     "29cbe0229c137112e3f4e9d3b5b778ebdbc05738"),
    ("R11_REPAIR", "29cbe0229c137112e3f4e9d3b5b778ebdbc05738",
     "103ac4dc7ce0d7d06b413847938a22e356cc26b9"),
    ("R11_REPAIR2", "103ac4dc7ce0d7d06b413847938a22e356cc26b9",
     "f16601924f6623260cbe45d96547c6e9fc71067a"),
    ("R11_REPAIR3", "f16601924f6623260cbe45d96547c6e9fc71067a",
     "35923cfb7a8c1149284d2ba327a8d5ae306f5490"),
    ("R12", "35923cfb7a8c1149284d2ba327a8d5ae306f5490",
     "4ae97520e44e2580a82e08985fcad744106a3769"),
    ("R13", "4ae97520e44e2580a82e08985fcad744106a3769",
     "acec641eff61a90f302806fb913321b61d0f61c8"),
)


def test_rail_is_valid_python_no_args_and_exact_ladder_only() -> None:
    source = RAIL.read_text(encoding="utf-8")
    ast.parse(source)
    assert "len(sys.argv) != 1" in source
    assert "argparse" not in source
    assert "R14" in source and "intentionally outside this rail" in source
    for stage, baseline, target in EXPECTED:
        assert f'("{stage}", "{baseline}",' in source
        assert target in source
    assert "ROADMAP470_INSTALLED_BASELINE_NOT_IN_FIXED_LADDER" in source
    assert "ROADMAP470_RAIL_BYTES_DRIFT" in source
    assert '"merge-base", "--is-ancestor"' in source
    assert "CRIPTA_STAGED_OFFLINE_CUTOVER=1" in source
    assert "CRIPTA_NATIVE_SOURCE_CUTOVER=0" in source
    assert "/usr/sbin/runuser" not in source
    assert '"/usr/bin/systemd-run"' in source
    assert '"--property=User=cripta"' in source
    assert '"--property=Group=cripta"' in source
    assert '"--property=NoNewPrivileges=yes"' in source


def test_unit_is_manual_inert_root_management_entrypoint() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "Type=oneshot" in unit
    assert "User=root" in unit and "Group=root" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "ProtectSystem=strict" in unit
    assert (
        "ExecStart=/usr/bin/python3 "
        "/usr/local/libexec/cripta-roadmap470-staged-release/"
        "cripta-roadmap470-staged-release"
    ) in unit
    assert "timer" not in unit.lower()
    preflight = PREFLIGHT_UNIT.read_text(encoding="utf-8")
    assert "CRIPTA_ROADMAP470_PREFLIGHT_ONLY=1" in preflight
    assert "User=root" in preflight and "Group=root" in preflight
    assert "NoNewPrivileges=yes" in preflight
    assert "ProtectSystem=strict" in preflight
    source = RAIL.read_text(encoding="utf-8")
    assert 'os.environ.get("CRIPTA_ROADMAP470_PREFLIGHT_ONLY") == "1"' in source
    assert "ROADMAP470_PREFLIGHT_ONLY=PASS" in source
    # Installing the management units never means enabling or starting them.
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "cripta-roadmap470-staged-release.service" not in installer
    assert "cripta-roadmap470-staged-release-preflight.service" not in installer


def test_installer_shell_syntax_and_staged_no_start_contract() -> None:
    if os.name == "posix":
        result = subprocess.run(
            ["/usr/bin/bash", "-n", str(INSTALLER)],
            check=False, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
    source = INSTALLER.read_text(encoding="utf-8")
    assert source.startswith("#!/usr/bin/env bash\n")
    for stage, baseline, target in EXPECTED:
        assert f"{stage}) printf" in source
        assert baseline in source and target in source
    assert "ROADMAP470_STAGE_REQUIRES_STAGED_OFFLINE" in source
    assert "ROADMAP470_TARGET_NOT_EXACT_STAGE_COMMIT" in source
    assert "ROADMAP470_TARGET_NOT_ANCESTOR_OF_CURRENT_MAIN" in source
    assert "ROADMAP470_CORE_SERVICE_MUST_BE_STOPPED" in source
    assert "ROADMAP470_NO_START_INSTALL=PASS" in source
    assert "ROADMAP470_NATIVE_SOURCE_STARTED_UNEXPECTEDLY" in source
    assert "ROADMAP470_TRADING_CONTROL_CHANGED_DURING_NO_START_INSTALL" in source
    assert "POST_DEPLOY_EXCHANGE_TO_DB_RECONCILIATION=DEFERRED_NO_START" in source
    assert "ROADMAP470_INCOMPLETE_CANDIDATE_RESET=PASS" in source
    assert "rm -rf --one-file-system --" in source
    assert "ROADMAP470_INCOMPLETE_CANDIDATE_IS_CURRENT" in source
    assert "R07|R08|R09|R09_REPAIR|R09_REPAIR2|R10|R10_ACTIVATE)" in source
    assert "DISPATCHER_IMPORT_PREFLIGHT=DEFERRED_NOT_IN_STAGE" in source
    assert "import bybit_workbench.dispatcher_v2" in source


def test_rail_captures_sanitized_installer_failure_evidence() -> None:
    source = RAIL.read_text(encoding="utf-8")
    assert "capture_output=True, text=True" in source
    assert "ROADMAP470_INSTALLER_DIAGNOSTIC_BEGIN" in source
    assert "ROADMAP470_INSTALLER_DIAGNOSTIC_END" in source
    assert "SENSITIVE_DIAGNOSTIC" in source
    assert "safe_lines" in source


def test_staged_migrations_do_not_reach_forward() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    assert "20261114_r07_source_epoch_transaction_guard.sql" in source
    assert "20261114_r11_optional_original_public_event_frames.sql" in source
    assert "20261114_r11_optional_publication_frame_refs.sql" in source
    assert "20261115_r11_mayak_consumer_commit.sql" in source
    assert "20261115_r13_opportunity_source_checkpoint.sql" in source
    assert 'case "$ROADMAP470_STAGE" in\n  ""|R11|R11_REPAIR|R11_REPAIR2|R11_REPAIR3|R12|R13)' in source
    assert 'case "$ROADMAP470_STAGE" in\n  ""|R13)' in source


def test_stage_chain_is_contiguous_and_sha_shaped() -> None:
    sha = re.compile(r"^[0-9a-f]{40}$")
    for i, (_, baseline, target) in enumerate(EXPECTED):
        assert sha.fullmatch(baseline)
        assert sha.fullmatch(target)
        if i:
            assert EXPECTED[i - 1][2] == baseline
