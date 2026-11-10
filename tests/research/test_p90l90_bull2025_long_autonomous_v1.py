"""Non-server tests for the one-shot launch and durable 183-day controller."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.research import p90l90_bull2025_long_autonomous_v1 as ctl


def state(days: int, published: bool = False) -> dict:
    return {
        "completed_days": days, "total_days": 183,
        "final_result_published": published,
    }


def fake_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    lock_file = tmp_path / "controller.lock"
    lock_file.touch()
    monkeypatch.setattr(ctl, "LOCK", lock_file)
    monkeypatch.setattr(ctl, "LAUNCH", tmp_path / "launch.json")
    (tmp_path / "launch.json").write_text(
        json.dumps({"controller_sha256": "fake-sha"}), encoding="utf-8"
    )
    monkeypatch.setattr(ctl, "SOURCE", tmp_path)
    monkeypatch.setattr(ctl, "CONTROLLER_REL", "controller.py")
    (tmp_path / "controller.py").write_text("fake source", encoding="utf-8")
    monkeypatch.setattr(ctl.chunk.old, "sha", lambda _: "fake-sha")
    monkeypatch.setattr(ctl, "validate_actor_and_path", lambda: None)
    monkeypatch.setattr(ctl.chunk.old.entry, "git_head", lambda: "f" * 40)
    monkeypatch.setattr(ctl.shutil, "disk_usage", lambda _: SimpleNamespace(free=20 * 2**30))
    statuses = []
    monkeypatch.setattr(
        ctl, "status",
        lambda stage, started_at, **kwargs: statuses.append((stage, kwargs)),
    )
    return lock_file, statuses


@pytest.mark.skipif(os.name != "posix", reason="Linux flock and real proc are required")
def test_long_controller_continues_every_five_days_to_final(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    lock_file, statuses = fake_worker(monkeypatch, tmp_path)
    current = {"days": 7, "published": False}
    calls = []

    def observed_snapshot():
        return state(current["days"], current["published"])

    def mock_run(side: str, head: str, max_days: int):
        assert side == "LONG_ONLY"
        assert head == "f" * 40
        assert max_days == 5
        calls.append(current["days"])
        current["days"] = min(183, current["days"] + max_days)
        if current["days"] == 183:
            current["published"] = True
            return {"status": "INTERNAL_QC_PASS_PENDING_INDEPENDENT"}
        return {"status": "PARTIAL_DURABLE_RESUME_READY_NOT_FINAL"}

    monkeypatch.setattr(ctl, "snapshot", observed_snapshot)
    monkeypatch.setattr(ctl.chunk, "run", mock_run)
    with lock_file.open("a+") as f:
        ctl.worker("f" * 40, f.fileno(), "2026-11-11T00:00:00Z")
    assert len(calls) == 36
    assert calls[:3] == [7, 12, 17]
    assert calls[-1] == 182
    assert current == {"days": 183, "published": True}
    assert statuses[-1][0] == "COMPLETED_INTERNAL_QC_PENDING_INDEPENDENT"
    assert sum(x[0] == "BATCH_COMMITTED" for x in statuses) == 35


@pytest.mark.skipif(os.name != "posix", reason="Linux flock and real proc are required")
def test_long_controller_fails_closed_without_losing_committed_days(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    lock_file, statuses = fake_worker(monkeypatch, tmp_path)
    done = {"days": 7}
    monkeypatch.setattr(ctl, "snapshot", lambda: state(done["days"]))

    def fail_after_first(side: str, head: str, max_days: int):
        if done["days"] == 7:
            done["days"] += 5
            return {"status": "PARTIAL_DURABLE_RESUME_READY_NOT_FINAL"}
        raise ValueError("actual native day SHA changed")

    monkeypatch.setattr(ctl.chunk, "run", fail_after_first)
    with (
        lock_file.open("a+") as f,
        pytest.raises(ValueError, match="native day SHA changed"),
    ):
        ctl.worker("f" * 40, f.fileno(), "2026-11-11T00:00:00Z")
    assert done["days"] == 12
    assert statuses[-1][0] == "FAILED_STOPPED_CHECKPOINTS_PRESERVED"
    assert "native day SHA changed" in statuses[-1][1]["failure"]


def test_atomic_json_preserves_complete_status(tmp_path: Path, monkeypatch):
    status_path = tmp_path / "status.json"
    monkeypatch.setattr(ctl.chunk, "fsync_dir", lambda _: None)
    ctl.atomic_json(status_path, {"status": "RUNNING", "days": 7})
    assert json.loads(status_path.read_text(encoding="utf-8")) == {
        "status": "RUNNING", "days": 7,
    }
    ctl.atomic_json(status_path, {"status": "STOPPED", "days": 12})
    assert json.loads(status_path.read_text(encoding="utf-8")) == {
        "status": "STOPPED", "days": 12,
    }


def test_controller_scope_is_single_direction_and_bounded():
    assert ctl.SIDE == "LONG_ONLY"
    assert ctl.BATCH_DAYS == 5
    assert ctl.chunk.TOTAL_DAYS == 183
    assert ctl.ID == "P90L90_BULL2025_LONG_AUTONOMOUS_V1"
    assert str(ctl.CTRL).startswith("/data/cripta/research/")
    assert str(ctl.STATUS).startswith("/data/cripta/research/")
    assert str(ctl.LOG).startswith("/data/cripta/research/")
    source = Path(ctl.__file__).read_text(encoding="utf-8")
    assert "start_new_session=True" in source
    assert "pass_fds=(lock.fileno(),)" in source
    assert "subprocess.Popen(" in source
    assert "short_only" not in source.lower()
    assert "LIVE_ARM" not in source
