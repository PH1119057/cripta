"""First-month OB200 terminal manifest lifecycle; never alter old result bundles."""
import datetime as dt
import importlib.util
import json
import os
import types
import zipfile
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_firstmonth_book9_11_29_utcclip_v2.py"
)
SPEC = importlib.util.spec_from_file_location("r158_book_terminal", SCRIPT)
SUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUT)

COMPLETED = "PHYSICAL_BOOK_MONTH_COMPLETE_PENDING_INDEPENDENT_QC"


def test_success_terminal_manifest_is_atomic_and_pending_qc(tmp_path):
    path = tmp_path / "run_manifest.json"
    initial = {
        "status": "RUNNING",
        "actor": "cripta",
        "source_commit": "original",
        "receiver_clock": "UNKNOWN",
    }
    path.write_text(json.dumps(initial))
    result = {"status": COMPLETED, "output_sha256": "abc", "rows": 2880}
    SUT.finalize_run_manifest(path, COMPLETED, result=result)
    final = json.loads(path.read_text())
    assert final["status"] == COMPLETED
    assert final["source_commit"] == "original"
    assert final["actor"] == "cripta"
    assert final["receiver_clock"] == "UNKNOWN"
    assert final["rows"] == 2880
    assert final["output_sha256"] == "abc"
    assert final["independent_qc"] == "PENDING"
    assert not (tmp_path / "run_manifest.json.terminal.part").exists()
    with pytest.raises(ValueError, match="DO_NOT_OVERWRITE"):
        SUT.finalize_run_manifest(path, COMPLETED, result=result)


def test_failed_terminal_manifest_preserves_failure_and_identity(tmp_path):
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps({"status": "RUNNING", "scope": ["APTUSDT", 2]}))
    SUT.finalize_run_manifest(path, "FAILED", error=ValueError("BAD_SOURCE"))
    final = json.loads(path.read_text())
    assert final["status"] == "FAILED"
    assert final["scope"] == ["APTUSDT", 2]
    assert final["error_type"] == "ValueError"
    assert final["error_message"] == "BAD_SOURCE"
    assert final["independent_qc"] == "NOT_RUN"


def test_terminal_manifest_refuses_missing_or_inconsistent_evidence(tmp_path):
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps({"status": "RUNNING"}))
    original = path.read_bytes()
    with pytest.raises(ValueError, match="TERMINAL_RESULT_REQUIRED"):
        SUT.finalize_run_manifest(path, COMPLETED)
    with pytest.raises(ValueError, match="TERMINAL_ERROR_REQUIRED"):
        SUT.finalize_run_manifest(path, "FAILED")
    with pytest.raises(ValueError, match="UNDECLARED_TERMINAL_STATUS"):
        SUT.finalize_run_manifest(path, "RESEARCH_READY")
    assert path.read_bytes() == original


def make_day_archive(root, symbol, day, *, invalid=False):
    folder = root / symbol / "orderbook"
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / f"{day.isoformat()}_{symbol}_ob200.data.zip"
    member = f"{day.isoformat()}_{symbol}_ob200.data"
    unix_ms = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
    record = {
        "ts": unix_ms + 1000,
        "type": "delta" if invalid else "snapshot",
        "data": {
            "b": [["100", "2"]],
            "a": [["100.01", "3"]],
            "seq": 1,
            "u": 1,
        },
    }
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
        out.writestr(member, json.dumps(record) + "\n")


def setup_isolated_two_day_run(tmp_path, monkeypatch, *, invalid_second=False):
    import pwd

    source = tmp_path / "raw"
    repo = tmp_path / "checkout"
    ref = repo / ".git/refs/heads/main"
    ref.parent.mkdir(parents=True)
    ref.write_text("source-test-sha\n")
    output = tmp_path / "research" / "book"
    output.parent.mkdir(parents=True)
    for i in range(2):
        make_day_archive(
            source, "APTUSDT", SUT.FIRST + dt.timedelta(days=i),
            invalid=(invalid_second and i == 1),
        )
    monkeypatch.setattr(SUT, "ROOT", source)
    monkeypatch.setattr(SUT, "OUTPUT", output)
    monkeypatch.setattr(SUT, "REPO", repo)
    monkeypatch.setattr(
        pwd, "getpwnam", lambda _user: types.SimpleNamespace(pw_uid=os.geteuid())
    )
    return output / "physical_2d/APTUSDT"


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="requires Unix actor check")
def test_full_two_day_success_updates_run_manifest(tmp_path, monkeypatch):
    target = setup_isolated_two_day_run(tmp_path, monkeypatch)
    SUT.run("APTUSDT", 2)
    manifest = json.loads((target / "run_manifest.json").read_text())
    result = json.loads((target / "result.json").read_text())
    assert manifest["status"] == result["status"] == COMPLETED
    assert manifest["rows"] == result["rows"] == 2880
    assert manifest["output_sha256"] == result["output_sha256"]
    assert manifest["source_commit"] == "source-test-sha"
    assert (target / "features.jsonl.gz").is_file()
    assert not (target / "features.jsonl.gz.part").exists()
    assert not (target / "independent_qc_report.json").exists()
    assert not (target / "run_manifest.json.terminal.part").exists()


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="requires Unix actor check")
def test_failed_two_day_run_marks_failed_without_overwrite(tmp_path, monkeypatch):
    target = setup_isolated_two_day_run(
        tmp_path, monkeypatch, invalid_second=True
    )
    with pytest.raises(ValueError, match="DELTA_BEFORE_INITIAL_SNAPSHOT"):
        SUT.run("APTUSDT", 2)
    manifest = json.loads((target / "run_manifest.json").read_text())
    assert manifest["status"] == "FAILED"
    assert manifest["error_type"] == "ValueError"
    assert manifest["scope"] == ["APTUSDT", 2]
    assert (target / "features.jsonl.gz.part").exists()
    assert not (target / "features.jsonl.gz").exists()
    assert not (target / "result.json").exists()
    before = (target / "run_manifest.json").read_bytes()
    with pytest.raises(FileExistsError, match="DO_NOT_OVERWRITE"):
        SUT.run("APTUSDT", 2)
    assert (target / "run_manifest.json").read_bytes() == before


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="requires Unix actor check")
def test_manifest_io_failure_does_not_relabel_committed_result(tmp_path, monkeypatch):
    target = setup_isolated_two_day_run(tmp_path, monkeypatch)

    def broken_finalize(*_args, **_kwargs):
        raise OSError("SIMULATED_FINALIZATION_ERROR")

    monkeypatch.setattr(SUT, "finalize_run_manifest", broken_finalize)
    with pytest.raises(OSError, match="SIMULATED_FINALIZATION_ERROR"):
        SUT.run("APTUSDT", 2)
    # Output is committed, but manifest update was not; do not claim FAILED.
    assert (target / "result.json").is_file()
    assert (target / "features.jsonl.gz").is_file()
    manifest = json.loads((target / "run_manifest.json").read_text())
    assert manifest["status"] == "RUNNING"
    assert not (target / "independent_qc_report.json").exists()
