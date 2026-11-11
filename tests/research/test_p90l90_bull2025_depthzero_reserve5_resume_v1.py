"""Owner-scoped P90L90 depthzero resume5 guard and no-duplicate tests."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.research import p90l90_bull2025_depthzero_reserve5_resume_v1 as resume


def test_owner_5gib_guard_is_inclusive(monkeypatch):
    monkeypatch.setattr(resume, "free_bytes", lambda: 5 * 2**30)
    resume.reserve5()
    monkeypatch.setattr(resume, "free_bytes", lambda: 16 * 2**30 - 1)
    resume.reserve5()  # legacy limit must not block
    monkeypatch.setattr(resume, "free_bytes", lambda: 5 * 2**30 - 1)
    with pytest.raises(RuntimeError, match="5GiB reserve"):
        resume.reserve5()


def test_original_module_still_has_full16_guard():
    # Historical already-published checkpoint algorithm must stay immutable.
    from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as engine
    from scripts.research import p90l90_bull2025_portfolio_v1 as portfolio

    assert "16 * 2**30" in inspect.getsource(engine.run)
    assert "16 * 2**30" in inspect.getsource(portfolio.verify_and_load)
    assert "reserve5()" in inspect.getsource(resume.run5)
    assert "reserve5()" in inspect.getsource(resume.verify_and_load5)


def test_source_paths_are_scoped_to_original_research_run():
    assert resume.ORIGINAL_HEAD == "87d44fb5ab6ae8e82ab63e6d765c6742d5289cf2"
    assert resume.OUT == resume.original.OUT
    assert resume.OUT.name == "portfolio_depthzero_2025_v1"
    assert resume.RESERVE_BYTES == 5 * 2**30
    assert resume.INITIAL_LONG_DAYS == 103


def test_check_resume_requires_exact_failed_old_job(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    (out / "full").mkdir()
    (out / "checkpoint").mkdir()
    (out / "status.json").write_text(
        json.dumps({"stage": "RUNNING", "error": "not failed"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(resume, "OUT", out)
    monkeypatch.setattr(resume, "free_bytes", lambda: 7 * 2**30)
    with pytest.raises(RuntimeError, match="unexpected original job status"):
        resume.check_resume(SimpleNamespace(), "a" * 40)


def test_check_resume_rejects_existing_resumed_job(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    (out / "resume5_launch.json").write_text('{"status":"RUNNING"}')
    monkeypatch.setattr(resume, "OUT", out)
    monkeypatch.setattr(resume, "free_bytes", lambda: 7 * 2**30)
    with pytest.raises(FileExistsError, match="already launched"):
        resume.check_resume(SimpleNamespace(), "a" * 40)


def test_check_resume_rejects_damaged_prior_chain(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    (out / "checkpoint" / "long_only").mkdir(parents=True)
    (out / "checkpoint" / "short_only").mkdir()
    (out / "full").mkdir()
    (out / "status.json").write_text(json.dumps({
        "stage": "FAILED",
        "error": resume.ORIGINAL_FAILURE,
        "source_head": resume.ORIGINAL_HEAD,
    }))
    (out / "launch.json").write_text(json.dumps({
        "source_head": resume.ORIGINAL_HEAD,
        "pid": 99999999,
    }))
    (out / "study.json").write_text(json.dumps({
        "source_head": resume.ORIGINAL_HEAD,
        "source_sha256": "old-sha",
        "exact_depth_percent": "0",
    }))
    monkeypatch.setattr(resume, "OUT", out)
    monkeypatch.setattr(resume, "free_bytes", lambda: 7 * 2**30)
    monkeypatch.setattr(resume.original, "sha", lambda _p: "old-sha")
    fake_engine = SimpleNamespace(
        load_parts=lambda _p: (
            {"next_day": 102},
            {"source_head_first": resume.ORIGINAL_HEAD, "index": 101},
            "old-manifest-sha",
        ),
    )
    with pytest.raises(RuntimeError, match="103-day original SHA checkpoint chain mismatch"):
        resume.check_resume(fake_engine, "a" * 40)


def test_source_only_pilot_must_not_overwrite_prior_failed_launch(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    (out / "resume5_pilot.json").write_text('{"status":"OLD"}')
    monkeypatch.setattr(resume, "OUT", out)
    monkeypatch.setattr(resume, "free_bytes", lambda: 7 * 2**30)
    with pytest.raises(FileExistsError, match="already attempted"):
        resume.check_resume(SimpleNamespace(), "a" * 40)
    assert json.loads((out / "resume5_pilot.json").read_text()) == {"status": "OLD"}


def test_worker_uses_exact_original_exit_and_replay():
    from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as engine

    source = inspect.getsource(resume.run5)
    assert "engine.replay_day(i, state, data, books, side)" in source
    assert "engine.make_part(" in source
    assert "engine.publish_final(" in source
    assert "engine.load_parts(folder, data)" in source
    assert "engine.pin_current(source_pins, first_head)" in source
    assert "engine.new_books(side, schedule)" in source
    assert "engine.restore_books(books, state[\"books\"])" in source
    assert engine.TOTAL_DAYS == 183


def test_script_located_inside_versioned_research_scope():
    assert resume.SELF_REL.startswith("scripts/research/")
    assert Path(resume.SELF_REL).suffix == ".py"


def test_preflight_accepts_absent_full_directory(tmp_path, monkeypatch):
    """The failed worker did not reach final publication; full/ legitimately absent."""
    out = tmp_path / "output"
    (out / "checkpoint" / "long_only").mkdir(parents=True)
    (out / "checkpoint" / "short_only").mkdir()
    (out / "status.json").write_text(json.dumps({
        "stage": "FAILED",
        "error": resume.ORIGINAL_FAILURE,
        "source_head": resume.ORIGINAL_HEAD,
    }))
    (out / "launch.json").write_text(json.dumps({
        "source_head": resume.ORIGINAL_HEAD,
        "pid": 99999999,
    }))
    (out / "study.json").write_text(json.dumps({
        "source_head": resume.ORIGINAL_HEAD,
        "source_sha256": "old-sha",
        "exact_depth_percent": "0",
    }))
    monkeypatch.setattr(resume, "OUT", out)
    monkeypatch.setattr(resume, "free_bytes", lambda: 7 * 2**30)
    monkeypatch.setattr(resume.original, "sha", lambda _p: "old-sha")
    def load(folder):
        if folder.name == "short_only":
            return None, None, None
        return (
            {"next_day": 103},
            {"source_head_first": resume.ORIGINAL_HEAD, "index": 102},
            "source-checkpoint-sha",
        )
    report = resume.check_resume(SimpleNamespace(load_parts=load), "a" * 40)
    assert report["status"] == "RESUME5_PREFLIGHT_PASS_NO_MUTATION"
    assert report["existing_long_days"] == 103
    assert not (out / "full").exists()


def test_preflight_rejects_nonempty_finished_output(tmp_path, monkeypatch):
    out = tmp_path / "output"
    (out / "full").mkdir(parents=True)
    (out / "full" / "incompatible_result.json").write_text("{}")
    (out / "checkpoint" / "long_only").mkdir(parents=True)
    (out / "checkpoint" / "short_only").mkdir()
    (out / "status.json").write_text(json.dumps({
        "stage": "FAILED", "error": resume.ORIGINAL_FAILURE,
        "source_head": resume.ORIGINAL_HEAD,
    }))
    (out / "launch.json").write_text(json.dumps({
        "source_head": resume.ORIGINAL_HEAD, "pid": 99999999,
    }))
    (out / "study.json").write_text(json.dumps({
        "source_head": resume.ORIGINAL_HEAD,
        "source_sha256": "old-sha",
        "exact_depth_percent": "0",
    }))
    monkeypatch.setattr(resume, "OUT", out)
    monkeypatch.setattr(resume, "free_bytes", lambda: 7 * 2**30)
    monkeypatch.setattr(resume.original, "sha", lambda _p: "old-sha")
    def load(folder):
        if folder.name == "short_only":
            return None, None, None
        return (
            {"next_day": 103},
            {"source_head_first": resume.ORIGINAL_HEAD, "index": 102},
            "source-checkpoint-sha",
        )
    with pytest.raises(RuntimeError, match="original final result"):
        resume.check_resume(SimpleNamespace(load_parts=load), "a" * 40)
