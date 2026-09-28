from __future__ import annotations

import getpass
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "operations" / "infrastructure" / "cripta-permission-preflight"
DAILY = ROOT / "operations" / "systemd" / "cripta-mayak-v2-report.service"
WEEKLY = ROOT / "operations" / "systemd" / "cripta-mayak-v2-weekly-report.service"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(PREFLIGHT), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_permission_preflight_passes_for_actual_actor(tmp_path: Path) -> None:
    actor = getpass.getuser()
    readable = tmp_path / "readable.txt"
    readable.write_text("ok\n", encoding="utf-8")

    result = _run(
        "--actor",
        actor,
        "--check",
        f"read:{readable}",
        "--check",
        f"create-child:{tmp_path}",
    )

    assert result.returncode == 0, result.stderr
    assert "PERMISSION_PREFLIGHT=PASS" in result.stdout


def test_permission_preflight_fails_before_mutation_on_unwritable_parent(tmp_path: Path) -> None:
    actor = getpass.getuser()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        result = _run(
            "--actor",
            actor,
            "--check",
            f"create-child:{locked}",
        )
    finally:
        locked.chmod(0o700)

    assert result.returncode == 20
    assert "reason=create_denied:" in result.stderr


def test_mayak_report_units_preflight_exact_writer_and_parent() -> None:
    expected = (
        "SupplementaryGroups=cripta-share",
        "ExecStartPre=/usr/local/sbin/cripta-permission-preflight "
        "--actor cripta --check create-child:/srv/cripta-share/reports",
        "ReadWritePaths=/srv/cripta-share/reports",
    )
    for unit_path in (DAILY, WEEKLY):
        body = unit_path.read_text(encoding="utf-8")
        for token in expected:
            assert token in body


def test_preflight_is_non_mutating() -> None:
    body = PREFLIGHT.read_text(encoding="utf-8")
    for forbidden in ("chmod ", "chown ", "mkdir ", "rm ", "touch ", "install "):
        assert forbidden not in body
