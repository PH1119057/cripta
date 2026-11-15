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

EXPECTED = (
    ("R07", "99a1ba4c16edbe5cb32022addf6c52888574716d",
     "bbc21196fc7233d199f1ff016ea8553450e55532"),
    ("R08", "bbc21196fc7233d199f1ff016ea8553450e55532",
     "a18a78b849ea0c6a18dfb6dce422e757466f5ab8"),
    ("R09", "a18a78b849ea0c6a18dfb6dce422e757466f5ab8",
     "1e29f9d5105ffc99a91af01c3c500cad062534ea"),
    ("R10", "1e29f9d5105ffc99a91af01c3c500cad062534ea",
     "dbe65bdd1883b09f752ef62864a8221bbb75106e"),
    ("R11", "dbe65bdd1883b09f752ef62864a8221bbb75106e",
     "caa5a636a67c6ff677a9796d172c8e072f71b4db"),
    ("R12", "caa5a636a67c6ff677a9796d172c8e072f71b4db",
     "5dfac7de21152ce67bfc2fc333eb53d22f292a15"),
    ("R13", "5dfac7de21152ce67bfc2fc333eb53d22f292a15",
     "b7731f1c7788703e0b9043c7b5dc0cc007552d4e"),
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
    # Installing the unit never means enabling or starting it.
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "cripta-roadmap470-staged-release.service" not in installer


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


def test_staged_migrations_do_not_reach_forward() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    assert "20261114_r07_source_epoch_transaction_guard.sql" in source
    assert "20261114_r11_optional_original_public_event_frames.sql" in source
    assert "20261114_r11_optional_publication_frame_refs.sql" in source
    assert "20261115_r11_mayak_consumer_commit.sql" in source
    assert "20261115_r13_opportunity_source_checkpoint.sql" in source
    assert 'case "$ROADMAP470_STAGE" in\n  ""|R11|R12|R13)' in source
    assert 'case "$ROADMAP470_STAGE" in\n  ""|R13)' in source


def test_stage_chain_is_contiguous_and_sha_shaped() -> None:
    sha = re.compile(r"^[0-9a-f]{40}$")
    for i, (_, baseline, target) in enumerate(EXPECTED):
        assert sha.fullmatch(baseline)
        assert sha.fullmatch(target)
        if i:
            assert EXPECTED[i - 1][2] == baseline
