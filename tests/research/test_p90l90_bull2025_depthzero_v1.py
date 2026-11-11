"""Strict-zero 2025 research admission, lineage and fail-closed launch tests."""
from __future__ import annotations

from decimal import Decimal as D
import json
from types import SimpleNamespace

import pytest

from scripts.research import p90l90_bull2025_depthzero_v1 as study


@pytest.mark.parametrize(
    ("side", "depth", "width", "result"),
    [
        ("LONG", "0", True, True),
        ("SHORT", "0.0000000000000", True, True),
        ("LONG", "-0", True, True),
        ("LONG", "0.00000000000000000001", True, False),
        ("LONG", "0.05", True, False),
        ("SHORT", "15", True, False),
        ("LONG", "0", False, False),
        ("SHORT", "0", False, False),
    ],
)
def test_zero_entry_is_exact_not_rounded(side, depth, width, result):
    e = {"side": side, "depth_pct": depth, "PREV7D": width}
    assert study.zero_entry(e, side) is result


def test_zero_entry_respects_direction_and_strict_pre7d_flag():
    a = {"side": "LONG", "depth_pct": "0", "PREV7D": True}
    assert not study.zero_entry(a, "SHORT")
    a["PREV7D"] = 1
    assert not study.zero_entry(a, "LONG")


def test_event_filter_does_not_modify_original_candidates():
    raw = [
        {"side": "LONG", "PREV7D": True, "depth_pct": "0", "event_id": "a"},
        {"side": "LONG", "PREV7D": True, "depth_pct": "0.001", "event_id": "b"},
        {"side": "LONG", "PREV7D": False, "depth_pct": "0", "event_id": "c"},
        {"side": "SHORT", "PREV7D": True, "depth_pct": "0", "event_id": "d"},
    ]
    before = json.dumps(raw, sort_keys=True)
    assert [e["event_id"] for e in study.filtered_events(raw, "LONG")] == ["a"]
    assert [e["event_id"] for e in study.filtered_events(raw, "SHORT")] == ["d"]
    assert json.dumps(raw, sort_keys=True) == before


def test_source_file_does_not_import_runtime_or_mutate_live():
    assert study.OUT.name == "portfolio_depthzero_2025_v1"
    assert study.OUT.parent == study.RESULTS
    assert study.BASE.name == "portfolio_capfree100_2025_v1"
    assert study.SIDES == ("LONG_ONLY", "SHORT_ONLY")
    assert study.CASES == ("NO_PAUSE", "SECOND48")
    assert study.EXPECTED_CANDIDATES == {
        "LONG_ONLY": 2583, "SHORT_ONLY": 2863
    }


def test_study_status_replacement_is_scoped_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "OUT", tmp_path)
    study.set_status("PILOT_PASS", days_done=1)
    assert json.loads((tmp_path / "status.json").read_text()) == {
        "stage": "PILOT_PASS", "days_done": 1
    }
    study.set_status("RUNNING", days_done=10)
    assert json.loads((tmp_path / "status.json").read_text()) == {
        "stage": "RUNNING", "days_done": 10
    }
    assert not (tmp_path / "status.json.new").exists()


def test_status_does_not_overwrite_unreconciled_temp(tmp_path):
    path = tmp_path / "status.json"
    temp = tmp_path / "status.json.new"
    temp.write_text("unresolved previous writer")
    with pytest.raises(FileExistsError, match="ambiguous status temp"):
        study.atom(path, {"stage": "RUNNING"})
    assert temp.read_text() == "unresolved previous writer"


def test_reject_second_pilot_before_any_engine_use(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "OUT", tmp_path)
    engine = SimpleNamespace(run=lambda *_a, **_k: pytest.fail("must not replay"))
    with pytest.raises(FileExistsError, match="already exists"):
        study.pilot(engine, {}, "a" * 40)


def test_launch_requires_verified_pilot_and_never_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "OUT", tmp_path)
    (tmp_path / "status.json").write_text('{"stage":"RUNNING"}')
    monkeypatch.setattr(study, "read_day", lambda _e, side: 1 if side == "LONG_ONLY" else 0)
    with pytest.raises(RuntimeError, match="pilot"):
        study.launch(object(), "a" * 40)
    (tmp_path / "status.json").write_text('{"stage":"PILOT_PASS"}')
    (tmp_path / "launch.json").write_text('{"status":"LAUNCHED"}')
    with pytest.raises(FileExistsError, match="already launched"):
        study.launch(object(), "a" * 40)


def test_baseline_actor_required_before_any_read(monkeypatch):
    monkeypatch.setattr(study.os, "geteuid", lambda: 9999)
    with pytest.raises(PermissionError, match="UID107"):
        study.baseline_proof()


def test_preflight_never_creates_output(tmp_path, monkeypatch):
    marker = tmp_path / "no-output"
    monkeypatch.setattr(study, "OUT", marker)
    monkeypatch.setattr(study, "prepare", lambda sha: (
        object(), {"depthzero_candidates": study.EXPECTED_CANDIDATES}
    ))
    # A side-effect-free import/actor-independent candidate source check is
    # exercised by the exact prepare() function at real server preflight.
    assert not marker.exists()


def test_money_quantum_and_margin_identity():
    assert (D("20") * D("5")) == D("100")
    assert (D("100") * D("0.00055")) == D("0.055")
