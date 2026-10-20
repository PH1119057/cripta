"""All production deploy entrypoints must share one effective lock.

A full application release may reinstall Dashboard UI assets and unit files.
The independent Dashboard UI updater therefore cannot overlap its cutover.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path("operations/infrastructure")
INSTALLER = (ROOT / "install_verified_release.sh").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "deploy_dashboard_ui.sh").read_text(encoding="utf-8")


def lock_path(script: str) -> str:
    match = re.search(r"^exec 9>(/run/lock/[a-z0-9-]+\.lock)$", script, re.M)
    assert match is not None, "release entrypoint missing canonical lock"
    return match.group(1)


def test_full_and_dashboard_deploy_use_one_serialization_lock():
    expected = "/run/lock/cripta-install-verified-release.lock"
    assert lock_path(INSTALLER) == lock_path(DASHBOARD) == expected
    for script in (INSTALLER, DASHBOARD):
        assert "flock -n 9" in script
        assert script.index("flock -n 9") < script.index("ls-remote origin")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock contract")
def test_two_independent_processes_cannot_take_same_lock(tmp_path):
    fcntl = pytest.importorskip("fcntl")
    path = tmp_path / "shared-deploy.lock"
    with path.open("a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        competing = subprocess.run(
            ["bash", "-c", 'exec 9>"$1"; flock -n 9', "bash", str(path)],
            check=False, capture_output=True, text=True,
        )
        assert competing.returncode != 0, "second deploy acquired held lock"
    after = subprocess.run(
        ["bash", "-c", 'exec 9>"$1"; flock -n 9', "bash", str(path)],
        check=False, capture_output=True, text=True,
    )
    assert after.returncode == 0, after.stderr
