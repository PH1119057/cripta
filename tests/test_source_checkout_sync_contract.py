from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations" / "infrastructure" / "cripta-source-sync"
SERVICE = ROOT / "operations" / "systemd" / "cripta-source-sync.service"
TIMER = ROOT / "operations" / "systemd" / "cripta-source-sync.timer"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _run_sync(repo: Path, lock_file: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "CRIPTA_SOURCE_CHECKOUT": str(repo),
            "CRIPTA_SOURCE_REMOTE": "origin",
            "CRIPTA_SOURCE_BRANCH": "main",
            "CRIPTA_SOURCE_SYNC_LOCK": str(lock_file),
        }
    )
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    mirror = tmp_path / "mirror"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
    _git(seed, "config", "user.email", "sync-test@example.invalid")
    _git(seed, "config", "user.name", "sync-test")
    (seed / "state.txt").write_text("one\n", encoding="utf-8")
    _git(seed, "add", "state.txt")
    _git(seed, "commit", "-m", "one")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")
    subprocess.run(["git", "clone", "-b", "main", str(origin), str(mirror)], check=True, capture_output=True)
    return origin, seed, mirror


def test_sync_fast_forwards_clean_mirror(tmp_path: Path) -> None:
    _, seed, mirror = _fixture(tmp_path)
    before = _git(mirror, "rev-parse", "HEAD")
    (seed / "state.txt").write_text("two\n", encoding="utf-8")
    _git(seed, "add", "state.txt")
    _git(seed, "commit", "-m", "two")
    _git(seed, "push", "origin", "main")
    remote = _git(seed, "rev-parse", "HEAD")

    result = _run_sync(mirror, tmp_path / "sync.lock")

    assert result.returncode == 0, result.stderr
    assert "SOURCE_SYNC=UPDATED" in result.stdout
    assert _git(mirror, "rev-parse", "HEAD") == remote
    assert _git(mirror, "status", "--porcelain") == ""
    assert before != remote


def test_sync_blocks_dirty_worktree_without_moving_head(tmp_path: Path) -> None:
    _, seed, mirror = _fixture(tmp_path)
    before = _git(mirror, "rev-parse", "HEAD")
    (mirror / "local.txt").write_text("do not overwrite\n", encoding="utf-8")
    (seed / "state.txt").write_text("remote change\n", encoding="utf-8")
    _git(seed, "add", "state.txt")
    _git(seed, "commit", "-m", "remote")
    _git(seed, "push", "origin", "main")

    result = _run_sync(mirror, tmp_path / "sync.lock")

    assert result.returncode == 20
    assert "reason=dirty_worktree" in result.stderr
    assert _git(mirror, "rev-parse", "HEAD") == before
    assert (mirror / "local.txt").read_text(encoding="utf-8") == "do not overwrite\n"


def test_sync_blocks_diverged_history(tmp_path: Path) -> None:
    _, seed, mirror = _fixture(tmp_path)
    _git(mirror, "config", "user.email", "sync-test@example.invalid")
    _git(mirror, "config", "user.name", "sync-test")
    (mirror / "local-commit.txt").write_text("local\n", encoding="utf-8")
    _git(mirror, "add", "local-commit.txt")
    _git(mirror, "commit", "-m", "local")
    local_head = _git(mirror, "rev-parse", "HEAD")

    (seed / "remote-commit.txt").write_text("remote\n", encoding="utf-8")
    _git(seed, "add", "remote-commit.txt")
    _git(seed, "commit", "-m", "remote")
    _git(seed, "push", "origin", "main")

    result = _run_sync(mirror, tmp_path / "sync.lock")

    assert result.returncode == 20
    assert "reason=non_fast_forward" in result.stderr
    assert _git(mirror, "rev-parse", "HEAD") == local_head


def test_systemd_contract_is_non_deploying_and_periodic() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")

    assert "User=cripta" in service
    assert "ExecStart=/usr/local/sbin/cripta-source-sync" in service
    assert "ReadWritePaths=/srv/cripta/source_checkout" in service
    assert "ProtectSystem=strict" in service
    assert "OnUnitInactiveSec=5min" in timer
    assert "Persistent=true" in timer

    script = SCRIPT.read_text(encoding="utf-8")
    assert "merge-base --is-ancestor" in script
    assert "merge --ff-only" in script
    assert "dirty_worktree" in script
    for forbidden in ("systemctl restart", "systemctl start cripta-", "install_verified_release"):
        assert forbidden not in script
