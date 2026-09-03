from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.mayak.research import event_truth
from bybit_workbench.mayak.research.event_truth import AnchorType, load_discovery_events

ROOT = Path(__file__).parents[1]
ANCHOR = "2026-05-18T00:00:00+00:00"
OUTCOME = "2026-05-18T01:00:00+00:00"


def _full_research_available() -> bool:
    required = []
    for symbol in event_truth.DISCOVERY_SYMBOLS:
        asset = (
            ROOT
            / "reports"
            / "cross_asset_validation"
            / f"{symbol}_{event_truth.PERIOD_TAG}"
            / "p40"
        )
        required.extend(
            [asset / "absorption_features.csv", asset / "summary.json"]
        )
    required.extend(
        [
            ROOT
            / "reports"
            / "early_protection_plus05_minus05_v1"
            / "ALL9_20260819_115647"
            / "event_results.csv",
            ROOT
            / "reports"
            / "portfolio_replay_v1"
            / "ALL9_20260819_205330"
            / "stop_clusters_15m.csv",
        ]
    )
    present = [path.is_file() for path in required]
    if not any(present):
        return False
    missing = [str(path) for path, exists in zip(required, present, strict=True) if not exists]
    if missing:
        raise FileNotFoundError(
            "partial frozen Mayak research inputs: " + "; ".join(missing)
        )
    for symbol in event_truth.DISCOVERY_SYMBOLS:
        asset = (
            ROOT
            / "reports"
            / "cross_asset_validation"
            / f"{symbol}_{event_truth.PERIOD_TAG}"
            / "p40"
        )
        summary = json.loads((asset / "summary.json").read_text(encoding="utf-8"))
        event_truth._resolve_dataset_manifest(ROOT, str(summary["dataset_dir"]))
    return True


FULL_RESEARCH_AVAILABLE = _full_research_available()


def _synthetic_research_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    monkeypatch.setattr(event_truth, "DISCOVERY_SYMBOLS", ("UNIUSDT",))
    monkeypatch.setattr(event_truth, "PERIOD_TAG", "SYNTHETIC")
    monkeypatch.setattr(event_truth, "EXPECTED_DISCOVERY_EVENTS", 1)

    asset = (
        tmp_path
        / "reports"
        / "cross_asset_validation"
        / "UNIUSDT_SYNTHETIC"
        / "p40"
    )
    asset.mkdir(parents=True)
    (asset / "absorption_features.csv").write_text(
        f"touch_at,direction\n{ANCHOR},Long\n",
        encoding="utf-8",
    )

    dataset_dir = tmp_path / "reports" / "synthetic_dataset"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "dataset_manifest.json").write_text(
        '{"fixture":"synthetic"}\n',
        encoding="utf-8",
    )
    (asset / "summary.json").write_text(
        json.dumps({"dataset_dir": str(dataset_dir)}) + "\n",
        encoding="utf-8",
    )

    outcome = (
        tmp_path
        / "reports"
        / "early_protection_plus05_minus05_v1"
        / "ALL9_20260819_115647"
    )
    outcome.mkdir(parents=True)
    (outcome / "event_results.csv").write_text(
        "symbol,touch_at,outcome,event_at\n"
        f"UNIUSDT,{ANCHOR},reached_1p10,{OUTCOME}\n",
        encoding="utf-8",
    )

    clusters = (
        tmp_path
        / "reports"
        / "portfolio_replay_v1"
        / "ALL9_20260819_205330"
    )
    clusters.mkdir(parents=True)
    (clusters / "stop_clusters_15m.csv").write_text(
        "policy_id,cluster_start,cluster_end\n",
        encoding="utf-8",
    )
    return tmp_path


def test_synthetic_event_truth_is_exact_and_causal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _synthetic_research_root(tmp_path, monkeypatch)
    events = load_discovery_events(root)

    assert len(events) == 1
    event = events[0]
    assert event.anchor_type is AnchorType.ENTRY_DECISION
    assert event.anchor_time == event.entry_time
    assert event.anchor_time == datetime.fromisoformat(ANCHOR).astimezone(UTC)
    assert event.outcome_time == datetime.fromisoformat(OUTCOME).astimezone(UTC)
    assert event.outcome_time >= event.anchor_time


def test_missing_frozen_research_input_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(event_truth, "DISCOVERY_SYMBOLS", ("UNIUSDT",))
    monkeypatch.setattr(event_truth, "PERIOD_TAG", "MISSING")
    monkeypatch.setattr(event_truth, "EXPECTED_DISCOVERY_EVENTS", 1)

    with pytest.raises(FileNotFoundError, match="absorption_features.csv"):
        load_discovery_events(tmp_path)


@pytest.mark.skipif(
    not FULL_RESEARCH_AVAILABLE,
    reason="frozen Mayak research inputs are local and intentionally not versioned",
)
def test_all9_event_truth_is_exact_and_causal() -> None:
    events = load_discovery_events(ROOT)
    assert len(events) == 1063
    assert all(event.anchor_type is AnchorType.ENTRY_DECISION for event in events)
    assert all(event.anchor_time == event.entry_time for event in events)
    assert all(event.anchor_time.tzinfo == UTC for event in events)
    assert min(event.entry_time for event in events) >= datetime(2026, 5, 18, tzinfo=UTC)


@pytest.mark.skipif(
    not FULL_RESEARCH_AVAILABLE,
    reason="frozen Mayak research inputs are local and intentionally not versioned",
)
def test_future_metadata_never_moves_feature_cutoff() -> None:
    event = load_discovery_events(ROOT)[0]
    assert event.anchor_time == event.entry_time
    assert event.outcome_time is None or event.outcome_time >= event.anchor_time
