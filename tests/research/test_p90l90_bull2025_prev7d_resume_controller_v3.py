"""Immutable QC and independently supervised resume contract (#617)."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.research import p90l90_bull2025_prev7d_resume_controller_v3 as resume


def test_frozen_lineage_and_exact_checkpoint_identity():
    assert resume.FROZEN_HEAD == "ea170ba1c08e4cbd16f8c328834217623e365278"
    assert resume.FROZEN_SRC_SHA256 == (
        "af41b9271d8318a45e268e6f1932c35daba3735a890b95339079903c3fb78120"
    )
    assert resume.EXPECTED_DAYS[("010", "long_only")] == 183
    assert resume.EXPECTED_DAYS[("010", "short_only")] == 100
    assert len(resume.EXPECTED_DAYS) == 12
    assert resume.SIDES == ("long_only", "short_only")
    assert resume.BANDS == ("010", "020", "080", "090", "100", "150")


def test_preflight_fails_closed_on_wrong_effective_actor(monkeypatch):
    monkeypatch.setattr(resume.os, "geteuid", lambda: 998, raising=False)
    monkeypatch.setattr(resume.os, "getegid", lambda: 998, raising=False)
    with pytest.raises(PermissionError, match="actual service"):
        resume.guard(SimpleNamespace(), "unused")


def test_resume_reuses_only_identical_existing_qc(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, "OUT", tmp_path)
    qc = tmp_path / "band010" / "qc_long_only.json"
    qc.parent.mkdir()
    qc.write_text(json.dumps({"status": "validated", "n": 25}))
    original_bytes = qc.read_bytes()
    calls = []

    def old_writer(path, value):
        calls.append((path, value))

    engine = SimpleNamespace(write_once=old_writer)
    resume.install_exact_qc_reuse(engine)
    engine.write_once(qc, {"n": 25, "status": "validated"})
    assert calls == []
    assert qc.read_bytes() == original_bytes  # immutable SHA preserved

    with pytest.raises(ValueError, match="recomputed QC differs"):
        engine.write_once(qc, {"n": 26, "status": "validated"})
    assert qc.read_bytes() == original_bytes

    other = tmp_path / "band020" / "qc_long_only.json"
    other.parent.mkdir()
    other.write_text("{}")
    engine.write_once(other, {"status": "another"})
    assert calls == [(other, {"status": "another"})]

    fresh = tmp_path / "band010" / "qc_short_only.json"
    engine.write_once(fresh, {"status": "first"})
    assert calls[-1] == (fresh, {"status": "first"})


def test_symlink_existing_qc_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, "OUT", tmp_path)
    qc = tmp_path / "band010" / "qc_long_only.json"
    qc.parent.mkdir()
    target = tmp_path / "real-qc.json"
    target.write_text('{"x":1}')
    qc.symlink_to(target)
    def fail_writer(path, value):
        raise FileExistsError("symlink prohibited")
    engine = SimpleNamespace(write_once=fail_writer)
    resume.install_exact_qc_reuse(engine)
    with pytest.raises(FileExistsError, match="symlink prohibited"):
        engine.write_once(qc, {"x": 1})


def test_old_source_unchanged_and_no_detached_popen():
    src = inspect.getsource(resume)
    assert "subprocess.Popen" not in src
    assert "start_new_session" not in inspect.getsource(resume.main)
    assert "sweep.worker(engine, catalog, current, FROZEN_HEAD)" in src
    assert "engine.load_parts(folder)" in src
    assert "sweep.prepare(FROZEN_HEAD)" in src
    assert "controller source SHA256 drift" in src
    assert "original worker may still be alive" in src
    assert "research disk below owner-approved 5 GiB reserve" in src
    assert "recomputed QC differs from immutable LONG QC" in src


def test_no_unsafe_qc_overwrite_or_broad_reuse():
    src = inspect.getsource(resume.install_exact_qc_reuse)
    assert 'permitted = OUT / "band010" / "qc_long_only.json"' in src
    assert "path.is_file() and not path.is_symlink()" in src
    assert "original(path, value)" in src


def test_absolute_contained_results_and_not_production():
    assert resume.OUT.is_absolute()
    assert "/data/cripta/research/runs/" in str(resume.OUT)
    assert "/srv/cripta/runtime" not in str(resume.OUT)
    assert resume.FROZEN_ROOT.is_absolute()
    assert resume.FROZEN_REL.startswith("scripts/research/")
