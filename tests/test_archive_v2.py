from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operations.dashboard import archive_v2


def test_code_filter_rejects_secrets_caches_and_binary_databases() -> None:
    rejected = (
        Path(".git/config"),
        Path(".env"),
        Path("src/__pycache__/x.pyc"),
        Path("test_gate_venv/bin/python"),
        Path("reports/result.json"),
        Path("data.sqlite"),
    )
    assert all(archive_v2._excluded(path) for path in rejected)
    assert not archive_v2._excluded(Path("docs/PROJECT_ARCHITECTURE_RU.md"))
    assert not archive_v2._excluded(Path("config/strategy_dispatcher/profile.json"))


def test_job_status_is_persistent_and_readable_after_a_new_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archive_v2, "JOB_ROOT", tmp_path)
    job_id = "01234567-89ab-cdef-0123-456789abcdef"
    archive_v2._json_write(archive_v2._job_path(job_id), {"job_id": job_id, "stage": "CODE"})
    assert archive_v2.read_job(job_id)["stage"] == "CODE"


def test_bundle_verification_detects_changed_component(tmp_path: Path) -> None:
    code_path = tmp_path / "01_CODE.zip"
    with zipfile.ZipFile(code_path, "w") as code:
        code.writestr("tests/test_archive_v2.py", "pass\n")
    payload = code_path.read_bytes()
    index = {
        "bundle_cutoff_time_utc": "2026-08-31T00:00:00+00:00",
        "components": [
            {
                "name": "01_CODE.zip",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ]
    }
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as outer:
        outer.writestr("00_INDEX.json", json.dumps(index))
        outer.writestr("01_CODE.zip", payload + b"changed")
    with pytest.raises(RuntimeError, match="контрольная сумма"):
        archive_v2.verify_bundle(bundle)


def test_code_component_has_no_source_checkout_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source_checkout"
    (source / "src").mkdir(parents=True)
    (source / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(archive_v2, "CODE_ROOT", source)
    assert list(archive_v2._code_files())[0][1] == "src/module.py"


def test_invalid_profile_and_period_fail_before_background_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="профиль"):
        archive_v2.start_job("UNKNOWN", "3d")
    with pytest.raises(ValueError, match="период"):
        archive_v2.start_job("CODE", "week")


def test_job_identifier_cannot_escape_state_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archive_v2, "JOB_ROOT", tmp_path)
    with pytest.raises(ValueError, match="идентификатор"):
        archive_v2._job_path("../../etc/passwd")


def test_period_copy_obeys_both_since_and_cutoff(tmp_path: Path) -> None:
    source = tmp_path / "reports"
    source.mkdir()
    old, current, future = source / "old.txt", source / "current.txt", source / "future.txt"
    for path in (old, current, future):
        path.write_text(path.stem, encoding="utf-8")
    cutoff = datetime.now(UTC)
    import os

    os.utime(old, (cutoff.timestamp() - 400_000,) * 2)
    os.utime(current, (cutoff.timestamp() - 60,) * 2)
    os.utime(future, (cutoff.timestamp() + 60,) * 2)
    output = tmp_path / "period.zip"
    archive_v2._copy_period_files(source, output, cutoff, cutoff - timedelta(days=3))
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["current.txt"]


def test_postgres_dump_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Result:
        returncode = 1
        stderr = "database unavailable"

    monkeypatch.setattr(archive_v2.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(RuntimeError, match="database unavailable"):
        archive_v2._postgres_dump(tmp_path / "failed.dump", datetime.now(UTC))


def test_json_state_write_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    archive_v2._json_write(target, {"status": "DONE"})
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "DONE"
    assert not target.with_suffix(".json.tmp").exists()


def test_code_zip_keeps_json_and_complete_test_tree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "config").mkdir(parents=True)
    (source / "tests" / "fixtures").mkdir(parents=True)
    (source / "config" / "profile.json").write_text("{}", encoding="utf-8")
    (source / "tests" / "test_one.py").write_text("def test_one(): pass", encoding="utf-8")
    (source / "tests" / "fixtures" / "sample.json").write_text("{}", encoding="utf-8")
    files = [
        (path, path.relative_to(source).as_posix()) for path in source.rglob("*") if path.is_file()
    ]
    output = tmp_path / "code.zip"
    archive_v2._zip_files(output, files)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "config/profile.json",
            "tests/test_one.py",
            "tests/fixtures/sample.json",
        }


def test_failed_builder_persists_failed_job_without_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archive_v2, "JOB_ROOT", tmp_path / "jobs")
    archive_v2.JOB_ROOT.mkdir()
    job_id = "11111111-1111-1111-1111-111111111111"
    archive_v2._json_write(
        archive_v2._job_path(job_id),
        {"job_id": job_id, "status": "QUEUED", "stage": "PREPARE"},
    )

    def fail(_job_id: str) -> Path:
        raise RuntimeError("smoke failed")

    monkeypatch.setattr(archive_v2, "_build", fail)
    archive_v2._run_job(job_id)
    state = archive_v2.read_job(job_id)
    assert state["status"] == "FAILED"
    assert "smoke failed" in state["error"]
    assert state.get("output") is None


def test_statistics_cutoff_violation_fails_smoke(tmp_path: Path) -> None:
    code_path = tmp_path / "01_CODE.zip"
    with zipfile.ZipFile(code_path, "w") as code:
        code.writestr("tests/test_archive_v2.py", "pass\n")
    statistics_path = tmp_path / "03_STATISTICS_3d.zip"
    row = b'{"observed_at":"2026-09-01T00:00:00+00:00"}\n'
    with zipfile.ZipFile(statistics_path, "w") as statistics:
        statistics.writestr("mayak/events.jsonl", row)
        statistics.writestr(
            "STATISTICS_MANIFEST.json",
            json.dumps(
                {
                    "tables": [
                        {
                            "table": "mayak_v2.events",
                            "path": "mayak/events.jsonl",
                            "row_count": 1,
                            "time_column": "observed_at",
                        }
                    ]
                }
            ),
        )
    components = []
    for component in (code_path, statistics_path):
        payload = component.read_bytes()
        components.append(
            {
                "name": component.name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    bundle = tmp_path / "cutoff.zip"
    with zipfile.ZipFile(bundle, "w") as outer:
        outer.writestr(
            "00_INDEX.json",
            json.dumps(
                {
                    "profile": "CODE",
                    "period": "3d",
                    "bundle_cutoff_time_utc": "2026-08-31T00:00:00+00:00",
                    "components": components,
                }
            ),
        )
        outer.write(code_path, code_path.name)
        outer.write(statistics_path, statistics_path.name)
    with pytest.raises(RuntimeError, match="позже общего cutoff"):
        archive_v2.verify_bundle(bundle)


def test_legacy_endpoint_only_starts_async_job() -> None:
    source = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    block = source[source.index('if path in {"/api/project/package"') :]
    block = block[: block.index('if path == "/api/trading/export"')]
    assert 'start_archive_job("ANALYSIS_FULL", "3d")' in block
    assert "package_project()" not in block
