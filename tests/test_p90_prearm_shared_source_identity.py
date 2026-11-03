from __future__ import annotations

from pathlib import Path

import pytest

from bybit_workbench import p90l90_real_prearm_evidence as prearm
from bybit_workbench.source_live_identity import SourceLiveIdentity

RUNTIME = "a" * 40
DASHBOARD = "b" * 40


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    state = tmp_path / "release-state"
    state.mkdir()
    (runtime / "INSTALLED_COMMIT").write_text(RUNTIME + "\n", encoding="utf-8")
    (state / "INSTALLED_COMMIT").write_text(RUNTIME + "\n", encoding="utf-8")
    return source, runtime, state


def test_p90_prearm_accepts_verified_dashboard_only_source_delta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, runtime, state = _roots(tmp_path)

    def shared_identity(**kwargs):
        assert kwargs["loaded_commit"] == RUNTIME
        assert kwargs["source"] == source
        assert kwargs["release_state"] == state
        return SourceLiveIdentity(
            ok=True,
            blocker=None,
            loaded_commit=RUNTIME,
            source_head=DASHBOARD,
            remote_head=DASHBOARD,
            dashboard_ui_commit=DASHBOARD,
            changed_paths=(
                "operations/dashboard/index.html",
                "tests/test_dashboard_p90_first_pass_351.py",
            ),
        )

    monkeypatch.setattr(prearm, "inspect_source_live_identity", shared_identity)

    evidence = prearm._release_identity_evidence(
        release_commit=RUNTIME,
        source_root=source,
        runtime_root=runtime,
        release_state_root=state,
    )

    assert evidence["release_commit"] == RUNTIME
    assert evidence["runtime_commit"] == RUNTIME
    assert evidence["state_commit"] == RUNTIME
    assert evidence["source_commit"] == DASHBOARD
    assert evidence["remote_main"] == DASHBOARD
    assert evidence["dashboard_ui_commit"] == DASHBOARD
    assert evidence["source_delta_paths"] == [
        "operations/dashboard/index.html",
        "tests/test_dashboard_p90_first_pass_351.py",
    ]


def test_p90_prearm_propagates_shared_classifier_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, runtime, state = _roots(tmp_path)
    monkeypatch.setattr(
        prearm,
        "inspect_source_live_identity",
        lambda **_kwargs: SourceLiveIdentity(
            ok=False,
            blocker="TRADING_OR_UNKNOWN_SOURCE_DELTA",
            loaded_commit=RUNTIME,
            source_head=DASHBOARD,
            remote_head=DASHBOARD,
            dashboard_ui_commit=None,
            changed_paths=("src/bybit_workbench/live_arm.py",),
        ),
    )

    with pytest.raises(
        prearm.P90PrearmEvidenceBlocked,
        match="TRADING_OR_UNKNOWN_SOURCE_DELTA",
    ):
        prearm._release_identity_evidence(
            release_commit=RUNTIME,
            source_root=source,
            runtime_root=runtime,
            release_state_root=state,
        )


def test_p90_prearm_keeps_runtime_and_release_state_identity_strict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, runtime, state = _roots(tmp_path)
    (state / "INSTALLED_COMMIT").write_text(DASHBOARD + "\n", encoding="utf-8")

    called = False

    def should_not_run(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("shared classifier must not run on runtime/state split")

    monkeypatch.setattr(prearm, "inspect_source_live_identity", should_not_run)

    with pytest.raises(
        prearm.P90PrearmEvidenceBlocked,
        match="runtime/state release identity mismatch",
    ):
        prearm._release_identity_evidence(
            release_commit=RUNTIME,
            source_root=source,
            runtime_root=runtime,
            release_state_root=state,
        )
    assert called is False


def test_p90_prearm_does_not_reintroduce_exact_source_equals_runtime_gate() -> None:
    source = Path(
        "src/bybit_workbench/p90l90_real_prearm_evidence.py"
    ).read_text(encoding="utf-8")
    assert "inspect_source_live_identity" in source
    assert "source_commit == remote == runtime_commit" not in source
    assert "remote/source/runtime/state release identity mismatch" not in source
