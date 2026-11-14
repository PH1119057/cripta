"""Issue #642: source-only test for the two retired backup ZIP exclusions.

This intentionally does not run the production backup, read credentials,
connect to PostgreSQL or change existing backup generations.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


BACKUP_SH = (
    Path(__file__).resolve().parents[1]
    / "research"
    / "server"
    / "backup"
    / "backup.sh"
)
EXPECTED_EXCLUDES = (
    "*/cripta_project_trading_3d_202608*.zip",
    "*/аналитика_сигналов_72_часа_202609*.zip",
)


def _actual_excludes() -> tuple[str, ...]:
    script = BACKUP_SH.read_text(encoding="utf-8")
    return tuple(re.findall(r"--exclude='([^']+)'", script))


def test_backup_exclusion_is_only_the_two_retired_classes() -> None:
    script = BACKUP_SH.read_text(encoding="utf-8")
    assert _actual_excludes() == EXPECTED_EXCLUDES
    assert "/srv/cripta /srv/cripta-share/reports" in script
    assert "sudo -u postgres pg_dump --format=custom cripta" in script
    assert "sha256sum --check SHA256SUMS" in script
    assert "pg_restore --list cripta.pgdump" in script
    assert "verified_generations" in script


@pytest.mark.skipif(sys.platform != "linux", reason="GNU tar contract")
def test_tar_retains_operational_and_audit_files(tmp_path: Path) -> None:
    tar = shutil.which("tar")
    if tar is None:
        pytest.skip("GNU tar unavailable")

    source = tmp_path / "srv" / "cripta"
    reports = tmp_path / "srv" / "cripta-share" / "reports"
    jobs = reports / "jobs"
    source.mkdir(parents=True)
    jobs.mkdir(parents=True)

    keep = (
        source / "current_release.txt",
        jobs / "recovery_job.json",
        reports / "закрытые_реальные_сделки_сутки_20260905.zip",
        reports / "CRIPTA_LIVE_FORENSICS_20260905.zip",
        reports / "current_report.txt",
    )
    retire = (
        reports / "cripta_project_trading_3d_20260830_121518.zip",
        reports / "аналитика_сигналов_72_часа_20260906_001628.zip",
    )
    for entry in (*keep, *retire):
        entry.write_bytes(entry.name.encode("utf-8"))

    archive = tmp_path / "project.tar.gz"
    subprocess.run(
        [
            tar,
            "--create",
            "--gzip",
            "--file",
            str(archive),
            "-C",
            str(tmp_path),
            *(f"--exclude={pattern}" for pattern in _actual_excludes()),
            "srv/cripta",
            "srv/cripta-share/reports",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    with tarfile.open(archive, "r:gz") as file:
        members = set(file.getnames())

    for entry in keep:
        assert str(entry.relative_to(tmp_path)) in members
    for entry in retire:
        assert str(entry.relative_to(tmp_path)) not in members
        assert entry.is_file()  # source files never deleted


@pytest.mark.skipif(sys.platform != "linux", reason="bash syntax")
def test_backup_script_bash_syntax() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash unavailable")
    subprocess.run([bash, "-n", str(BACKUP_SH)], check=True)
